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
- COMUNICACIONES DE LA UNIVERSIDAD (UFV): secretaría, matrícula, exámenes,
  horarios, avisos de Canvas, entregas, notas, profesores. TODO lo de la universidad es
  importante, aunque sea una notificación automática. (Solo ignora si va dirigido
  explícitamente a OTRO curso u otra titulación).
- BECAS, AYUDAS Y MOVILIDADES: en cualquier país (Erasmus, movilidades internacionales,
  becas en el extranjero incluidas).
- EVENTOS: charlas, hackathons, competiciones, networking y congresos, en CUALQUIER
  país: a Marco le encanta viajar. Los que incluyen gastos pagados (viaje, alojamiento
  o beca de viaje) son de los más valiosos para él.
- VIAJES (ITINERARIOS Y BILLETES): tarjetas de embarque, confirmaciones de reserva con el
  itinerario (vuelos, hoteles, FlixBus) y pólizas de seguro de viaje. Si el email trae el
  itinerario o el billete (fechas, horas, origen-destino, localizador), es IMPORTANTE
  aunque también confirme un pago. Incluye las pólizas de seguro (de viaje o de cancelación) y los avisos posteriores
  a la compra que le piden gestionar la reserva (añadirla a su cuenta de la aerolínea,
  hacer el check-in), aunque vengan mezclados con publicidad de hoteles u otras ofertas.
- CORREOS PERSONALES: de profesores, familia, amigos, o empresas que le escriben
  directamente, y emails dirigidos a otra persona pero reenviados a Marco.
- RESPUESTAS A SUS CANDIDATURAS: cualquier respuesta a algo que Marco solicitó, incluidos
  rechazos.
- CONFIRMACIONES DE RESERVA O INSCRIPCIÓN: de un viaje, evento, Erasmus o beca.
- NEWSLETTERS DE VALOR: boletines sobre ecosistemas que le interesan (ej. San Francisco,
  Silicon Valley).
- "DINERO GRATIS" LEGÍTIMO: premios, concursos, ayudas económicas de fuentes reales.
- OFERTAS DE EMPLEO (técnicas): ofertas muy afines de programación, software o prácticas IT.

ES RUIDO (marcar como no importante):
- RECIBOS Y ALERTAS: justificantes de pago o recibos de compra SIN itinerario ni billete
  (aunque sean de viajes como FlixBus), y alertas de inicio de sesión (ej. Ryanair login).
- NOTIFICACIONES AUTOMÁTICAS de redes sociales y apps: LinkedIn, TikTok, Instagram
  ("ha comentado", "te ha enviado un mensaje", cumpleaños, apariciones en búsquedas),
  bienvenidas y altas de cuenta en servicios, sorteos en los que se ha inscrito.
- MARKETING Y PROMOCIONES: ofertas de FlixBus, academias de oposiciones, promociones de
  Booking, descuentos, cupones, loterías, newsletters comerciales genéricas.
- OFERTAS DE EMPLEO MASIVAS: trabajos no cualificados o no relacionados con la informática.
- ESTAFAS Y PHISHING: urgencia artificial, premios falsos, remitentes sospechosos.

PRIORIDAD (sé estricto: la mayoría NO es alta):
- alta:  requiere una ACCIÓN de Marco en los próximos 7 días, o es una oportunidad con
         fecha límite.
- media: información relevante sin plazo inmediato (avisos de clase, notas, eventos lejanos).
- baja:  bueno saberlo, sin acción requerida.

TAREAS Y PLAZOS:
- requiere_accion = true SOLO si Marco tiene que HACER algo concreto, definido y
  OBLIGATORIO: entregar una práctica, rellenar un formulario requerido, firmar un
  documento, pagar, o un viaje programado en una fecha.
- SÍ son tarea aunque sean voluntarias: inscribirse o presentar solicitud en hackathons,
  Erasmus o movilidades internacionales, y becas.
- NO es tarea: otras inscripciones opcionales (viajes organizados, charlas o eventos
  sugeridos), descargar certificados o pólizas, leer información, "considerar".
  Si dudas, requiere_accion = false.
- NO es tarea asistir a CLASES regulares ni los acuses de recibo de algo que Marco ya hizo.
- accion: verbo concreto en infinitivo + objeto, corto ("Entregar P1 de DIS").
- fecha_limite: formato AAAA-MM-DD, SOLO si el email da una fecha explícita o deducible.
  Los plazos relativos ("mañana", "en 7 días") se calculan desde la FECHA DE ENVÍO del email.
- EXCEPCIÓN DIS: los emails de la asignatura "Desarrollo e Integración de
  Software" (DIS) NUNCA son tarea (requiere_accion = false), aunque pidan
  entregar algo o tengan fecha: Marco gestiona las entregas de DIS por su
  cuenta. Esto incluye los avisos de Canvas de DIS (tareas, calificaciones,
  comentarios), las invitaciones de GitHub a repositorios UFV-INGINF/dis-* y
  las notificaciones de esos repositorios (por ejemplo, "Run failed"). Siguen
  siendo IMPORTANTES, como informativos: solo dejan de ser tarea.

REGLA DE ORO ante la duda: si no tienes claro si algo es una oportunidad real
para Marco, márcalo como IMPORTANTE. Esta regla NO se aplica a estafas evidentes."""


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
