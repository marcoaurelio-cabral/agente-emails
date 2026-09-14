"""
llm.py — La "toma de corriente" del cerebro
===========================================

Aquí vive TODA la dependencia del proveedor de LLM. El cerebro no sabe
si debajo hay Anthropic, un modelo local con Ollama o cualquier otro:
solo pide "rellena este schema a partir de este texto" y recibe un dict.

Es el patrón Strategy de tu asignatura de Ingeniería del Software, con
inversión de dependencias: el cerebro depende de la ABSTRACCIÓN
(LLMProvider), no de un proveedor concreto. Cambiar de modelo = añadir
una clase aquí y cambiar una variable de entorno. Cero cambios en el
resto del proyecto.

Elegir proveedor:  variable de entorno LLM_PROVIDER  (anthropic | deepseek | ollama)
Elegir modelo:     variable de entorno LLM_MODEL     (por defecto: el del proveedor)

Proveedores disponibles:
  - anthropic  -> pip install anthropic   + ANTHROPIC_API_KEY
  - deepseek   -> pip install openai      + DEEPSEEK_API_KEY   (API compatible OpenAI)
  - ollama     -> hueco pendiente (modelo local gratis)
"""

import json
import os
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Contrato que debe cumplir cualquier proveedor.

    Solo una operación: dado un system prompt, un texto y un JSON schema,
    devolver un dict que cumpla ese schema. Es lo único que el cerebro
    necesita, así que es lo único que la interfaz expone. Interfaces
    pequeñas = fáciles de implementar para un proveedor nuevo.
    """

    @abstractmethod
    def rellenar_schema(self, system: str, texto: str,
                        tool_name: str, descripcion: str, schema: dict) -> dict:
        ...


class AnthropicProvider(LLMProvider):
    """Implementación con la API de Anthropic (la que usas ahora)."""

    def __init__(self, model: str | None = None):
        import anthropic  # import local: si no usas este proveedor, no hace falta tenerlo
        self.client = anthropic.Anthropic()
        # Sonnet por defecto. Para abaratar clasificación masiva, cambia a
        # "claude-haiku-4-5-20251001" con LLM_MODEL, sin tocar código, claude-sonnet-4-6.
        self.model = model or "claude-haiku-4-5-20251001"

    def rellenar_schema(self, system, texto, tool_name, descripcion, schema):
        tool = {"name": tool_name, "description": descripcion, "input_schema": schema}
        response = self.client.messages.create(
            model=self.model, max_tokens=600, system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": texto}],
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return tool_use.input


class DeepSeekProvider(LLMProvider):
    """Implementación con DeepSeek, a través de su API compatible con OpenAI.

    Fíjate en la diferencia con AnthropicProvider: el "formato de cable" es
    distinto (tools van dentro de {"type": "function", ...}, la respuesta llega
    en message.tool_calls con los argumentos como STRING JSON, etc.). Esa
    diferencia queda ENCERRADA aquí. El cerebro ni se entera. Eso es
    exactamente lo que compra la interfaz.

    Requiere: pip install openai   y   DEEPSEEK_API_KEY en el entorno.
    Modelo por defecto: deepseek-flash (el barato, V4.1 Flash).
    Alternativa más capaz y cara: deepseek-v4-pro (vía LLM_MODEL).
    """

    BASE_URL = "https://api.deepseek.com"

    def __init__(self, model: str | None = None):
        from openai import OpenAI  # import local: solo hace falta si usas este proveedor
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("Falta DEEPSEEK_API_KEY en el entorno.")
        self.client = OpenAI(api_key=api_key, base_url=self.BASE_URL)
        self.model = model or "deepseek-flash"

    def rellenar_schema(self, system, texto, tool_name, descripcion, schema):
        # Mismo schema JSON que en Anthropic, pero envuelto al estilo OpenAI.
        tool = {
            "type": "function",
            "function": {"name": tool_name, "description": descripcion, "parameters": schema},
        }
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": texto},
            ],
            tools=[tool],
            # Forzamos esta tool (equivalente al tool_choice de Anthropic).
            tool_choice={"type": "function", "function": {"name": tool_name}},
            max_tokens=600,
        )
        message = response.choices[0].message

        if message.tool_calls:
            # Los argumentos vienen como string JSON, no como dict: hay que parsear.
            return json.loads(message.tool_calls[0].function.arguments)

        # Fallback defensivo: si el modelo ignoró el tool_choice y contestó
        # con texto, intentamos leerlo como JSON antes de rendirnos.
        if message.content:
            return json.loads(message.content)

        raise RuntimeError("DeepSeek no devolvió ni tool_call ni JSON parseable.")


class OllamaProvider(LLMProvider):
    """HUECO para un modelo local gratuito (Llama, Mistral, Qwen... vía Ollama).

    Pendiente de implementar: cuando quieras migrar, esta es la ÚNICA clase
    que hay que escribir. El cerebro no cambia. Deberá:
      1. Llamar a la API local de Ollama (http://localhost:11434).
      2. Pedirle salida JSON que cumpla `schema`.
      3. Parsear y devolver el dict.
    """

    def __init__(self, model: str | None = None):
        self.model = model or "llama3"

    def rellenar_schema(self, system, texto, tool_name, descripcion, schema):
        raise NotImplementedError(
            "OllamaProvider aún no está implementado. "
            "Es el hueco preparado para migrar a un modelo local gratis."
        )


# ─────────────────────────────────────────────────────────────────────────
# Fábrica: el único sitio que decide QUÉ proveedor se usa.
# ─────────────────────────────────────────────────────────────────────────

_PROVEEDORES = {
    "anthropic": AnthropicProvider,
    "deepseek": DeepSeekProvider,
    "ollama": OllamaProvider,
}


def get_provider() -> LLMProvider:
    nombre = os.getenv("LLM_PROVIDER", "anthropic").lower()
    modelo = os.getenv("LLM_MODEL")  # None -> el proveedor usa su default
    if nombre not in _PROVEEDORES:
        raise ValueError(f"Proveedor '{nombre}' desconocido. Opciones: {list(_PROVEEDORES)}")
    return _PROVEEDORES[nombre](model=modelo)
