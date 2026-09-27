"""
abrir_app.pyw — Abre el Agente de emails con un doble clic, sin terminales
===========================================================================

Lo lanza el acceso directo del escritorio con el pythonw.exe del venv
(pythonw = Python SIN ventana de consola). Hace tres cosas:

  1. Si el servidor no está funcionando, lo arranca OCULTO en segundo plano
     (uvicorn, solo accesible desde este PC: 127.0.0.1). Lo que escriba el
     servidor va a servidor.log, por si algo falla.
  2. Espera a que responda.
  3. Abre la app en una ventana propia de Edge (modo aplicación: sin barra de
     direcciones ni pestañas). Si no encuentra Edge, usa el navegador normal.

El servidor se queda funcionando en segundo plano hasta que apagues el PC.
Después de cambiar código Python, reinícialo:  taskkill /IM pythonw.exe /F
"""

import ctypes
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
LOG = RAIZ / "servidor.log"
DIST = RAIZ / "app" / "dist"
ESPERA_MAX = 25  # segundos


def mensaje(texto: str):
    ctypes.windll.user32.MessageBoxW(0, texto, "Agente de emails", 0x10)


def servidor_vivo() -> bool:
    try:
        with urllib.request.urlopen(URL + "/salud", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def arrancar_servidor():
    log = open(LOG, "a", encoding="utf-8")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · arrancando =====\n")
    log.flush()
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=RAIZ, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def abrir_ventana():
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(edge).exists():
            subprocess.Popen([edge, f"--app={URL}", "--window-size=480,900"])
            return
    webbrowser.open(URL)


def main():
    if not DIST.exists():
        mensaje("Falta la app compilada.\n\nEn la carpeta 'app' ejecuta una vez:\n"
                "npx expo export --platform web")
        return
    if not servidor_vivo():
        arrancar_servidor()
        limite = time.time() + ESPERA_MAX
        while not servidor_vivo():
            if time.time() > limite:
                mensaje(f"El servidor no ha arrancado.\n\nMira el final de:\n{LOG}")
                return
            time.sleep(0.5)
    abrir_ventana()


if __name__ == "__main__":
    main()
