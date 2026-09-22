# Agente de emails

Agente personal que lee mi Gmail, decide qué es de verdad importante (becas,
eventos de informática/emprendimiento, comunicaciones de la UFV, ofertas
afines, dinero legítimo) y extrae tareas con su plazo. El objetivo: no perder
oportunidades entre el ruido (publicidad, InfoJobs genérico, notificaciones
automáticas) que compone ~80% de una bandeja real.

**Versión actual:** ver [`VERSION`](VERSION). Historial de cambios en
[`CHANGELOG.md`](CHANGELOG.md).

## Cómo funciona

```
Gmail ──► Jev (prefiltro, opcional) ──► LLM (Claude/DeepSeek) ──► SQLite ──► CLI / API
           barato, decide                caro, redacta y                    tareas,
           si hace falta el LLM           extrae tarea+plazo                importantes
```

1. **Se lee Gmail** (solo lectura) con lectura adaptativa: primero 1.500
   caracteres; si el email resulta importante y era más largo, se relee entero.
2. **Prefiltro Jev** (opcional): dos preguntas tipadas y calibradas —
   `p(importante)` y categoría. Si la probabilidad es baja **y** la categoría
   es descartable (`promocional`, `automatico`, `sospechoso`), el email se
   marca ruido sin gastar una llamada al LLM. Cualquier otra categoría
   (`personal`, `universidad`, `beca`, `evento`, `dinero`, `empleo_afin`,
   `otro`) va siempre al LLM, tenga la probabilidad que tenga: la decisión de
   descartar exige que **dos señales independientes** estén de acuerdo.
3. **El LLM clasifica**: importante o no, categoría, prioridad, si requiere
   una acción concreta y su plazo (resuelto contra la fecha de envío del
   email, no contra "hoy"). Salida validada con Pydantic, con guardrails de
   coherencia (una "tarea" sin acción deja de ser tarea).
4. **Se guarda en SQLite**, con deduplicación temporal de notificaciones
   dobles y estado de tarea (`pendiente` / `completada`).
5. **Corrección humana**: cualquier veredicto del modelo se puede corregir
   (`POST /emails/{id}/corregir`); la corrección manda sobre el modelo en
   todas las vistas y queda registrada como etiqueta de oro.

## Estado en esta versión (0.0.0)

Backend completo y usable desde la CLI o desde `/docs` (Swagger). **No hay
todavía app ni cliente propio** — es la siguiente etapa.

- Lectura de Gmail, clasificación con tareas y plazos, persistencia con
  migraciones automáticas de esquema.
- Prefiltro Jev calibrado y medido en sombra sobre bandeja real (ver
  `calibracion_jev.json` y el CHANGELOG): ahorra la mayoría de llamadas al
  LLM en el ruido, sin falsos negativos detectados hasta ahora.
- API REST (FastAPI) con tareas, importantes, ruido, corrección humana y
  revisión en segundo plano. Sin autenticación: pensada para correr en la
  WiFi de casa, no expuesta a internet.
- Sistema de evaluación con dataset propio etiquetado a ciegas por mí sobre
  mi bandeja real (dataset privado, no en el repo) y un dataset público de
  ejemplo para el portfolio.
- Dos proveedores de LLM intercambiables (Anthropic, DeepSeek) tras una
  interfaz común; cambiar de modelo no toca el resto del código.

## Estructura del proyecto

| Módulo | Responsabilidad |
|---|---|
| `llm.py` | Interfaz de proveedor de LLM (Anthropic / DeepSeek), contador de coste |
| `cerebro.py` | Criterio de clasificación, schema de salida, guardrails de coherencia |
| `jev.py` | Cliente del prefiltro TypeSafe Jev; nunca decide, solo informa |
| `politica.py` | Única fuente de verdad de cuándo se descarta un email sin LLM |
| `gmail.py` | Lectura de Gmail (OAuth, paginación, reintentos con backoff) |
| `almacen.py` | Persistencia SQLite: tareas, estados, corrección humana, migraciones |
| `servicio.py` | Casos de uso (la cascada Jev→LLM); lo usan la CLI y la API por igual |
| `revisar_bandeja.py` | CLI: pone la bandeja al día, imprime tareas e importantes |
| `tareas.py` | CLI: listar / completar tareas |
| `api.py` | Backend HTTP (FastAPI) — lo que consumirá la futura app |
| `evals.py` | Evaluación del cerebro contra un dataset etiquetado |
| `dataset_evals.json` | Dataset **público** de ejemplo (casos inventados + reales anonimizados) |
| `etiquetar.py` | Herramienta de etiquetado a ciegas sobre mi bandeja real |
| `reparar_dataset.py` | Repara `dataset_real.json` sin perder etiquetas ya hechas |
| `calibrar_jev.py` | Calibra el umbral del prefiltro contra el dataset etiquetado |
| `sombra_jev.py` | Mide el prefiltro sobre la bandeja real sin cambiar ninguna decisión |

Archivos que **no** están en el repo (`.gitignore`): `.env` (claves),
`credentials.json` / `token.json` (OAuth de Gmail), `emails.db` (mi bandeja),
`dataset_real*.json` (mis correos etiquetados).

## Cómo ejecutarlo

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
# .env con ANTHROPIC_API_KEY (y opcionalmente DEEPSEEK_API_KEY, TYPESAFE_API_KEY)
# credentials.json de Google Cloud (OAuth, tipo "Aplicación de escritorio")

python revisar_bandeja.py              # CLI: revisa la bandeja y muestra tareas
python tareas.py completar 3           # marcar una tarea hecha

uvicorn api:app --host 0.0.0.0 --port 8000 --reload
# http://localhost:8000/docs           # API interactiva
```

## Decisiones de diseño

- **Separación en capas**: fuente de datos (`gmail.py`), lógica (`cerebro.py`,
  `politica.py`), persistencia (`almacen.py`), casos de uso (`servicio.py`),
  e interfaces (`revisar_bandeja.py`, `api.py`) que no duplican lógica entre sí.
- **Regla de oro del criterio**: ante la duda, marcar importante. El coste de
  un falso positivo es leer un email de más; el de un falso negativo es
  perder una beca.
- **Defensa en dos señales** en el prefiltro: descartar sin LLM exige que la
  probabilidad sea baja *y* la categoría lo sea. Un fallo aislado de una
  señal no basta para perder un email importante.
- **Fallar abierto, nunca silencioso**: si el prefiltro falla (rate limit,
  modelo no encontrado...), el email pasa al LLM y el fallo queda contado y
  visible, con cortocircuito tras fallos consecutivos.
- **Medir antes de activar**: cada cambio de criterio o de proveedor se
  valida con `evals.py` sobre datos etiquetados antes de tocar producción;
  el prefiltro se validó primero en sombra sobre datos reales sin cambiar
  ninguna decisión.
