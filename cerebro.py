"""
cerebro.py — El clasificador (código). El CRITERIO vive en criterio.md.
======================================================================

Separación deliberada:
    criterio.md  -> QUÉ le importa a Marco. Lo editas tú, en texto plano.
    cerebro.py   -> CÓMO se le pregunta al modelo. Código.
Así una actualización del código nunca pisa tu criterio, y un cambio de
criterio nunca obliga a tocar código.

Claves de agrupación (las extrae el modelo en la MISMA llamada, casi sin coste):
  tipo_entidad, entidad, fecha_entidad, referencia -> agrupar.py (código, sin IA)
  decide qué emails hablan de lo mismo (el mismo viaje, la misma reunión).
  confirma_hecho -> si el email demuestra que algo ya está hecho; agrupar.py
  cierra la tarea pendiente de su mismo grupo.
  rol: solo se le dice al modelo cuando Marco va en copia.
"""

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from llm import get_provider

ARCHIVO_CRITERIO = Path(__file__).parent / "criterio.md"
if not ARCHIVO_CRITERIO.exists():
    raise FileNotFoundError(
        "Falta criterio.md. Si vienes de la versión con el CRITERIO dentro de "
        "cerebro.py, extráelo primero (mira COMANDOS.txt o la conversación)."
    )
CRITERIO = ARCHIVO_CRITERIO.read_text(encoding="utf-8").strip()


class Clasificacion(BaseModel):
    importante: bool = Field(description="True si es relevante para Marco (aunque repita algo ya visto: agrupar es cosa del sistema); False si es ruido.")
    categoria: str = Field(description="beca | evento | viaje | personal | dinero | empleo_afin | ruido")
    prioridad: str = Field(description="alta | media | baja")
    motivo: str = Field(description="Una frase breve explicando la decisión.")
    resumen: str = Field(description="Resumen en una frase de qué es el email.")
    requiere_accion: bool = Field(description="True solo si Marco tiene que hacer algo concreto.")
    accion: str = Field(description="La acción en infinitivo y corta. Cadena vacía si no hay.")
    fecha_limite: str = Field(description="Plazo en formato AAAA-MM-DD, o cadena vacía si no hay.")
    tipo_entidad: str = Field(default="otro", description=(
        "De qué trata, para agrupar emails sobre lo mismo: viaje | reunion | evento | "
        "entrega | tramite | otro"))
    entidad: str = Field(default="", description=(
        "Nombre corto de ESO concreto, siempre igual para lo mismo: el destino de un viaje "
        "('Venecia'), el nombre de una reunión o evento ('Bienvenida Explorer'), la entrega "
        "o trámite ('Formulario beca ACE2EU'). Vacío si es 'otro'."))
    fecha_entidad: str = Field(default="", description=(
        "Fecha de ESO (salida del viaje, día del evento, plazo del trámite), AAAA-MM-DD, o vacía."))
    referencia: str = Field(default="", description=(
        "Código de reserva, localizador o número de pedido si aparece (ej. 'G8LLTK'); si no, vacío."))
    confirma_hecho: bool = Field(default=False, description=(
        "True si el email demuestra que algo que Marco tenía que hacer YA está hecho: acuse de "
        "un formulario que envió, nota publicada de una entrega, inscripción confirmada."))

    @field_validator("fecha_limite")
    @classmethod
    def _fecha_valida(cls, v: str) -> str:
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

    @field_validator("fecha_entidad")
    @classmethod
    def _fecha_entidad_valida(cls, v: str) -> str:
        return cls._fecha_valida(v)

    @field_validator("referencia")
    @classmethod
    def _referencia_limpia(cls, v: str) -> str:
        return "".join((v or "").split()).upper()

    @field_validator("entidad")
    @classmethod
    def _entidad_limpia(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def _coherencia(self):
        """El prompt propone; el código impone.
        - una tarea sin acción no es tarea; un informativo no lleva acción ni plazo"""
        if self.requiere_accion and not self.accion:
            self.requiere_accion = False
        if not self.requiere_accion:
            self.accion = ""
            self.fecha_limite = ""
        return self


_llm = get_provider()

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# Solo se informa de la señal que AYUDA: ir en copia. "No figuras" no se dice
# nunca: puede llegarte a otra dirección tuya o en copia oculta (lo normal en
# envíos a todos los seleccionados de una beca), y el modelo lo usaba para descartar.
_ROL = {
    "copia": "Marco figura EN COPIA (CC) en este email.",
}


def clasificar(remitente: str, asunto: str, cuerpo: str,
               fecha_email: date | None = None, hoy: date | None = None,
               rol: str = "") -> Clasificacion:
    """Clasifica un email según el criterio de Marco.

    fecha_email: referencia para los plazos relativos. hoy: contexto.
    rol: 'para' | 'copia' | 'no figura' | '' (desconocido).
    """
    hoy = hoy or date.today()
    fecha_email = fecha_email or hoy
    partes = [
        f"Este email se envió el {_DIAS[fecha_email.weekday()]} {fecha_email.isoformat()}. "
        f"Hoy es {_DIAS[hoy.weekday()]} {hoy.isoformat()}. "
        f"Los plazos relativos se calculan desde la fecha de envío."
    ]
    if rol in _ROL:
        partes.append(_ROL[rol])
    partes.append(f"EMAIL A CLASIFICAR:\nDe: {remitente}\nAsunto: {asunto}\n\n{cuerpo}")

    datos = _llm.rellenar_schema(
        system=CRITERIO,
        texto="\n\n".join(partes),
        tool_name="clasificar_email",
        descripcion="Registra la clasificación de un email según el criterio de Marco.",
        schema=Clasificacion.model_json_schema(),
    )
    return Clasificacion(**datos)
