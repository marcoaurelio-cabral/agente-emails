"""
calendario.py — Tareas, viajes y eventos en el Calendario de Apple (iCloud)
===========================================================================

El agente ESCRIBE en tu iCloud por CalDAV (no hace falta abrir tu servidor a
internet). Lo que crea aparece solo en el iPhone y el Mac.

Qué se sincroniza (todo en un calendario propio, "Agente de emails", para no
tocar nunca tus otros calendarios):
  - TAREAS pendientes con fecha límite  -> "⏰ Rellenar formulario de la beca",
    con avisos a las 9:00 del día anterior y del propio día.
  - VIAJES y EVENTOS con fecha           -> "✈️ Venecia", "📅 Hackathon". Los
    emails del mismo grupo (agrupar.py) son UN solo evento.
  Al marcar una tarea como hecha o descartada, su evento desaparece.

Reglas de seguridad de la sincronización:
  - Solo se toca lo que creó el agente (identificadores terminados en
    @agente-emails). Un evento que crees tú a mano en ese calendario, se respeta.
  - Los viajes y eventos PASADOS se conservan (es tu historial); solo se retira
    lo futuro que ha dejado de tener sentido (lo marcaste "no me importa"...).
  - Solo se envía lo que cambia (cada evento lleva una huella de su contenido).
  - Lo que cambies a mano en un evento del agente se sobrescribe en la próxima
    sincronización: la fuente de verdad es el agente.

Configuración en el .env (NUNCA en git, NUNCA en un chat):
    ICLOUD_USUARIO=tu_apple_id@icloud.com
    ICLOUD_PASSWORD_APP=xxxx-xxxx-xxxx-xxxx     (contraseña ESPECÍFICA de aplicación)
    CALENDARIO_NOMBRE=Agente de emails          (opcional)

Uso:
    python calendario.py --comprobar   comprueba la conexión (crea y borra un evento de prueba)
    python calendario.py               sincroniza ahora
    python calendario.py --ics         escribe agente-emails.ics (para importarlo a mano)
"""

import hashlib
import json
import os
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent
LOG = RAIZ / "calendario.log"
URL_CALDAV = "https://caldav.icloud.com/"
SUFIJO_UID = "@agente-emails"
DIAS_ATRAS = 30          # viajes/eventos de hasta hace 30 días siguen en el calendario
RETRASO_ASYNC = 2.0      # agrupa varias acciones seguidas en una sola sincronización

ESTADO = {"ultima": None, "resumen": None, "error": None}


class ErrorCalendario(Exception):
    pass


def nombre_calendario() -> str:
    return os.getenv("CALENDARIO_NOMBRE", "Agente de emails")


def configurado() -> bool:
    return bool(os.getenv("ICLOUD_USUARIO") and os.getenv("ICLOUD_PASSWORD_APP"))


# ── Qué eventos debería haber ────────────────────────────────────────────────

def _recortar(texto: str, n: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def _descripcion(fila: dict) -> str:
    partes = [p for p in (fila.get("asunto"), fila.get("resumen")) if p]
    partes.append("— Agente de emails")
    return "\n\n".join(partes)


def eventos_deseados(hoy: date | None = None) -> dict[str, dict]:
    """{uid: evento} con TODO lo que debería estar hoy en el calendario."""
    import almacen
    hoy = hoy or date.today()
    eventos: dict[str, dict] = {}

    for t in almacen.tareas_pendientes():
        try:
            fecha = date.fromisoformat(t["fecha_limite"])
        except (TypeError, ValueError):
            continue                                   # sin plazo (o plazo inválido): no va al calendario
        uid = f"tarea-{min(t['ids'])}{SUFIJO_UID}"     # min(ids) = el email más antiguo: estable
        eventos[uid] = dict(uid=uid, tipo="tarea", fecha=fecha, alarmas=fecha >= hoy,
                            resumen="⏰ " + _recortar(t.get("accion") or t.get("asunto") or "Tarea", 90),
                            descripcion=_descripcion(t))

    for e in almacen.eventos_para_calendario(DIAS_ATRAS):
        try:
            fecha = date.fromisoformat(e["fecha_entidad"])
        except (TypeError, ValueError):
            continue
        tipo = e["tipo_entidad"]
        uid = f"{tipo}-{e['clave']}{SUFIJO_UID}"
        icono = "✈️" if tipo == "viaje" else "📅"
        nombre = e.get("entidad") or e.get("asunto") or tipo.capitalize()
        eventos[uid] = dict(uid=uid, tipo=tipo, fecha=fecha, alarmas=fecha >= hoy,
                            resumen=f"{icono} {_recortar(nombre, 90)}", descripcion=_descripcion(e))
    return eventos


# ── iCalendar ────────────────────────────────────────────────────────────────

def huella(ev: dict) -> str:
    """Identifica el CONTENIDO del evento: si no cambia, no se vuelve a enviar."""
    datos = [ev["resumen"], ev["descripcion"], ev["fecha"].isoformat(), ev["alarmas"]]
    return hashlib.sha1(json.dumps(datos, ensure_ascii=False).encode()).hexdigest()[:16]


def _vevent(ev: dict):
    from icalendar import Alarm, Event
    e = Event()
    e.add("uid", ev["uid"])
    e.add("summary", ev["resumen"])
    e.add("description", ev["descripcion"])
    e.add("dtstart", ev["fecha"])                          # día completo
    e.add("dtend", ev["fecha"] + timedelta(days=1))
    e.add("dtstamp", datetime.now(timezone.utc))
    e.add("transp", "TRANSPARENT")                         # no te marca como "ocupado"
    e.add("categories", ["Tarea" if ev["tipo"] == "tarea" else ev["tipo"].capitalize()])
    e.add("x-agente-hash", huella(ev))
    if ev["alarmas"]:
        # En un evento de día completo el inicio es las 00:00: -15 h = 9:00 del día
        # anterior; +9 h = 9:00 del propio día.
        for desplazamiento in (timedelta(hours=-15), timedelta(hours=9)):
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", ev["resumen"])
            a.add("trigger", desplazamiento)
            e.add_component(a)
    return e


def _calendario(nombre: str | None = None):
    from icalendar import Calendar
    c = Calendar()
    c.add("prodid", "-//Agente de emails//ES")
    c.add("version", "2.0")
    if nombre:
        c.add("x-wr-calname", nombre)
    return c


def a_ics(ev: dict) -> str:
    """Un evento como su propio archivo .ics (lo que se envía a iCloud)."""
    c = _calendario()
    c.add_component(_vevent(ev))
    return c.to_ical().decode("utf-8")


def a_feed(eventos: dict[str, dict]) -> str:
    """Todos los eventos en un solo .ics (para importar a mano)."""
    c = _calendario(nombre_calendario())
    for ev in eventos.values():
        c.add_component(_vevent(ev))
    return c.to_ical().decode("utf-8")


# ── Destino: iCloud por CalDAV ───────────────────────────────────────────────

class DestinoICloud:
    def __init__(self):
        import caldav
        cliente = caldav.DAVClient(url=URL_CALDAV, username=os.environ["ICLOUD_USUARIO"],
                                   password=os.environ["ICLOUD_PASSWORD_APP"])
        principal = cliente.principal()
        nombre = nombre_calendario()
        self.cal = next((c for c in principal.calendars() if c.get_display_name() == nombre), None)
        if self.cal is None:
            try:
                self.cal = principal.make_calendar(name=nombre, supported_calendar_component_set=["VEVENT"])
            except Exception as e:
                raise ErrorCalendario(
                    f"No existe el calendario «{nombre}» y no he podido crearlo ({e}). "
                    "Créalo tú (Calendario de Apple, o icloud.com > Calendario > +) y vuelve a probar.")
        self._objetos: dict = {}

    def existentes(self) -> dict[str, dict]:
        from icalendar import Calendar
        res, self._objetos = {}, {}
        for obj in self.cal.events():
            try:
                contenido = Calendar.from_ical(obj.data)
            except Exception:
                continue
            for comp in contenido.walk("VEVENT"):
                uid = str(comp.get("UID", ""))
                if not uid:
                    continue
                inicio = comp.get("DTSTART")
                fecha = inicio.dt if inicio is not None else None
                res[uid] = {"hash": str(comp.get("X-AGENTE-HASH", "")),
                            "fecha": fecha.date() if isinstance(fecha, datetime) else fecha}
                self._objetos[uid] = obj
        return res

    def guardar(self, uid: str, ics: str):
        self.cal.save_event(ics)

    def borrar(self, uid: str):
        self._objetos[uid].delete()


# ── Sincronización ───────────────────────────────────────────────────────────

def sincronizar(destino=None, hoy: date | None = None) -> dict:
    """Deja el calendario como debe estar. Idempotente: si nada ha cambiado, no envía nada."""
    hoy = hoy or date.today()
    destino = destino or DestinoICloud()
    deseados = eventos_deseados(hoy)
    existentes = destino.existentes()
    creados = actualizados = borrados = 0

    for uid, ev in deseados.items():
        previo = existentes.get(uid)
        if previo is None:
            destino.guardar(uid, a_ics(ev)); creados += 1
        elif previo["hash"] != huella(ev):
            destino.guardar(uid, a_ics(ev)); actualizados += 1

    for uid, previo in existentes.items():
        if uid in deseados or not uid.endswith(SUFIJO_UID):
            continue                                   # lo deseado se queda; lo ajeno no se toca
        futuro = previo["fecha"] is None or previo["fecha"] >= hoy
        if uid.startswith("tarea-") or futuro:         # tareas hechas/descartadas: fuera; viajes pasados: se quedan
            destino.borrar(uid); borrados += 1

    resumen = {"creados": creados, "actualizados": actualizados, "borrados": borrados,
               "total": len(deseados)}
    ESTADO.update(ultima=datetime.now().isoformat(timespec="seconds"), resumen=resumen, error=None)
    return resumen


_cerrojo = threading.Lock()
_en_curso = False
_pendiente = False


def sincronizar_async():
    """Sincroniza en segundo plano sin bloquear a quien lo pide (la API). Varias
    peticiones seguidas se agrupan en una sola. Si iCloud no está configurado, no hace nada."""
    global _en_curso, _pendiente
    if not configurado():
        return
    with _cerrojo:
        if _en_curso:
            _pendiente = True
            return
        _en_curso = True
    threading.Thread(target=_bucle, daemon=True).start()


def _bucle():
    global _en_curso, _pendiente
    while True:
        time.sleep(RETRASO_ASYNC)
        try:
            sincronizar()
        except Exception as e:
            ESTADO["error"] = f"{type(e).__name__}: {e}"
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}\n")
        with _cerrojo:
            if _pendiente:
                _pendiente = False
                continue
            _en_curso = False
            return


# ── Línea de comandos ────────────────────────────────────────────────────────

AYUDA_CONFIG = """El calendario no está configurado. Añade al .env:

    ICLOUD_USUARIO=tu_apple_id@icloud.com
    ICLOUD_PASSWORD_APP=xxxx-xxxx-xxxx-xxxx

La contraseña NO es la de tu Apple ID: es una contraseña específica de aplicación.
Créala en account.apple.com > Inicio de sesión y seguridad > Contraseñas específicas
de la app (necesita la verificación en dos pasos). Se puede revocar allí cuando quieras."""


def comprobar():
    if not configurado():
        print(AYUDA_CONFIG); return
    try:
        destino = DestinoICloud()
        print(f"✓ Sesión iniciada y calendario «{nombre_calendario()}» localizado.")
        prueba = dict(uid=f"prueba-conexion{SUFIJO_UID}", tipo="evento", fecha=date.today(), alarmas=False,
                      resumen="Prueba del Agente de emails", descripcion="Puedes borrar este evento.")
        destino.guardar(prueba["uid"], a_ics(prueba))
        if prueba["uid"] not in destino.existentes():
            print("✗ El evento de prueba se envió pero no se vuelve a leer. Espera unos segundos y repite.")
            return
        print("✓ Evento de prueba creado y leído.")
        destino.borrar(prueba["uid"])
        print("✓ Evento de prueba borrado. Todo listo: ya puedes ejecutar  python calendario.py")
    except Exception as e:
        print(f"✗ No ha funcionado: {type(e).__name__}: {e}")
        print("  Si es un error de autenticación (401), revisa el usuario y que uses una contraseña "
              "ESPECÍFICA de aplicación, no la de tu Apple ID.")


def main():
    import almacen
    almacen.inicializar()
    if "--comprobar" in sys.argv:
        comprobar()
    elif "--ics" in sys.argv:
        eventos = eventos_deseados()
        ruta = RAIZ / "agente-emails.ics"
        ruta.write_text(a_feed(eventos), encoding="utf-8")
        print(f"{len(eventos)} eventos escritos en {ruta.name}. Ábrelo para importarlos al Calendario de Apple "
              "(no se mantendrá sincronizado; para eso, configura iCloud).")
    elif not configurado():
        print(AYUDA_CONFIG)
    else:
        r = sincronizar()
        print(f"Calendario «{nombre_calendario()}»: {r['creados']} nuevos · {r['actualizados']} actualizados · "
              f"{r['borrados']} borrados · {r['total']} eventos en total.")


if __name__ == "__main__":
    main()
