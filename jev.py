"""
jev.py — Prefiltro de decisión (TypeSafe Jev)
=============================================

Jev NO es un LLM de texto: no escribe resúmenes ni extrae plazos. Recibe un
ESTADO (el email) y PREGUNTAS TIPADAS, y devuelve valores con probabilidades
calibradas. Por eso vive en su propio módulo y no implementa LLMProvider:
hace otra cosa.

Su papel en el sistema es ser la PRIMERA ETAPA de una cascada:

    email ──► Jev (rápido, ~0,04 $/M tokens)
                │
                ├─ p(importante) muy baja ──► ruido, FIN. (no se llama al LLM)
                │
                └─ importante o dudoso ─────► LLM (resumen, acción, plazo)

Decisión de diseño: el UMBRAL vive en servicio.py, no aquí. Este módulo
informa; quien decide es tu código. Y guardamos la probabilidad entera,
no solo la etiqueta, para poder calibrar después (ver calibrar_jev.py).

Política de fallos (IMPORTANTE):
  - Errores del SERVICIO (rate limit, timeout, 5xx, clave o modelo inválido):
    evaluar() devuelve None y el email pasa al LLM. Se falla "abierto": cuesta
    más, pero nunca esconde un email importante. Se CUENTAN y se muestran en
    resumen_uso(): un fallo tragado en silencio es un fallo que no ves nunca.
  - Tras MAX_ERRORES_SEGUIDOS fallos consecutivos, se abre el CORTOCIRCUITO:
    Jev se desactiva el resto de la ejecución. Si el problema es permanente
    (clave mala, versión inexistente), no tiene sentido pagar reintentos y
    esperas en cada uno de los 150 emails.
  - Errores de PROGRAMACIÓN (un argumento mal puesto, un tipo incorrecto) NO
    se capturan: deben reventar ruidosamente para que los arregles.

Requisitos:  pip install typesafe-sdk   +   TYPESAFE_API_KEY en el .env
Si no hay clave, disponible() devuelve False y el sistema sigue funcionando
exactamente como antes (solo LLM). Degradación elegante.
"""

import atexit
import os

from dotenv import load_dotenv

load_dotenv()

# Versión FIJA, no el alias 'jev-latest': el UMBRAL_RUIDO de servicio.py está
# calibrado contra las probabilidades de ESTE modelo. Si el alias apuntara a
# una versión nueva, el umbral podría descalibrarse sin avisar.
# OJO: si este nombre no existe, todas las llamadas fallarán; lo verás en el
# contador de errores y calibrar_jev.py abortará avisándote.
MODELO = os.getenv("JEV_MODEL", "jev-1.13.0")
MAX_ERRORES_SEGUIDOS = 3

_cliente = None
_intentado = False
_errores_seguidos = 0
_cortocircuito = False

# Contador de uso, igual que en llm.py, más los errores.
uso = {"llamadas": 0, "entrada": 0, "salida": 0, "errores": 0, "ultimo_error": None}


def disponible() -> bool:
    """¿Podemos usar Jev? (SDK instalado + clave + sin cortocircuito)."""
    global _cliente, _intentado
    if _cortocircuito:
        return False
    if _intentado:
        return _cliente is not None
    _intentado = True
    if not os.getenv("TYPESAFE_API_KEY"):
        return False
    try:
        from typesafe_sdk import TypeSafeClient
        _cliente = TypeSafeClient()
        atexit.register(_cliente.close)
    except Exception as e:
        uso["ultimo_error"] = f"no se pudo crear el cliente: {e}"
        _cliente = None
    return _cliente is not None


def cortocircuito_abierto() -> bool:
    return _cortocircuito


# ─────────────────────────────────────────────────────────────────────────
# EL ESTADO Y LAS PREGUNTAS
#
# El perfil del destinatario va UNA vez en el estado, no repetido en cada
# pregunta. Las preguntas se refieren a las partes del estado por su ruta
# entre comillas invertidas (`email`, `destinatario`), como indica la guía.
# ─────────────────────────────────────────────────────────────────────────

DESTINATARIO = {
    "nombre": "Marco",
    "estudios": "3º del Grado en Ingeniería Informática, Universidad Francisco de Vitoria (Madrid)",
    "intereses": "emprendimiento, becas, hackathons, prácticas de programación",
    # Sin esto, Jev medía la relevancia solo como "oportunidad" y un amigo
    # preguntando por el sábado salía con p=0.44 (calibración del 19/09).
    "siempre_relevante": "cualquier correo escrito personalmente para él por familia, "
                         "amigos o conocidos, sea cual sea el tema",
}

# Noul con criteria {true, false}: cada resultado definido por separado, más
# claro para el modelo que una instrucción mezclando "cuenta / no cuenta".
INSTRUCCIONES_IMPORTANTE = "¿Es este `email` relevante para el `destinatario`, según su perfil?"
CRITERIOS_IMPORTANTE = {
    "true": (
        "Cualquier correo escrito personalmente para él O otra persona reenviado a él por una persona real (familia, "
        "amigos, conocidos, profesores), SEA CUAL SEA EL TEMA, aunque sea informal o no "
        "tenga que ver con estudios ni oportunidades; becas y ayudas; eventos de "
        "informática o emprendimiento en España u online; comunicaciones de su "
        "universidad dirigidas a su curso o a todos (Canvas, secretaría, entregas, notas); "
        "premios y oportunidades económicas legítimas; respuestas a candidaturas suyas, "
        "incluidos rechazos; ofertas de empleo de programación afines a su perfil."
        "Confirmación de eventos como vuelos, bus o tren todos con fecha"
    ),
    "false": (
        "Publicidad, promociones, newsletters comerciales, loterías; ofertas masivas de "
        "empleo no informático; notificaciones automáticas rutinarias (redes sociales, "
        "recibos de compras propias, alertas de inicio de sesión propio); acuses de recibo "
        "de formularios que él mismo envió; comunicaciones dirigidas a OTRO curso u otra "
        "titulación; estafas y phishing."
    ),
}

# 'otro' está a propósito: opción de escape cuando la lista puede ser incompleta.
CRITERIOS_CATEGORIA = {
    "beca": "Becas, ayudas al estudio, convocatorias de movilidad o financiación académica",
    "evento": "Charlas, hackathons, congresos, competiciones, networking, viajes organizados",
    "universidad": "Comunicaciones de la UFV: Canvas, secretaría, profesores, entregas, notas, horarios",
    "personal": "Correos escritos por una persona real dirigidos a él (familia, amigos, contactos)",
    "dinero": "Premios, concursos con premio, subvenciones, reembolsos de fuentes legítimas",
    "empleo_afin": "Ofertas o procesos de empleo/prácticas de informática y programación",
    "promocional": "Publicidad, descuentos, newsletters comerciales, loterías, marketing",
    "automatico": "Notificaciones automáticas rutinarias: redes sociales, recibos, alertas, acuses",
    "sospechoso": "Phishing, estafas, premios falsos, peticiones de datos bancarios con urgencia",
    "otro": "No encaja claramente en ninguna de las anteriores",
}


def evaluar(remitente: str, asunto: str, cuerpo: str, max_caracteres: int = 1500) -> dict | None:
    """Evalúa un email. Devuelve dict con:
        noul            p(importante) de 0 a 1, calibrada
        categoria       etiqueta elegida
        confianza       confianza de la categoría
        probabilidades  distribución completa (se guarda para auditoría)
        modelo          versión REAL devuelta por la API
    o None si Jev no está disponible o el servicio falló (ver política arriba).
    """
    global _errores_seguidos, _cortocircuito
    if not disponible():
        return None

    from typesafe_sdk import Choice, Noul, TypeSafeError

    estado = {
        "destinatario": DESTINATARIO,
        "email": {"remitente": remitente, "asunto": asunto, "cuerpo": cuerpo[:max_caracteres]},
    }
    try:
        respuesta = _cliente.system_one(
            state=estado,
            model=MODELO,
            questions={
                "importante": Noul(instructions=INSTRUCCIONES_IMPORTANTE,
                                   criteria=CRITERIOS_IMPORTANTE),
                "categoria": Choice(
                    instructions="¿Qué tipo de `email` es? Elige la categoría que mejor lo describe.",
                    criteria=CRITERIOS_CATEGORIA,
                ),
            },
        )
    except TypeSafeError as e:
        # Fallo del servicio tras agotar los reintentos del SDK. Este email pasa
        # al LLM, pero el fallo queda REGISTRADO y a la vista.
        uso["errores"] += 1
        uso["ultimo_error"] = f"{type(e).__name__}: {e}"
        _errores_seguidos += 1
        if _errores_seguidos >= MAX_ERRORES_SEGUIDOS:
            _cortocircuito = True
        return None

    _errores_seguidos = 0
    u = getattr(respuesta, "usage", None)
    uso["llamadas"] += 1
    if u is not None:
        uso["entrada"] += getattr(u, "input_tokens", 0) or 0
        uso["salida"] += getattr(u, "output_tokens", 0) or 0

    a_imp = respuesta.answers["importante"]
    a_cat = respuesta.answers["categoria"]
    return {
        "noul": float(a_imp.noul),
        "categoria": a_cat.choice,
        "confianza": float(getattr(a_cat, "confidence", 0.0)),
        "probabilidades": dict(getattr(a_cat, "probabilities", {}) or {}),
        "modelo": getattr(respuesta, "model", MODELO),
    }


def resumen_uso() -> str:
    partes = []
    if uso["llamadas"]:
        total = uso["entrada"] + uso["salida"]
        coste = uso["entrada"] / 1e6 * 0.042  # la salida no se factura
        partes.append(f"{uso['llamadas']} llamadas · {total:,} tokens · ≈ {coste:.4f} $"
                      .replace(",", "."))
    else:
        partes.append("sin llamadas correctas")
    if uso["errores"]:
        partes.append(f"⚠️ {uso['errores']} ERRORES (último: {uso['ultimo_error']})")
    if _cortocircuito:
        partes.append(f"🔌 prefiltro DESACTIVADO tras {MAX_ERRORES_SEGUIDOS} fallos seguidos")
    return " · ".join(partes)
