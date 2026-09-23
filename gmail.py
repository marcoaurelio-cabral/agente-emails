"""
gmail.py — Fuente de emails (Gmail API, SOLO LECTURA) — v2
==========================================================

Novedades respecto a la v1:

  - PAGINACIÓN: Gmail devuelve los resultados por páginas. Para traer 150
    (o 500) emails hay que ir pidiendo "la siguiente página" con
    nextPageToken hasta tener suficientes.

  - LECTURA EN DOS FASES:
        listar_ids()      -> solo los ids (una llamada por cada 100). Barato.
        obtener_por_ids() -> el contenido completo, SOLO de los que pidas.
    ¿Por qué? Para poder comprobar contra la BD cuáles ya están
    clasificados ANTES de descargar su contenido. Si de 150 ya conoces 130,
    solo descargas 20. Ahorra tiempo y llamadas a la API.

  - fecha_epoch: la fecha real de llegada en segundos Unix (Gmail la da en
    internalDate). Necesaria para que la BD pueda filtrar "última semana".

Todo lo demás (OAuth, credentials.json, token.json, solo lectura) igual que
antes. Recuerda: NI credentials.json NI token.json van al repo.

Requisitos:
  pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
Prueba rápida (sin gastar LLM):  python gmail.py
"""

import base64
import os
import os.path

from dotenv import load_dotenv
load_dotenv()
import random
import re
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENCIALES = "credentials.json"
TOKEN = "token.json"
MAX_CARACTERES = 12000  # tope de SEGURIDAD (emails patológicos). El recorte para
                        # abaratar se decide en el orquestador (lectura adaptativa).

# Pausa mínima entre llamadas a Gmail. Cortesía con la API y evita ráfagas.
PAUSA_ENTRE_PETICIONES = 0.1

_servicio = None  # cliente de la API, se crea una vez y se reutiliza


def _ejecutar_con_reintentos(peticion, intentos: int = 6):
    """Ejecuta una petición a la API con EXPONENTIAL BACKOFF.

    Si Google responde "frena" (403/429 por límite de peticiones), esperamos
    1s, 2s, 4s, 8s... (más un poco de azar para no sincronizarnos con otros)
    y reintentamos. Cualquier otro error se propaga tal cual.

    Es el patrón estándar para hablar con cualquier API externa: la red y
    los límites de cuota son parte de la vida, y un sistema serio no se cae
    por ellos, espera y reintenta.
    """
    for intento in range(intentos):
        try:
            resultado = peticion.execute()
            time.sleep(PAUSA_ENTRE_PETICIONES)
            return resultado
        except HttpError as e:
            razon = str(e)
            es_limite = e.resp.status in (403, 429) and "rateLimit" in razon
            if not es_limite or intento == intentos - 1:
                raise
            espera = 2 ** intento + random.random()
            print(f"  [Gmail] límite de peticiones alcanzado; esperando {espera:.0f}s "
                  f"(intento {intento + 1}/{intentos})...")
            time.sleep(espera)


def _autorizar(motivo: str) -> Credentials:
    """Abre el navegador para que autorices el acceso a Gmail (solo lectura)."""
    print(f"\n  [Gmail] {motivo}")
    print("          Se abrirá el navegador: elige tu cuenta -> Avanzado ->")
    print("          Ir a agente-emails (no seguro) -> Permitir.\n")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENCIALES, SCOPES)
    return flow.run_local_server(port=0)


def _credenciales() -> Credentials:
    """Devuelve credenciales válidas, pidiendo autorización SOLO si hace falta.

    Si el permiso ha caducado (7 días con la app en modo prueba) o se ha
    revocado (cambio de contraseña, quitar el acceso desde tu cuenta de
    Google, 6 meses sin uso), se detecta y se vuelve a autorizar solo: ya no
    hay que borrar token.json a mano.

    Refresco PREVENTIVO al arrancar: se renueva aunque el token aún parezca
    válido. Así, si el permiso ha caducado, te enteras AQUÍ, antes de empezar,
    y no a mitad de 150 emails. Y el token recién renovado dura una hora,
    más que cualquier ejecución.
    """
    creds = None
    if os.path.exists(TOKEN):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
        except ValueError:
            creds = None  # token.json corrupto o incompleto: se autoriza de nuevo

    if creds is None or not creds.refresh_token:
        creds = _autorizar("Primera autorización de acceso a tu Gmail.")
    else:
        try:
            creds.refresh(Request())
        except RefreshError:
            os.remove(TOKEN)
            creds = _autorizar("El permiso de acceso a Gmail ha caducado o se ha revocado. "
                               "Hay que autorizar de nuevo.")

    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    return creds


def _obtener_servicio():
    global _servicio
    if _servicio is None:
        _servicio = build("gmail", "v1", credentials=_credenciales())
    return _servicio

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENCIALES, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())

    _servicio = build("gmail", "v1", credentials=creds)
    return _servicio


# ── Parseo del cuerpo (igual que v1) ────────────────────────────────────────

def _decodificar(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _hojas(payload: dict):
    if payload.get("parts"):
        for parte in payload["parts"]:
            yield from _hojas(parte)
    else:
        yield payload


def _extraer_cuerpo(payload: dict) -> str:
    hojas = list(_hojas(payload))
    for h in hojas:
        if h.get("mimeType") == "text/plain" and h.get("body", {}).get("data"):
            return _decodificar(h["body"]["data"])
    for h in hojas:
        if h.get("mimeType") == "text/html" and h.get("body", {}).get("data"):
            return re.sub(r"<[^>]+>", " ", _decodificar(h["body"]["data"]))
    return ""


# ── FASE 1: solo ids ────────────────────────────────────────────────────────

def listar_ids(consulta: str = "newer_than:7d", max_resultados: int = 150) -> list[str]:
    """Devuelve hasta `max_resultados` ids de Gmail que cumplen la consulta,
    del más reciente al más antiguo, paginando lo que haga falta."""
    service = _obtener_servicio()
    ids, token = [], None
    while len(ids) < max_resultados:
        respuesta = _ejecutar_con_reintentos(
            service.users().messages().list(
                userId="me",
                q=consulta,
                maxResults=min(100, max_resultados - len(ids)),
                pageToken=token,
            )
        )
        ids.extend(m["id"] for m in respuesta.get("messages", []))
        token = respuesta.get("nextPageToken")
        if not token:
            break  # no hay más páginas
    return ids


# ── FASE 2: contenido completo de los ids que pidas ─────────────────────────

def iterar_por_ids(ids: list[str]):
    """GENERADOR: va devolviendo los emails de UNO EN UNO según los descarga.

    Esto es lo que permite el flujo "descargar → clasificar → guardar" por
    email, en vez de "descargar los 150 → luego clasificar". Ventajas:
      - las llamadas a Gmail se reparten en el tiempo (entre email y email
        hay una llamada al LLM), así que no hay ráfagas que disparen el límite;
      - si algo se corta en el email 87, los 86 anteriores ya están guardados.
    """
    service = _obtener_servicio()
    for id_ in ids:
        msg = _ejecutar_con_reintentos(
            service.users().messages().get(userId="me", id=id_, format="full")
        )
        cabeceras = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        cuerpo = _extraer_cuerpo(msg["payload"]) or msg.get("snippet", "")
        cuerpo = re.sub(r"\s+", " ", cuerpo).strip()[:MAX_CARACTERES]
        yield {
            "id": id_,
            "remitente": cabeceras.get("from", "(desconocido)"),
            "asunto": cabeceras.get("subject", "(sin asunto)"),
            "fecha": cabeceras.get("date", ""),
            "fecha_epoch": int(msg.get("internalDate", 0)) // 1000,  # ms -> s
            "para": cabeceras.get("to", ""),
            "cc": cabeceras.get("cc", ""),
            "cuerpo": cuerpo,
        }


_mi_direccion = None


def mi_direccion() -> str:
    """Tu dirección de Gmail (se pregunta una vez a la API)."""
    global _mi_direccion
    if _mi_direccion is None:
        perfil = _ejecutar_con_reintentos(_obtener_servicio().users().getProfile(userId="me"))
        _mi_direccion = perfil["emailAddress"].lower()
    return _mi_direccion


def mis_direcciones() -> set[str]:
    """Tu Gmail + las direcciones extra de MIS_DIRECCIONES en el .env
    (separadas por comas; p. ej. tu correo de la universidad si se reenvía a Gmail)."""
    extra = {d.strip().lower() for d in os.getenv("MIS_DIRECCIONES", "").split(",") if d.strip()}
    return {mi_direccion()} | extra


def rol_de(e: dict) -> str:
    """'para' | 'copia' | 'no figura': dónde apareces en este email."""
    yo = mis_direcciones()
    cc, para = (e.get("cc") or "").lower(), (e.get("para") or "").lower()
    if any(d in cc for d in yo):
        return "copia"
    if any(d in para for d in yo):
        return "para"
    return "no figura"


def obtener_por_ids(ids: list[str]) -> list[dict]:
    """Atajo: la lista completa de golpe (para pruebas o lotes pequeños)."""
    return list(iterar_por_ids(ids))


def obtener_emails(consulta: str = "newer_than:7d", max_resultados: int = 150) -> list[dict]:
    """Atajo: las dos fases seguidas (cuando no necesitas filtrar por medio)."""
    return obtener_por_ids(listar_ids(consulta, max_resultados))


if __name__ == "__main__":
    print("Conectando con Gmail...")
    ids = listar_ids("newer_than:7d", max_resultados=150)
    print(f"{len(ids)} emails en la última semana (solo ids, sin descargar contenido).")
    for e in obtener_por_ids(ids[:5]):
        print(f"  · {e['asunto'][:60]:<60}  ({e['remitente'][:30]})")
    print("\nConexión OK.")
