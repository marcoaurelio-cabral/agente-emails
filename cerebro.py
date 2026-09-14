"""
cerebro.py — El clasificador personalizado de emails de Rodri
=============================================================

Fíjate en lo que YA NO hay aquí: ningún `import anthropic`. El cerebro no
sabe qué modelo hay debajo; solo habla con la interfaz de llm.py. Eso es
lo que te permite cambiar de proveedor (o a un modelo local gratis) sin
tocar una línea de este archivo.

Responsabilidades de este módulo, y solo estas:
  - el CRITERIO de Rodri (qué es importante y qué es ruido)
  - la forma de la SALIDA (Clasificacion, validada con Pydantic)
  - la función clasificar()

No sabe de dónde vienen los emails (Gmail, Outlook, un test) ni a dónde
van los resultados (consola, API, app). Ese aislamiento es lo que deja
que el MISMO cerebro sirva para todas las etapas del proyecto.

Uso rápido: python cerebro.py
"""

from pydantic import BaseModel, Field
from llm import get_provider


# ─────────────────────────────────────────────────────────────────────────
# EL CRITERIO DE RODRI — el corazón del producto. Editar esto es "entrenar"
# al agente. No hace falta tocar nada más.
# ─────────────────────────────────────────────────────────────────────────

CRITERIO = """Eres el asistente personal de correo de Rodri, estudiante de 3º de
Ingeniería Informática, con mentalidad EMPRENDEDORA y muchas ganas de experiencias
y oportunidades nuevas. Tu trabajo es separar lo que de verdad le importa del ruido.

LE IMPORTA (marcar como importante):
- BECAS de cualquier tipo (académicas, de emprendimiento, de movilidad, ayudas). Todas.
- EVENTOS de informática o emprendimiento: charlas, hackathons, competiciones,
  congresos, ferias, talleres, demo days, networking.
- CORREOS PERSONALES reales dirigidos a él: de profesores, de personas que le
  escriben directamente, o de empresas que contactan con él de forma genuina
  (no envíos masivos).
- "DINERO GRATIS": subvenciones, premios, concursos con premio, ayudas económicas,
  reembolsos, oportunidades de financiación.
- Ofertas de INFOJOBS u otros portales de empleo SOLO si encajan MUY bien con su
  perfil (informática, prácticas/junior relevantes). Las ofertas genéricas o poco
  alineadas NO le interesan.

ES RUIDO (marcar como no importante):
- Ofertas masivas y genéricas de InfoJobs u otros portales que no encajan con su perfil.
- Publicidad, promociones comerciales, newsletters de marketing, descuentos.
- Notificaciones automáticas irrelevantes (redes sociales, apps, etc.).

REGLA DE ORO ante la duda: si no tienes claro si algo es una oportunidad real
para Rodri (una beca dudosa, un evento que quizá encaje), márcalo como IMPORTANTE.
Es mucho peor esconderle una oportunidad buena que mostrarle una de más. Prefiere
el falso positivo al falso negativo."""


# ─────────────────────────────────────────────────────────────────────────
# SALIDA ESTRUCTURADA — datos, no prosa. Consumible por script, API o app.
# ─────────────────────────────────────────────────────────────────────────

class Clasificacion(BaseModel):
    importante: bool = Field(description="True si Rodri debería verlo; False si es ruido.")
    categoria: str = Field(description="beca | evento | personal | dinero | empleo_afin | ruido")
    prioridad: str = Field(description="alta | media | baja")
    motivo: str = Field(description="Una frase breve explicando la decisión.")
    resumen: str = Field(description="Resumen en una frase de qué es el email y qué acción sugiere (si la hay).")


# Un único proveedor para todo el módulo (se crea una vez, se reutiliza).
_llm = get_provider()


def clasificar(remitente: str, asunto: str, cuerpo: str) -> Clasificacion:
    """Clasifica un email según el criterio de Rodri.

    Recibe texto, devuelve un objeto validado. No sabe nada de Gmail ni
    de qué modelo hay debajo. Esa es la gracia.
    """
    contenido = f"De: {remitente}\nAsunto: {asunto}\n\n{cuerpo}"
    datos = _llm.rellenar_schema(
        system=CRITERIO,
        texto=contenido,
        tool_name="clasificar_email",
        descripcion="Registra la clasificación de un email según el criterio de Rodri.",
        schema=Clasificacion.model_json_schema(),
    )
    return Clasificacion(**datos)  # Pydantic valida: si el modelo devuelve basura, peta aquí


# ─────────────────────────────────────────────────────────────────────────
# PRUEBA con emails realistas de tu caso
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    EJEMPLOS = [
        ("becas@fundacion.org", "Convocatoria beca emprendimiento joven 2026",
         "Abrimos la convocatoria de becas para proyectos de estudiantes emprendedores. Dotación de 3000€."),
        ("no-reply@infojobs.net", "20 nuevas ofertas para ti",
         "Teleoperador, comercial, mozo de almacén, dependiente... ¡Inscríbete ya!"),
        ("profesor.garcia@ufv.es", "Sobre tu proyecto de la asignatura",
         "Rodri, he visto tu entrega y quería comentarte un par de cosas. ¿Puedes pasarte el jueves?"),
        ("eventos@techmadrid.com", "Hackathon de IA este finde - inscripciones abiertas",
         "48h construyendo con IA, premios de 2000€ al ganador. Plazas limitadas."),
        ("ofertas@tienda.com", "🔥 -50% en toda la tienda solo hoy",
         "No te pierdas nuestras rebajas de temporada. Envío gratis a partir de 30€."),
        ("rrhh@startup.io", "Prácticas de desarrollo backend - tu perfil nos encaja",
         "Vimos tu perfil de estudiante de informática y buscamos alguien para prácticas en Python/FastAPI."),
    ]

    for remitente, asunto, cuerpo in EJEMPLOS:
        c = clasificar(remitente, asunto, cuerpo)
        marca = "⭐ IMPORTANTE" if c.importante else "   ruido    "
        print(f"{marca} [{c.categoria}/{c.prioridad}] {asunto}")
        print(f"             → {c.motivo}\n")
