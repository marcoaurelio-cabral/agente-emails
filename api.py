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
    POST /tareas/reabrir             {ids: [...]}  -> deshacer
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

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import almacen
import servicio


@asynccontextmanager
async def lifespan(app: FastAPI):
    almacen.inicializar()  # migraciones al arrancar, como en la CLI
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
        _revision.update(error=f"{type(e).__name__}: {e}", progreso="error")
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


@app.get("/estadisticas")
def estadisticas():
    return almacen.estadisticas()
