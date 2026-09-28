"""
abrir_app.pyw — Abre el Agente de emails con un doble clic, sin terminales
===========================================================================

Lo lanza el acceso directo del escritorio con el pythonw.exe del venv.
Arranca el servidor oculto si hace falta y abre la app en su propia ventana.

El servidor se queda funcionando en segundo plano hasta que apagues el PC.
Después de cambiar código Python, reinícialo:  taskkill /IM pythonw.exe /F
"""

import lanzador

if not lanzador.DIST.exists():
    lanzador.mensaje("Falta la app compilada.\n\nEn la carpeta 'app' ejecuta una vez:\n"
                     "npx expo export --platform web")
elif not lanzador.asegurar_servidor():
    lanzador.mensaje(f"El servidor no ha arrancado.\n\nMira el final de:\n{lanzador.LOG_SERVIDOR}")
else:
    lanzador.abrir_ventana()
