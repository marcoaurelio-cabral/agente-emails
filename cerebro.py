"""
cerebro.py — El clasificador personalizado de emails de Marco — v3
==================================================================

Novedades v3: el cerebro ahora extrae TAREAS.
  - requiere_accion: ¿Marco tiene que HACER algo?
  - accion:          qué, en una frase corta ("Entregar P1 de DIS")
  - fecha_limite:    AAAA-MM-DD si el email da un plazo, o "" si no.

Dos detalles de ingeniería importantes:

  1. El modelo NO SABE QUÉ DÍA ES HOY. Para que "este jueves" o "mañana"
     se conviertan en una fecha real, le decimos la fecha actual en cada
     llamada (ver clasificar()). Sin esto, los plazos relativos salen mal.

  2. Pydantic VALIDA el formato de fecha. Si el modelo devuelve "sin fecha",
     "N/A" o "el jueves", el validador lo convierte en "" en vez de meter
     basura en la BD. Guardrail de datos, como en el Proyecto 2.

Sigue sin saber nada de Gmail, de la BD ni del modelo que hay debajo.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator
from llm import get_provider


# ─────────────────────────────────────────────────────────────────────────
# EL CRITERIO DE Marco — el corazón del producto.
# ─────────────────────────────────────────────────────────────────────────

CRITERIO = """Eres el asistente personal de correo de Marco: estudiante de 3º del Grado en
Ingeniería Informática en la UFV (Madrid), con mentalidad EMPRENDEDORA y muchas ganas
de experiencias y oportunidades nuevas. Tu trabajo es separar lo que de verdad le
importa del ruido, y detectar qué tareas y plazos tiene.

LE IMPORTA (marcar como importante):
- BECAS de cualquier tipo: académicas, de emprendimiento, de movilidad, ayudas. Todas.
- EVENTOS de informática o emprendimiento: charlas, hackathons, competiciones,
  congresos, ferias, talleres, demo days, networking. SOLO si son en Madrid, en
  España, u ONLINE/remotos. Un evento presencial en otro país (salvo movilidades
  organizadas por la UFV) es prioridad BAJA: no puede asistir.
- CORREOS PERSONALES reales dirigidos a él: de profesores, de familia, de amigos o
  conocidos, de personas que le escriben directamente, o de empresas que contactan
  con él de forma genuina (no envíos masivos).
- COMUNICACIONES DE LA UNIVERSIDAD (UFV): secretaría, matrícula, exámenes,
  convocatorias, horarios, avisos de Canvas, entregas, notas, eventos de la uni.
  Aunque sean notificaciones automáticas, son importantes.
  EXCEPCIÓN: si el correo va dirigido explícitamente a OTRO curso u OTRA titulación
  (por ejemplo "aulas de 1º y 2º de Ingeniería Física"), es RUIDO: no le afecta.
  Si es genérico o no especifica curso, márcalo importante.
- RESPUESTAS A SUS PROPIAS CANDIDATURAS: si Marco se apuntó a algo (hackathon,
  beca, movilidad, proceso de selección), cualquier respuesta es IMPORTANTE,
  también los rechazos. Necesita saber en qué quedó.
- "DINERO GRATIS" LEGÍTIMO: subvenciones, premios, concursos con premio, ayudas
  económicas, reembolsos, financiación de fuentes reales e identificables.
- Ofertas de INFOJOBS u otros portales de empleo SOLO si encajan MUY bien con su
  perfil: desarrollo de software, informática, prácticas o junior en programación.

ES RUIDO (marcar como no importante):
- Ofertas masivas de empleo que no sean de informática/programación (comercial,
  azafato, teleoperador, dependiente, mozo de almacén...).
- Publicidad, promociones, newsletters de marketing, descuentos, cupones, loterías.
- Notificaciones automáticas rutinarias: redes sociales, recibos de compras propias,
  confirmaciones de reservas propias, alertas de seguridad de inicios de sesión
  propios, bienvenidas a servicios, acuses de recibo de formularios que él mismo
  rellenó. (Las de la UNIVERSIDAD no cuentan aquí.)
- ESTAFAS Y PHISHING: "has ganado un premio", "confirma tus datos bancarios",
  urgencia artificial, remitentes raros. Aunque hablen de dinero, son RUIDO.

PRIORIDAD (sé estricto: la mayoría NO es alta):
- alta:  requiere una ACCIÓN de Marco con plazo en los próximos 7 días, o es una
         oportunidad con fecha límite (beca, premio, inscripción, entrega).
- media: información relevante sin plazo inmediato (notas publicadas, avisos de
         clase, eventos a más de una semana).
- baja:  bueno saberlo, sin acción requerida.

TAREAS Y PLAZOS:
- requiere_accion = true SOLO si Marco tiene que HACER algo concreto y definido:
  entregar una práctica, rellenar un formulario, inscribirse, responder, firmar,
  pagar, aceptar una invitación, corregir algo, asistir a algo con fecha, o una
  lectura/ejercicio que un profesor encarga expresamente para clase.
- NO es tarea: leer información general, "considerar", "informarse", "valorar",
  "revisar", "verificar", "comprobar", ni nada condicional u opcional ("si te
  interesa", "si no puedes asistir avisa", "opcional"). Si dudas,
  requiere_accion = false: el email seguirá siendo importante, solo que informativo.
- NO es tarea asistir a CLASES regulares, ni un cambio de horario o de aula de una
  asignatura: eso es información. Solo cuenta asistir a un EVENTO puntual con
  inscripción o fecha concreta (charla, hackathon, networking, viaje).
- NO es tarea un acuse de recibo de algo que Marco ya hizo (confirmación de
  formulario enviado, inscripción registrada, "hemos recibido tu respuesta"),
  aunque mencione "próximos pasos". La tarea, si existe, viene en otro email.
- accion: verbo concreto en infinitivo + objeto, corto ("Entregar P1 de DIS",
  "Firmar convenio Erasmus", "Inscribirse en EY Campus"). Nunca verbos débiles.
  Vacía si no hay acción.
- fecha_limite: formato AAAA-MM-DD, SOLO si el email da una fecha explícita o
  claramente deducible. Los plazos relativos ("mañana", "este jueves", "en 7 días")
  se calculan desde la FECHA DE ENVÍO del email que se te indica, no desde hoy.
  Si el email tiene varias fechas, usa la del plazo de la acción principal.
  Si no hay plazo, déjala vacía. NO inventes plazos. Si ya pasó, ponla igualmente.

REGLA DE ORO ante la duda: si no tienes claro si algo es una oportunidad real
para Marco, márcalo como IMPORTANTE. Es mucho peor esconderle una oportunidad
buena que mostrarle una de más. Esta regla NO aplica a estafas evidentes."""


# ─────────────────────────────────────────────────────────────────────────
# SALIDA ESTRUCTURADA
# ─────────────────────────────────────────────────────────────────────────

class Clasificacion(BaseModel):
    importante: bool = Field(description="True si Marco debería verlo; False si es ruido.")
    categoria: str = Field(description="beca | evento | personal | dinero | empleo_afin | ruido")
    prioridad: str = Field(description="alta | media | baja")
    motivo: str = Field(description="Una frase breve explicando la decisión.")
    resumen: str = Field(description="Resumen en una frase de qué es el email.")
    requiere_accion: bool = Field(description="True solo si Marco tiene que hacer algo concreto.")
    accion: str = Field(description="La acción en infinitivo y corta. Cadena vacía si no hay.")
    fecha_limite: str = Field(description="Plazo en formato AAAA-MM-DD, o cadena vacía si no hay.")

    @field_validator("fecha_limite")
    @classmethod
    def _fecha_valida(cls, v: str) -> str:
        """Guardrail: solo aceptamos AAAA-MM-DD real. Cualquier otra cosa -> ''."""
        v = (v or "").strip()
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            return ""

    @field_validator("accion")
    @classmethod
    def _accion_limpia(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def _coherencia_tarea(self):
        """Guardrail de coherencia entre campos (esto el prompt no lo garantiza):
        - una tarea sin acción NO es una tarea -> requiere_accion = False
        - un email informativo no lleva acción ni plazo -> se limpian
        Así la BD nunca tiene "tareas fantasma" ni plazos huérfanos."""
        if self.requiere_accion and not self.accion:
            self.requiere_accion = False
        if not self.requiere_accion:
            self.accion = ""
            self.fecha_limite = ""
        return self


_llm = get_provider()

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def clasificar(remitente: str, asunto: str, cuerpo: str,
               fecha_email: date | None = None, hoy: date | None = None) -> Clasificacion:
    """Clasifica un email según el criterio de Marco.

    `fecha_email` es cuándo se ENVIÓ el email: la referencia correcta para
    "en 7 días" o "este jueves". `hoy` es solo contexto (para que el modelo
    sepa qué plazos ya han pasado). Si no se pasa fecha_email, se asume hoy.
    Ambas se pueden fijar a mano para tests reproducibles.
    """
    hoy = hoy or date.today()
    fecha_email = fecha_email or hoy
    contexto = (
        f"Este email se envió el {_DIAS[fecha_email.weekday()]} {fecha_email.isoformat()}. "
        f"Hoy es {_DIAS[hoy.weekday()]} {hoy.isoformat()}. "
        f"Los plazos relativos se calculan desde la fecha de envío."
    )
    contenido = f"{contexto}\n\nDe: {remitente}\nAsunto: {asunto}\n\n{cuerpo}"

    datos = _llm.rellenar_schema(
        system=CRITERIO,
        texto=contenido,
        tool_name="clasificar_email",
        descripcion="Registra la clasificación de un email según el criterio de Marco.",
        schema=Clasificacion.model_json_schema(),
    )
    return Clasificacion(**datos)


if __name__ == "__main__":
    EJEMPLOS = [
        ("becas@fundacion.org", "Convocatoria beca emprendimiento 2026",
         "Abrimos la convocatoria. Dotación de 3000€. Plazo de solicitud: hasta el próximo viernes."),
        ("DIS(B) <notifications@instructure.com>", "Tarea disponible: P3 - Docker",
         "La práctica P3 ya está publicada en Canvas. Fecha de entrega: 25 de septiembre a las 23:59."),
        ("ofertas@tienda.com", "-50% solo hoy", "Rebajas de temporada, envío gratis."),
    ]
    for remitente, asunto, cuerpo in EJEMPLOS:
        c = clasificar(remitente, asunto, cuerpo)
        marca = "⭐" if c.importante else "  "
        tarea = f" · TAREA: {c.accion} (límite {c.fecha_limite or 'sin fecha'})" if c.requiere_accion else ""
        print(f"{marca} [{c.categoria}/{c.prioridad}] {asunto}{tarea}")
