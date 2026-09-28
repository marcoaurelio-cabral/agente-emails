"""
lanzador.py — Arrancar el servidor y abrir la app (código común)
================================================================

Lo usan abrir_app.pyw (el acceso directo) y revision_diaria.pyw (el aviso
diario), para que los dos arranquen el servidor exactamente igual.
"""

import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
LOG_SERVIDOR = RAIZ / "servidor.log"
DIST = RAIZ / "app" / "dist"
ESPERA_MAX = 25  # segundos


def mensaje(texto: str, error: bool = True):
    """Ventana de aviso de Windows (o print fuera de Windows)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto, "Agente de emails", 0x10 if error else 0x40)
    except Exception:
        print(texto)


def servidor_vivo() -> bool:
    try:
        with urllib.request.urlopen(URL + "/salud", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def asegurar_servidor() -> bool:
    """Arranca el servidor oculto (solo accesible desde este PC) si no está
    funcionando, y espera a que responda. True si queda funcionando."""
    if servidor_vivo():
        return True
    log = open(LOG_SERVIDOR, "a", encoding="utf-8")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · arrancando =====\n")
    log.flush()
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=RAIZ, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    limite = time.time() + ESPERA_MAX
    while time.time() < limite:
        if servidor_vivo():
            return True
        time.sleep(0.5)
    return False


def abrir_ventana():
    """La app en una ventana propia de Edge (modo aplicación) o, si no, el navegador."""
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(edge).exists():
            subprocess.Popen([edge, f"--app={URL}", "--window-size=480,900"])
            return
    webbrowser.open(URL)
