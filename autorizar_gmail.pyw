"""
autorizar_gmail.pyw — Volver a dar permiso a Gmail (doble clic)
================================================================

Se usa cuando el permiso ha caducado o se ha revocado. Normalmente se lanza
solo al pulsar la notificación de Windows (página /gmail/autorizar); también
se puede abrir a mano con un doble clic. Abre el navegador, eliges tu cuenta y pulsas Permitir;
al terminar te confirma qué cuenta ha quedado autorizada.

En el navegador verás "Google no ha verificado esta aplicación":
Avanzado -> Ir a agente-emails (no seguro) -> Permitir.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import lanzador  # noqa: E402

try:
    import gmail
    direccion = gmail.reautorizar()
except Exception as e:
    lanzador.mensaje(f"No se ha podido autorizar Gmail.\n\n{type(e).__name__}: {e}")
else:
    (RAIZ / ".aviso_autorizacion").unlink(missing_ok=True)   # que un fallo futuro vuelva a avisar
    lanzador.mensaje(f"Gmail autorizado: {direccion}\n\nLa próxima revisión (diaria, o el botón "
                     "«Revisar bandeja») funcionará con normalidad.", error=False)
