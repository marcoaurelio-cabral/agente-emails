"""
revision_diaria.pyw — Revisa el correo una vez al día y avisa en Windows
=========================================================================

Lo lanza el Programador de tareas de Windows (cada día a la hora elegida) y
también al iniciar sesión, por si el PC estaba apagado a esa hora. Solo
revisa UNA vez al día: la segunda llamada del día termina sin hacer nada.

  1. Revisa Gmail (lo mismo que el botón "Revisar bandeja").
  2. Si hay algo que merezca tu atención, te lo dice en una notificación de
     Windows; al pulsarla se abre la app. Si no hay nada nuevo, no molesta.
  3. Si la revisión falla, también avisa: nunca creas que estás al día sin estarlo.

Probar a mano (con consola, para ver los mensajes):
    python revision_diaria.pyw --forzar
"""

import os
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
os.chdir(RAIZ)                       # token.json, credentials.json, .env... se buscan aquí
sys.path.insert(0, str(RAIZ))

import lanzador  # noqa: E402

MARCA = RAIZ / ".ultima_revision"
AVISO_AUTH = RAIZ / ".aviso_autorizacion"   # para avisar de esto UNA vez al día
LOG = RAIZ / "revision.log"


def log(texto: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {texto}\n")


def notificar(titulo: str, texto: str, url: str | None = None, boton: str = "Abrir"):
    """Notificación de Windows; al pulsarla se abre `url` (por defecto, la app).
    IMPORTANTE: quien llame debe asegurarse de que el servidor está funcionando
    antes de mostrarla, o al pulsar el navegador dirá "conexión rechazada"."""
    url = url or lanzador.URL
    try:
        from winotify import Notification, audio
        icono = RAIZ / "app" / "assets" / "icon.png"
        n = Notification(app_id="Agente de emails", title=titulo, msg=texto,
                         icon=str(icono) if icono.exists() else "", duration="long",
                         launch=url)
        n.add_actions(label=boton, launch=url)
        n.set_audio(audio.Default, loop=False)
        n.show()
    except ImportError:
        lanzador.mensaje(f"{titulo}\n\n{texto}\n\n(Instala winotify para recibir notificaciones:\n"
                         "python -m pip install winotify)", error=False)


def resumen(r: dict, pendientes: list[dict]) -> tuple[str, str] | None:
    """(título, texto) de la notificación, o None si no hay nada que contar."""
    hoy, manana = date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat()
    nuevas_tareas = [x for x in r["nuevos_importantes"] if x.get("requiere_accion")]
    nuevos_avisos = [x for x in r["nuevos_importantes"] if not x.get("requiere_accion")]
    urgentes = [t for t in pendientes if t.get("fecha_limite") and t["fecha_limite"] <= manana]
    cerradas = r.get("tareas_cerradas_auto") or []

    partes = []
    if nuevas_tareas:
        partes.append(f"{len(nuevas_tareas)} {'tarea nueva' if len(nuevas_tareas) == 1 else 'tareas nuevas'}")
    if nuevos_avisos:
        partes.append(f"{len(nuevos_avisos)} {'aviso importante' if len(nuevos_avisos) == 1 else 'avisos importantes'}")
    if cerradas:
        partes.append(f"{len(cerradas)} {'tarea cerrada sola' if len(cerradas) == 1 else 'tareas cerradas solas'}")
    if not partes and not urgentes:
        return None

    lineas = []
    if partes:
        lineas.append((", ".join(partes[:-1]) + " y " + partes[-1] if len(partes) > 1 else partes[0]) + ".")
    for t in urgentes[:2]:
        cuando = "Vencida" if t["fecha_limite"] < hoy else ("Vence hoy" if t["fecha_limite"] == hoy else "Vence mañana")
        lineas.append(f"{cuando}: {t.get('accion') or t.get('asunto')}")
    if len(urgentes) > 2:
        lineas.append(f"Y {len(urgentes) - 2} más con plazo inmediato.")

    total = len(r["nuevos_importantes"])
    titulo = f"{total} {'novedad' if total == 1 else 'novedades'} en tu correo" if total else "Tienes tareas con plazo"
    return titulo, "\n".join(lineas)


def main():
    forzar = "--forzar" in sys.argv
    hoy = date.today().isoformat()
    if not forzar and MARCA.exists() and MARCA.read_text(encoding="utf-8").strip() == hoy:
        return  # ya se revisó hoy

    try:
        import almacen
        import gmail
        import servicio
        gmail.INTERACTIVO = False   # desatendido: si hace falta autorizar, avisa; no abre nada
        r = servicio.revisar()
        pendientes = almacen.tareas_pendientes()
    except Exception as e:
        if getattr(e, "necesita_autorizar", False):
            log("AUTORIZACIÓN NECESARIA: " + str(e))
            if not forzar and AVISO_AUTH.exists() and AVISO_AUTH.read_text(encoding="utf-8").strip() == hoy:
                return  # ya se avisó hoy; no repetirlo en cada inicio de sesión
            AVISO_AUTH.write_text(hoy, encoding="utf-8")
            lanzador.asegurar_servidor()   # si no, al pulsar el aviso no habría nada que abrir
            notificar("Gmail necesita que vuelvas a autorizar el acceso",
                      "Tu permiso ha caducado o se ha revocado, así que hoy no se ha revisado el correo.\n"
                      "Pulsa este aviso para autorizarlo de nuevo (20 segundos).",
                      url=lanzador.URL + "/gmail/autorizar", boton="Autorizar")
            return
        log("ERROR\n" + traceback.format_exc())
        lanzador.asegurar_servidor()
        notificar("La revisión diaria ha fallado",
                  f"{type(e).__name__}: {str(e)[:120]}\nAbre la app y pulsa Revisar bandeja; detalles en revision.log")
        return

    MARCA.write_text(hoy, encoding="utf-8")
    log(f"OK · {r['nuevos']} nuevos · {len(r['nuevos_importantes'])} importantes · "
        f"{r.get('filtrados_por_jev', 0)} descartados por Jev · {r['llamadas_llm']} llamadas LLM")

    aviso = resumen(r, pendientes)
    if aviso:
        lanzador.asegurar_servidor()   # para que al pulsar la notificación la app responda
        notificar(*aviso)
    if forzar:
        print("Revisión hecha.", "Notificación:" if aviso else "Nada que notificar.", aviso or "")


if __name__ == "__main__":
    main()
