# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Versionado: mientras el proyecto no tenga un primer release estable, los
cambios se acumulan en `0.0.0`; a partir del primer release se sigue
[SemVer](https://semver.org/lang/es/).

## [0.0.0] — punto de partida documentado

Todo el trabajo hasta la fecha, sin releases intermedios etiquetados.

### Añadido

- **Clasificador base** (`cerebro.py`, `llm.py`): criterio personalizado
  (becas, eventos de informática/emprendimiento, comunicaciones de la UFV,
  ofertas afines al perfil, dinero legítimo vs. ruido/publicidad/phishing),
  salida estructurada con Pydantic, interfaz de proveedor de LLM intercambiable
  (Anthropic Claude / DeepSeek).
- **Extracción de tareas y plazos**: campos `requiere_accion`, `accion`,
  `fecha_limite` en la clasificación; los plazos relativos se resuelven contra
  la fecha de envío del email, no contra la fecha de ejecución. Guardrail de
  coherencia: una "tarea" sin acción concreta deja de considerarse tarea.
- **Lectura de Gmail** (`gmail.py`): OAuth de solo lectura, paginación,
  reintentos con backoff exponencial ante límites de la API, lectura
  adaptativa (texto corto para decidir, completo solo si resulta importante).
- **Persistencia** (`almacen.py`): SQLite con migraciones de esquema
  automáticas y no destructivas, estado de tarea (`pendiente`/`completada`),
  deduplicación de notificaciones dobles por ventana temporal, corrección
  humana como etiqueta de oro independiente del veredicto del modelo.
- **Capa de servicio** (`servicio.py`): casos de uso compartidos entre la CLI
  y la API, sin lógica duplicada.
- **CLI** (`revisar_bandeja.py`, `tareas.py`): revisión de bandeja con informe
  de tareas pendientes e importantes, gestión de tareas desde consola.
- **API REST** (`api.py`, FastAPI): tareas, importantes, ruido, corrección
  humana, revisión en segundo plano con estado consultable, estadísticas.
  Sin autenticación (uso en red local, no expuesta a internet).
- **Sistema de evaluación** (`evals.py`, `dataset_evals.json`): dataset público
  con casos reales anonimizados e inventados, mide exactitud de importancia,
  tarea, plazo y consistencia entre repeticiones; guarda resultados por
  proveedor/modelo para comparar.
- **Etiquetado en bandeja real** (`etiquetar.py`, `reparar_dataset.py`):
  herramienta de etiquetado a ciegas (sin ver el veredicto del modelo) sobre
  mi propio correo, para un dataset privado (`dataset_real.json`, fuera del
  repo) fiel a lo que a mí me importa y no a una idea externa de ello.
- **Prefiltro Jev** (`jev.py`, `politica.py`): dos preguntas tipadas y
  calibradas (probabilidad de importancia + categoría) como primera etapa de
  una cascada delante del LLM. Regla de decisión en `politica.py` (única
  fuente de verdad, la comparten producción, calibración y sombra): se
  descarta sin LLM solo si la probabilidad es baja **y** la categoría es
  descartable (`promocional`, `automatico`, `sospechoso`); el resto va
  siempre al LLM. Fallos del servicio no tumban la cascada (el email pasa al
  LLM), se cuentan y son visibles, con cortocircuito tras fallos consecutivos.
- **Calibración y validación del prefiltro** (`calibrar_jev.py`,
  `sombra_jev.py`): calibración sobre el dataset etiquetado (sin pérdidas
  hasta umbral 0.6 con la regla de dos señales) y validación en sombra sobre
  ~138 emails reales ya clasificados, sin cambiar ninguna decisión: ~75% de
  la bandeja resuelto sin LLM, 0 desacuerdos con el histórico del LLM al
  umbral elegido (0.25).

### Decisiones de diseño registradas

- Regla de oro del criterio: ante la duda, marcar importante (falso positivo
  barato, falso negativo caro).
- Prefiltro con dos señales independientes en vez de una sola probabilidad,
  para que un fallo aislado no cueste un email importante.
- Todo cambio de criterio o de proveedor se mide con evals antes de aplicarse
  en producción; el prefiltro se validó en sombra antes de activarse.

### Pendiente (próximas versiones)

- Cliente propio (app móvil, Expo + React Native) que consuma la API.
- Ampliar el dataset real etiquetado y recalibrar el prefiltro con él.
- Evaluar clasificador propio (ML) sobre remitentes recurrentes como capa
  adicional de ahorro, si el volumen real lo justifica.
  
## [Unreleased]

### Añadido
- Remitentes protegidos (`remitentes_protegidos.txt`): direcciones que siempre
  pasan al LLM; tercera defensa del prefiltro, añadida tras encontrar fallos
  reales de Jev en la prueba en sombra.
- `corregir.py`: corrige etiquetas en la BD y en el dataset real a la vez.

### Cambiado
- Criterio: eventos en cualquier país, viajes (itinerarios, seguros, gestión
  de reservas), confirmaciones de inscripción; excepción de tareas de DIS.
- Criterio de Jev sincronizado con el del cerebro; nueva categoría protegida `viaje`.