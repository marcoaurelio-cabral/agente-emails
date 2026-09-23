"""
servicio.py — Capa de aplicación (los casos de uso)
====================================================

Aquí vive la lógica de "qué hace el sistema", sin saber QUIÉN la invoca:
    revisar_bandeja.py  (CLI)  ->  servicio  ->  gmail / cerebro / almacen
    api.py              (HTTP) ->  servicio  ->  gmail / cerebro / almacen
    (mañana la app móvil llama a api.py, que llama aquí)

Por eso no hay ni un print ni nada de FastAPI: solo funciones que reciben
parámetros y devuelven datos. Si mañana cambias cómo se revisa la bandeja,
lo cambias UNA vez y lo heredan todas las interfaces.

`progreso` es un callback opcional (p.ej. print, o actualizar un estado en
la API) para informar de avance en operaciones largas sin acoplarse a nadie.
"""

from datetime import date
from typing import Callable

from pathlib import Path

import agrupar
import almacen
import cerebro
import gmail
import jev
import politica
from cerebro import Clasificacion, clasificar

# LECTURA ADAPTATIVA: clasificar con los primeros N caracteres (barato); solo
# si resulta importante y el email era más largo, releer entero para que
# resumen, acción y plazo salgan de todo el contenido.
LECTURA_RAPIDA = 1500

# La POLÍTICA del prefiltro (umbral, regla, categorías descartables) vive en
# politica.py, compartida con calibrar_jev.py y sombra_jev.py: lo que se mide
# es exactamente lo que se ejecuta.

Progreso = Callable[[str], None] | None


ARCHIVO_CONTACTOS = Path(__file__).parent / "contactos_importantes.txt"


def _cargar_contactos() -> frozenset[str]:
    if not ARCHIVO_CONTACTOS.exists():
        return frozenset()
    lineas = ARCHIVO_CONTACTOS.read_text(encoding="utf-8").splitlines()
    return frozenset(l.strip().lower() for l in lineas if l.strip() and not l.strip().startswith("#"))


CONTACTOS_IMPORTANTES = _cargar_contactos()


def contacto_importante(e: dict) -> str | None:
    """¿Aparece un contacto importante en remitente, Para o CC? Devuelve cuál."""
    texto = " ".join((e.get(k) or "") for k in ("remitente", "para", "cc")).lower()
    for c in CONTACTOS_IMPORTANTES:
        if c in texto:
            return c
    return None


def clasificar_llm(e: dict, hoy: date | None = None, rol: str = "") -> Clasificacion:
    """Etapa LLM con LECTURA ADAPTATIVA: primero los primeros LECTURA_RAPIDA
    caracteres; si resulta importante y el email era más largo, se relee entero.
    Es la MISMA función que usan producción y evals.py: lo que se mide es lo
    que se ejecuta."""
    hoy = hoy or date.today()
    fecha_email = date.fromtimestamp(e["fecha_epoch"])
    cuerpo = e["cuerpo"]
    kw = dict(fecha_email=fecha_email, hoy=hoy, rol=rol)
    c = clasificar(e["remitente"], e["asunto"], cuerpo[:LECTURA_RAPIDA], **kw)
    if c.importante and len(cuerpo) > LECTURA_RAPIDA:
        c = clasificar(e["remitente"], e["asunto"], cuerpo, **kw)
    # Regla determinista: un contacto importante manda sobre el modelo.
    quien = contacto_importante(e)
    if quien and not c.importante:
        c.importante = True
        if c.categoria == "ruido":
            c.categoria = "personal"
        c.motivo = f"contacto importante ({quien}); el modelo dijo: {c.motivo}"
    return c


def clasificar_email(e: dict, hoy: date | None = None, rol: str = "") -> tuple:
    """Clasifica un email en CASCADA. Devuelve (clasificacion, prefiltro).

    Etapa 1 (Jev): decide si merece el modelo caro. Si p(importante) está
                   por debajo del umbral -> ruido y se acabó.
    Etapa 2 (LLM): para todo lo demás, con lectura adaptativa. Es el único
                   que sabe redactar el resumen y extraer acción y plazo.
    """
    hoy = hoy or date.today()
    fecha_email = date.fromtimestamp(e["fecha_epoch"])
    cuerpo = e["cuerpo"]

    pre = None
    if politica.USAR_PREFILTRO:
        pre = jev.evaluar(e["remitente"], e["asunto"], cuerpo)
        if pre and politica.descartar(pre["noul"], pre["categoria"], remitente=e["remitente"]):
            pre["decidido_por"] = "jev"
            return Clasificacion(
                importante=False, categoria="ruido", prioridad="baja",
                motivo=f"prefiltro Jev: {pre['categoria']} (p={pre['noul']:.3f}, {politica.REGLA})",
                resumen="", requiere_accion=False, accion="", fecha_limite="",
            ), pre

    c = clasificar_llm(e, hoy, rol)
    if pre:
        pre["decidido_por"] = "llm"
    return c, pre


def reclasificar(ids: list[str], progreso: Progreso = None) -> list[dict]:
    """Vuelve a pasar emails ya guardados por el cerebro. Devuelve los cambios
    (importante<->ruido, tarea<->informativo) para auditar el efecto."""
    flips = []
    total = len(ids)
    fechas = {k: v["fecha_epoch"] for k, v in almacen.metadatos(ids).items()}
    ids = sorted(ids, key=lambda i: fechas.get(i, 0))
    for i, e in enumerate(gmail.iterar_por_ids(ids), 1):
        c, _ = clasificar_email(e, rol=gmail.rol_de(e))
        for campo, (antes, ahora) in almacen.actualizar_clasificacion(e["id"], c).items():
            flips.append({"campo": campo, "antes": antes, "ahora": ahora, "asunto": e["asunto"]})
        if progreso and (i % 10 == 0 or i == total):
            progreso(f"re-clasificados {i}/{total}")
    return flips


def reagrupar_y_cerrar() -> list[dict]:
    """Recalcula los grupos de los emails recientes y cierra las tareas cuyo
    grupo ha recibido después un email que confirma que están hechas."""
    filas = almacen.filas_para_agrupar()
    grupos = agrupar.agrupar(filas)
    almacen.guardar_grupos(grupos)
    cerradas = []
    for tarea, por in agrupar.tareas_a_cerrar(filas, grupos):
        if almacen.cerrar_auto([tarea], por):
            cerradas.append({"tarea": tarea, "cerrada_por": por})
    return cerradas


def revisar(consulta: str = "newer_than:7d", maximo: int = 150,
            reclasificar_todo: bool = False, progreso: Progreso = None) -> dict:
    """El caso de uso principal: pone la bandeja al día.

    1. Migra/re-clasifica lo que toque.
    2. Pide ids a Gmail, descarta los ya clasificados.
    3. Clasifica y guarda los nuevos, en streaming.
    Devuelve un resumen serializable (la CLI lo imprime, la API lo devuelve).
    """
    hoy = date.today()
    almacen.inicializar()
    uso_antes = dict(cerebro._llm.uso)

    pendientes = almacen.ids_importantes() if reclasificar_todo else almacen.ids_importantes_sin_migrar()
    cambios = reclasificar(pendientes, progreso) if pendientes else []

    ids = gmail.listar_ids(consulta, maximo)
    conocidos = almacen.ids_conocidos(ids)
    nuevos_ids = [i for i in ids if i not in conocidos]

    # Del MÁS ANTIGUO al más reciente (Gmail los da al revés): un email solo
    # puede ser "repetido" de algo anterior, y una tarea solo la cierra un
    # email que llega después de ella.
    nuevos_ids = list(reversed(nuevos_ids))
    nuevos_importantes, nuevos_ruido, cerradas = [], [], []
    filtrados_por_jev = 0
    total = len(nuevos_ids)
    for i, e in enumerate(gmail.iterar_por_ids(nuevos_ids), 1):
        c, pre = clasificar_email(e, hoy, gmail.rol_de(e))
        if pre and pre.get("decidido_por") == "jev":
            filtrados_por_jev += 1
        almacen.guardar(e, c, pre)
        destino = nuevos_importantes if c.importante else nuevos_ruido
        destino.append({"id": e["id"], "asunto": e["asunto"], "remitente": e["remitente"],
                        "categoria": c.categoria, "prioridad": c.prioridad,
                        "requiere_accion": c.requiere_accion, "motivo": c.motivo})
        if progreso and (i % 10 == 0 or i == total):
            progreso(f"clasificados {i}/{total}")

    # Agrupación y cierre de tareas: código, sin IA, sobre las claves guardadas.
    cerradas = reagrupar_y_cerrar()

    uso = cerebro._llm.uso
    return {
        "consulta": consulta,
        "en_gmail": len(ids),
        "ya_clasificados": len(conocidos),
        "nuevos": total,
        "nuevos_importantes": nuevos_importantes,
        "nuevos_ruido": nuevos_ruido,
        "cambios_reclasificacion": cambios,
        "llamadas_llm": uso["llamadas"] - uso_antes["llamadas"],
        "tokens_llm": (uso["entrada"] + uso["salida"]) - (uso_antes["entrada"] + uso_antes["salida"]),
        "modelo": cerebro._llm.model,
        "tareas_cerradas_auto": cerradas,
        "prefiltro_activo": politica.USAR_PREFILTRO and jev.disponible(),
        "politica_prefiltro": politica.describir(),
        "filtrados_por_jev": filtrados_por_jev,
        "errores_jev": jev.uso["errores"],
        "jev_cortocircuito": jev.cortocircuito_abierto(),
        "uso_jev": jev.resumen_uso(),
    }
