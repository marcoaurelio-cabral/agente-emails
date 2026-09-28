"""
api.py — Backend HTTP (FastAPI)
================================

La puerta por la que entrará la app móvil. Es una capa FINA: cada endpoint
valida la entrada, llama a servicio.py o almacen.py, y devuelve JSON. Nada
de lógica de negocio aquí.

Endpoints:
    GET  /salud                      ¿está vivo?
    GET  /tareas                     pendientes, agrupadas, ordenadas por plazo
    POST /tareas/completar           {ids: [...]}  -> el botón "hecho"
    POST /tareas/reabrir             {ids: [...]}  -> deshacer (hecha o descartada)
    POST /tareas/descartar           {ids: [...]}  -> "no la voy a hacer"
    POST /emails/tarea               {ids, accion, fecha_limite?} -> importante -> tarea
    POST /emails/quitar-tarea        {ids: [...]}  -> deshacer
    GET  /tareas/completadas?dias=30 historial
    GET  /importantes?dias=7         informativos (sin acción) que aún no has visto
    POST /emails/visto               {ids: [...]}  -> "me he enterado"
    POST /emails/no-visto            {ids: [...]}  -> deshacer
    GET  /ruido?dias=7               la "papelera": para cazar falsos negativos
    POST /emails/{id}/corregir       {importante: bool} -> etiqueta de oro
    GET  /correcciones               exportar etiquetas de oro (para evals/ML)
    POST /revisar                    lanza la revisión en SEGUNDO PLANO
    GET  /revisar/estado             progreso / resultado de la última
    GET  /estadisticas

Documentación interactiva GRATIS en http://localhost:8000/docs — pruébalo
todo desde el navegador antes de que exista la app.

Arrancar (en la carpeta del proyecto, venv activo):
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload

--host 0.0.0.0 hace que sea accesible desde el móvil en tu misma WiFi:
    http://<IP-de-tu-PC>:8000/docs   (la IP sale con `ipconfig`)

SEGURIDAD: sin autenticación, a propósito, para desarrollo en tu WiFi.
Antes de sacarlo a internet hay que poner un token. Está apuntado.
"""

import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import almacen
import calendario
import gmail
import servicio


@asynccontextmanager
async def lifespan(app: FastAPI):
    almacen.inicializar()  # migraciones al arrancar, como en la CLI
    almacen.ALTERAR = calendario.sincronizar_async   # completar/descartar/corregir -> calendario
    gmail.INTERACTIVO = False  # el servidor va oculto: nunca debe abrir el navegador ni bloquearse
    yield


app = FastAPI(title="Agente de emails", version="0.1.0", lifespan=lifespan)

# La app móvil correrá en otro "origen": sin esto el navegador/Expo bloquea las
# peticiones. Abierto a todo porque estamos en la WiFi de casa.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Modelos de entrada (Pydantic valida el JSON que llega) ──────────────────

class Ids(BaseModel):
    ids: list[str] = Field(min_length=1)


class Correccion(BaseModel):
    importante: bool


class NuevaTarea(BaseModel):
    ids: list[str] = Field(min_length=1)
    accion: str = Field(min_length=1, max_length=200)
    fecha_limite: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class OpcionesRevision(BaseModel):
    consulta: str = "newer_than:7d"
    maximo: int = Field(default=150, ge=1, le=500)
    reclasificar_todo: bool = False


# ── Estado de la revisión en segundo plano ──────────────────────────────────
# Revisar tarda minutos (Gmail + LLM). Una petición HTTP no puede quedarse
# colgada tanto: se lanza en background y la app consulta el progreso.

_revision = {"en_curso": False, "progreso": "", "inicio": None, "fin": None,
             "resultado": None, "error": None}


def _ejecutar_revision(opts: OpcionesRevision):
    _revision.update(en_curso=True, progreso="iniciando", inicio=datetime.now().isoformat(),
                     fin=None, resultado=None, error=None)
    try:
        r = servicio.revisar(opts.consulta, opts.maximo, opts.reclasificar_todo,
                             progreso=lambda m: _revision.update(progreso=m))
        _revision.update(resultado=r, progreso="terminado")
    except Exception as e:  # el error se guarda, no se pierde en un hilo
        texto = str(e) if getattr(e, "necesita_autorizar", False) else f"{type(e).__name__}: {e}"
        _revision.update(error=texto, progreso="error")
    finally:
        _revision.update(en_curso=False, fin=datetime.now().isoformat())


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/salud")
def salud():
    return {"ok": True, "hora": datetime.now().isoformat()}


@app.get("/tareas")
def tareas():
    return {"tareas": almacen.tareas_pendientes()}


@app.post("/tareas/completar")
def completar(body: Ids):
    return {"completadas": almacen.marcar_completadas(body.ids)}


@app.post("/tareas/reabrir")
def reabrir(body: Ids):
    return {"reabiertas": almacen.reabrir(body.ids)}


@app.post("/tareas/descartar")
def descartar(body: Ids):
    """"No la voy a hacer": sale de pendientes sin contar como hecha."""
    return {"descartadas": almacen.descartar(body.ids)}


@app.post("/emails/tarea")
def convertir_en_tarea(body: NuevaTarea):
    """Convierte un importante en tarea (el modelo no podrá deshacerla)."""
    return {"convertidas": almacen.convertir_en_tarea(body.ids, body.accion.strip(), body.fecha_limite)}


@app.post("/emails/quitar-tarea")
def quitar_tarea(body: Ids):
    """Deshace convertir en tarea."""
    return {"restauradas": almacen.quitar_tarea(body.ids)}


@app.post("/emails/visto")
def visto(body: Ids):
    """"Me he enterado": sale de la lista de importantes sin cambiar su etiqueta."""
    return {"vistos": almacen.marcar_vistos(body.ids, True)}


@app.post("/emails/no-visto")
def no_visto(body: Ids):
    """Deshace "me he enterado"."""
    return {"restaurados": almacen.marcar_vistos(body.ids, False)}


@app.get("/tareas/completadas")
def completadas(dias: int = 30):
    return {"tareas": almacen.tareas_completadas(dias)}


@app.get("/importantes")
def importantes(dias: int = 7):
    return {"importantes": almacen.importantes_sin_accion(dias)}


@app.get("/ruido")
def ruido(dias: int = 7, limite: int = 200):
    return {"ruido": almacen.ruido_reciente(dias, limite)}


@app.post("/emails/{gmail_id}/corregir")
def corregir(gmail_id: str, body: Correccion):
    if not almacen.corregir(gmail_id, body.importante):
        raise HTTPException(status_code=404, detail="Email no encontrado")
    return {"ok": True, "gmail_id": gmail_id, "importante": body.importante}


@app.get("/correcciones")
def correcciones():
    return {"correcciones": almacen.correcciones()}


@app.post("/revisar", status_code=202)
def revisar(background: BackgroundTasks, opts: OpcionesRevision | None = None):
    if _revision["en_curso"]:
        raise HTTPException(status_code=409, detail="Ya hay una revisión en curso")
    background.add_task(_ejecutar_revision, opts or OpcionesRevision())
    return {"lanzada": True, "consulta_estado": "/revisar/estado"}


@app.get("/revisar/estado")
def estado_revision():
    return _revision


# ── Calendario de Apple ──────────────────────────────────────────────────────

@app.get("/calendario/estado")
def calendario_estado():
    return {"configurado": calendario.configurado(), **calendario.ESTADO}


@app.post("/calendario/sincronizar", status_code=202)
def calendario_sincronizar():
    if not calendario.configurado():
        raise HTTPException(status_code=409, detail="Calendario sin configurar (mira calendario.py)")
    calendario.sincronizar_async()
    return {"lanzada": True}


@app.get("/calendario.ics")
def calendario_ics():
    """Todo lo que iría al calendario, como archivo .ics (para importarlo a mano)."""
    return Response(calendario.a_feed(calendario.eventos_deseados()), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="agente-emails.ics"'})


# ── Autorizar Gmail desde la notificación de Windows ─────────────────────────
# La notificación "Gmail necesita autorización" abre /gmail/autorizar. El botón
# lanza autorizar_gmail.pyw (que abre el flujo de Google). Es POST y solo acepta
# peticiones del propio sitio: otra web abierta en tu navegador no puede
# dispararlo por su cuenta.

_PAGINA = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Autorizar Gmail</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#EEF1F8;color:#131F3A;display:grid;
place-items:center;min-height:100vh;margin:0}}main{{background:#fff;border-radius:16px;padding:32px;
max-width:420px;box-shadow:0 6px 24px rgba(27,43,94,.12)}}h1{{margin:0 0 8px;font-size:24px}}
button{{margin-top:16px;background:#2F5BEA;color:#fff;border:0;border-radius:999px;padding:12px 22px;
font:700 16px system-ui;cursor:pointer}}p{{color:#56627A}}</style>
<main><h1>{titulo}</h1>{cuerpo}</main></html>"""


def _pagina(titulo: str, cuerpo: str) -> HTMLResponse:
    return HTMLResponse(_PAGINA.format(titulo=titulo, cuerpo=cuerpo))


@app.get("/gmail/autorizar", response_class=HTMLResponse)
def pagina_autorizar():
    return _pagina("Autorizar Gmail", """
      <p>Tu permiso de acceso a Gmail ha caducado o se ha revocado, así que la revisión
      del correo está en pausa.</p>
      <p>Pulsa el botón: se abrirá la pantalla de Google. Elige tu cuenta, pulsa
      <b>Avanzado</b>, <b>Ir a agente-emails (no seguro)</b> y <b>Permitir</b>.</p>
      <form method="post" action="/gmail/autorizar"><button>Autorizar Gmail</button></form>""")


@app.post("/gmail/autorizar", response_class=HTMLResponse)
def lanzar_autorizacion(request: Request):
    origen = request.headers.get("origin")
    if origen and origen not in ("http://127.0.0.1:8000", "http://localhost:8000"):
        raise HTTPException(status_code=403, detail="Petición de otro sitio")
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")     # sin ventana de consola
    subprocess.Popen(
        [str(pythonw if pythonw.exists() else python), str(Path(__file__).parent / "autorizar_gmail.pyw")],
        cwd=Path(__file__).parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return _pagina("Abriendo Google…", """
      <p>Sigue los pasos en la ventana de Google que se acaba de abrir. Al terminar te saldrá
      un aviso confirmando la cuenta y ya puedes cerrar esta pestaña.</p>""")


@app.get("/estadisticas")
def estadisticas():
    return almacen.estadisticas()


# ── La app web (exportada con `npx expo export --platform web`) ─────────────
# Se monta AL FINAL: las rutas de la API (/tareas, /docs...) se definen antes
# y tienen prioridad; todo lo demás lo sirve la app. Así basta con un solo
# proceso (este servidor) para usar la app: sin Node ni Expo.
WEB = Path(__file__).parent / "app" / "dist"
if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


# ── La app compilada (npx expo export --platform web -> app/dist) ────────────
# Se monta la ÚLTIMA: FastAPI prueba antes todas las rutas de la API, y solo lo
# que no es API cae aquí. Así un único servidor sirve la app y la API en la
# misma dirección (http://localhost:8000), sin Expo ni terminales.
WEB = Path(__file__).parent / "app" / "dist"
if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
