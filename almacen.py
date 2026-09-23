"""
almacen.py — Memoria del agente (SQLite local) — v2
===================================================

Novedades v2:
  - TAREAS: columnas requiere_accion, accion, fecha_limite, estado y
    completada_en. Una tarea nace 'pendiente' y pasa a 'completada' cuando
    Rodri la marca (desde tareas.py hoy, desde un botón en la app mañana).
  - MIGRACIÓN AUTOMÁTICA: tu emails.db ya tiene 138 filas con el esquema
    viejo. inicializar() detecta las columnas que faltan y las AÑADE con
    ALTER TABLE, sin borrar nada. Así es como se evoluciona un esquema en
    producción: nunca "borra la BD y empieza de cero".
  - AGRUPADO: Canvas manda notificaciones duplicadas. Las consultas de
    tareas agrupan por (asunto normalizado, remitente) y devuelven una sola
    entrada con todos los ids del grupo. Completar una completa el grupo.

Este módulo sigue sin saber de Gmail ni de LLMs.
"""

import re
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "emails.db"

_ORDEN_PRIORIDAD = "CASE prioridad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END"

# Columnas de la tabla. Si en el futuro añades una, la migración la crea sola.
_COLUMNAS = {
    "gmail_id":        "TEXT PRIMARY KEY",
    "remitente":       "TEXT",
    "asunto":          "TEXT",
    "fecha_epoch":     "INTEGER",
    "importante":      "INTEGER NOT NULL DEFAULT 0",
    "categoria":       "TEXT",
    "prioridad":       "TEXT",
    "motivo":          "TEXT",
    "resumen":         "TEXT",
    "clasificado_en":  "INTEGER",
    # --- v2: tareas ---
    "requiere_accion": "INTEGER",   # NULL = clasificado con el esquema viejo (pendiente de migrar)
    "accion":          "TEXT",
    "fecha_limite":    "TEXT",      # 'AAAA-MM-DD' o NULL. ISO ordena bien como texto.
    "estado":          "TEXT",      # 'pendiente' | 'completada' | NULL (no es tarea)
    "completada_en":   "INTEGER",
    # --- v3: corrección humana (etiquetas de oro) ---
    "correccion_importante": "INTEGER",  # NULL = sin corregir; 1/0 = lo que dijo Marco
    "corregido_en":          "INTEGER",
    # --- v4: prefiltro Jev (guardamos la probabilidad, no solo la etiqueta,
    #         para poder calibrar umbrales y auditar después) ---
    "jev_noul":      "REAL",     # p(importante) según Jev
    "jev_categoria": "TEXT",
    "jev_modelo":    "TEXT",     # versión real que respondió
    "decidido_por":  "TEXT",     # 'jev' (prefiltro) | 'llm'
    # --- v5: memoria entre emails ---
    "cerrada_por":   "TEXT",     # gmail_id del email que cerró la tarea automáticamente
    # --- v6: agrupación (claves que extrae el modelo; el grupo lo decide agrupar.py) ---
    "tipo_entidad":   "TEXT",
    "entidad":        "TEXT",
    "fecha_entidad":  "TEXT",
    "referencia":     "TEXT",
    "confirma_hecho": "INTEGER",
    "grupo":          "TEXT",    # gmail_id del email más antiguo de su grupo
}

# "Importante efectivo": si Marco corrigió, manda su corrección; si no, el modelo.
# Se usa en todas las vistas para que una corrección se refleje al instante.
_IMPORTANTE = "COALESCE(correccion_importante, importante)"


def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar():
    """Crea la tabla si no existe y AÑADE las columnas que falten (migración)."""
    with _conectar() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS emails (gmail_id TEXT PRIMARY KEY)"
        )
        existentes = {f["name"] for f in conn.execute("PRAGMA table_info(emails)")}
        for nombre, tipo in _COLUMNAS.items():
            if nombre not in existentes:
                # ALTER TABLE no admite PRIMARY KEY ni NOT NULL sin default;
                # para columnas nuevas basta con el tipo base.
                tipo_base = tipo.split(" NOT NULL")[0].replace(" PRIMARY KEY", "")
                conn.execute(f"ALTER TABLE emails ADD COLUMN {nombre} {tipo_base}")
                print(f"  [BD] migración: añadida columna '{nombre}'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fecha ON emails (fecha_epoch)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_estado ON emails (estado, fecha_limite)")
        # Reparación de datos: 'pendiente' solo tiene sentido si es tarea.
        # Limpia zombis que pudieran quedar de versiones anteriores.
        cur = conn.execute(
            "UPDATE emails SET estado = NULL WHERE estado = 'pendiente' AND requiere_accion = 0"
        )
        if cur.rowcount:
            print(f"  [BD] reparación: {cur.rowcount} tareas zombi limpiadas")


# ── Escritura ───────────────────────────────────────────────────────────────

def _campos_clasificacion(c) -> dict:
    """Traduce un objeto Clasificacion a columnas."""
    return {
        "importante": int(c.importante),
        "categoria": c.categoria,
        "prioridad": c.prioridad,
        "motivo": c.motivo,
        "resumen": c.resumen,
        "requiere_accion": int(c.requiere_accion),
        "accion": c.accion or None,
        "fecha_limite": c.fecha_limite or None,
        "tipo_entidad": getattr(c, "tipo_entidad", None),
        "entidad": getattr(c, "entidad", None) or None,
        "fecha_entidad": getattr(c, "fecha_entidad", None) or None,
        "referencia": getattr(c, "referencia", None) or None,
        "confirma_hecho": int(bool(getattr(c, "confirma_hecho", False))),
    }


def guardar(email: dict, c, pre: dict | None = None) -> None:
    """Inserta un email nuevo con su clasificación. Idempotente (OR IGNORE).
    `pre` es el resultado del prefiltro Jev, si se usó."""
    campos = _campos_clasificacion(c)
    if pre:
        campos.update({"jev_noul": pre["noul"], "jev_categoria": pre["categoria"],
                       "jev_modelo": pre.get("modelo")})
    campos["decidido_por"] = (pre or {}).get("decidido_por", "llm")
    campos.update({
        "gmail_id": email["id"],
        "remitente": email["remitente"],
        "asunto": email["asunto"],
        "fecha_epoch": email["fecha_epoch"],
        "clasificado_en": int(time.time()),
        "estado": "pendiente" if c.requiere_accion else None,
    })
    cols = ", ".join(campos)
    marcas = ", ".join("?" * len(campos))
    with _conectar() as conn:
        conn.execute(f"INSERT OR IGNORE INTO emails ({cols}) VALUES ({marcas})",
                     list(campos.values()))


def actualizar_clasificacion(gmail_id: str, c) -> dict:
    """Re-clasifica un email ya guardado. Devuelve los cambios relevantes
    ({'importante': (antes, ahora)}, {'tarea': (antes, ahora)}) para auditar
    qué mueve un cambio de criterio.

    Coherencia de estado:
      - pasa a tarea   -> estado 'pendiente' (si no tenía)
      - deja de serlo  -> se limpia 'pendiente' (una 'completada' se respeta:
                          es historial de algo que Rodri hizo)
    """
    campos = _campos_clasificacion(c)
    campos["clasificado_en"] = int(time.time())
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    with _conectar() as conn:
        antes = conn.execute(
            "SELECT importante, requiere_accion FROM emails WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        conn.execute(f"UPDATE emails SET {asignaciones} WHERE gmail_id = ?",
                     [*campos.values(), gmail_id])
        if c.requiere_accion:
            conn.execute("UPDATE emails SET estado = 'pendiente' WHERE gmail_id = ? AND estado IS NULL",
                         (gmail_id,))
        else:
            conn.execute("UPDATE emails SET estado = NULL WHERE gmail_id = ? AND estado = 'pendiente'",
                         (gmail_id,))

    cambios = {}
    if antes is not None:
        if bool(antes["importante"]) != c.importante:
            cambios["importante"] = (bool(antes["importante"]), c.importante)
        if antes["requiere_accion"] is not None and bool(antes["requiere_accion"]) != c.requiere_accion:
            cambios["tarea"] = (bool(antes["requiere_accion"]), c.requiere_accion)
    return cambios


def marcar_completadas(ids: list[str]) -> int:
    with _conectar() as conn:
        cur = conn.execute(
            f"""UPDATE emails SET estado = 'completada', completada_en = ?
                WHERE gmail_id IN ({",".join("?" * len(ids))}) AND estado = 'pendiente'""",
            [int(time.time()), *ids],
        )
        return cur.rowcount


def reabrir(ids: list[str]) -> int:
    """Deshace un 'completar', manual o automático."""
    with _conectar() as conn:
        cur = conn.execute(
            f"""UPDATE emails SET estado = 'pendiente', completada_en = NULL, cerrada_por = NULL
                WHERE gmail_id IN ({",".join("?" * len(ids))}) AND estado = 'completada'""",
            ids,
        )
        return cur.rowcount


def cerrar_auto(ids: list[str], por: str) -> int:
    """Cierra tareas porque un email posterior demuestra que están hechas.
    Queda registrado QUIÉN la cerró (cerrada_por) y se puede reabrir."""
    if not ids:
        return 0
    with _conectar() as conn:
        cur = conn.execute(
            f"""UPDATE emails SET estado = 'completada', completada_en = ?, cerrada_por = ?
                WHERE gmail_id IN ({",".join("?" * len(ids))}) AND estado = 'pendiente'""",
            [int(time.time()), por, *ids],
        )
        return cur.rowcount


def filas_para_agrupar(dias: int = 60) -> list[dict]:
    """Emails recientes con claves de agrupación (importantes o con tarea)."""
    desde = int(time.time()) - dias * 86400
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT gmail_id, asunto, fecha_epoch, tipo_entidad, entidad, fecha_entidad,
                       referencia, confirma_hecho, estado
                FROM emails
                WHERE fecha_epoch >= ? AND tipo_entidad IS NOT NULL
                  AND ({_IMPORTANTE} = 1 OR confirma_hecho = 1)""",
            (desde,),
        ).fetchall()
    return [dict(f) for f in filas]


def guardar_grupos(grupos: dict[str, str]) -> None:
    with _conectar() as conn:
        conn.executemany("UPDATE emails SET grupo = ? WHERE gmail_id = ?",
                         [(g, gid) for gid, g in grupos.items()])


def tareas_cerradas_auto(dias: int = 7) -> list[dict]:
    desde = int(time.time()) - dias * 86400
    with _conectar() as conn:
        filas = conn.execute(
            """SELECT t.gmail_id, t.accion, t.asunto, c.asunto AS cerrada_por_asunto
               FROM emails t LEFT JOIN emails c ON c.gmail_id = t.cerrada_por
               WHERE t.cerrada_por IS NOT NULL AND t.completada_en >= ?
               ORDER BY t.completada_en DESC""", (desde,)).fetchall()
    return [dict(f) for f in filas]


def corregir(gmail_id: str, importante: bool) -> bool:
    """Marco corrige al modelo: "esto SÍ/NO era importante".

    La corrección se guarda APARTE de la etiqueta del modelo: no se pierde
    aunque re-clasifiques, y es una etiqueta de ORO para el dataset de evals
    (y para entrenar un clasificador propio el día que toque).
    Efecto inmediato en las vistas: si deja de ser importante, deja de ser
    tarea pendiente; si pasa a serlo y tenía acción, vuelve a pendiente.
    """
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE emails SET correccion_importante = ?, corregido_en = ? WHERE gmail_id = ?",
            (int(importante), int(time.time()), gmail_id),
        )
        if cur.rowcount == 0:
            return False
        if importante:
            conn.execute("""UPDATE emails SET estado = 'pendiente'
                            WHERE gmail_id = ? AND requiere_accion = 1 AND estado IS NULL""", (gmail_id,))
        else:
            conn.execute("""UPDATE emails SET estado = NULL
                            WHERE gmail_id = ? AND estado = 'pendiente'""", (gmail_id,))
        return True


def correcciones() -> list[dict]:
    """Etiquetas de oro: emails donde Marco corrigió al modelo (o lo confirmó)."""
    with _conectar() as conn:
        filas = conn.execute(
            """SELECT gmail_id, remitente, asunto, fecha_epoch, categoria,
                      importante AS modelo, correccion_importante AS humano, corregido_en
               FROM emails WHERE correccion_importante IS NOT NULL
               ORDER BY corregido_en DESC"""
        ).fetchall()
    return [dict(f) for f in filas]


def ruido_reciente(dias: int = 7, limite: int = 200) -> list[dict]:
    """Ruido de los últimos N días (para revisarlo desde la app y corregir
    falsos negativos: la pantalla de 'papelera')."""
    desde = int(time.time()) - dias * 24 * 3600
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT gmail_id, remitente, asunto, fecha_epoch, categoria, motivo, correccion_importante
                FROM emails WHERE {_IMPORTANTE} = 0 AND fecha_epoch >= ?
                ORDER BY fecha_epoch DESC LIMIT ?""",
            (desde, limite),
        ).fetchall()
    return [dict(f) for f in filas]


def guardar_prefiltro(gmail_id: str, pre: dict) -> None:
    """Guarda la opinión de Jev SIN cambiar la decisión del email (prueba en
    sombra). Así esos datos quedan para futuras calibraciones."""
    with _conectar() as conn:
        conn.execute("UPDATE emails SET jev_noul = ?, jev_categoria = ?, jev_modelo = ? WHERE gmail_id = ?",
                     (pre["noul"], pre["categoria"], pre.get("modelo"), gmail_id))


def metadatos(ids: list[str]) -> dict[str, dict]:
    """fecha_epoch y resumen del LLM de cada id (para reparar datasets)."""
    res = {}
    with _conectar() as conn:
        for i in range(0, len(ids), 500):
            bloque = ids[i:i + 500]
            for f in conn.execute(
                f"SELECT gmail_id, fecha_epoch, resumen FROM emails WHERE gmail_id IN ({','.join('?' * len(bloque))})",
                bloque,
            ):
                res[f["gmail_id"]] = dict(f)
    return res


def candidatos_etiquetado() -> list[dict]:
    """Emails para que Marco los etiquete, ORDENADOS por lo que más enseñan:
       1. desacuerdos Jev/LLM (uno dice importante y el otro no)
       2. los que el LLM marcó importantes
       3. los que Jev dudaba (0.2 <= p < 0.8)
       4. el resto (ruido evidente, rápido de etiquetar)
    Así, si paras a mitad, lo que llevas hecho es lo más útil."""
    with _conectar() as conn:
        filas = conn.execute(
            """SELECT gmail_id, asunto, remitente, fecha_epoch, importante, correccion_importante,
                      jev_noul, jev_categoria
               FROM emails ORDER BY fecha_epoch DESC"""
        ).fetchall()

    def prioridad(f) -> int:
        llm, p = bool(f["importante"]), f["jev_noul"]
        if p is not None and llm != (p >= 0.5):
            return 0
        if llm:
            return 1
        if p is not None and 0.2 <= p < 0.8:
            return 2
        return 3

    return sorted((dict(f) for f in filas), key=prioridad)


def emails_para_sombra() -> list[dict]:
    """Emails con una etiqueta de referencia fiable, y la opinión guardada de Jev.

    Referencia: tu corrección si la hay; si no, la del LLM. Se excluyen los que
    Jev descartó en producción SIN corrección tuya (su "etiqueta" sería la del
    propio Jev: medirlo contra sí mismo es circular). Pero si corregiste uno de
    esos, SÍ entra: es exactamente un fallo del prefiltro que hay que contar.
    """
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT gmail_id, asunto, remitente, {_IMPORTANTE} AS importante_ref,
                       correccion_importante, jev_noul, jev_categoria, decidido_por
                FROM emails
                WHERE decidido_por IS NULL OR decidido_por = 'llm'
                   OR correccion_importante IS NOT NULL
                ORDER BY fecha_epoch DESC"""
        ).fetchall()
    return [dict(f) for f in filas]


# ── Lectura ─────────────────────────────────────────────────────────────────

def ids_conocidos(ids: list[str]) -> set[str]:
    conocidos = set()
    with _conectar() as conn:
        for i in range(0, len(ids), 500):
            bloque = ids[i:i + 500]
            filas = conn.execute(
                f"SELECT gmail_id FROM emails WHERE gmail_id IN ({','.join('?' * len(bloque))})",
                bloque,
            ).fetchall()
            conocidos.update(f["gmail_id"] for f in filas)
    return conocidos


def ids_importantes_sin_migrar() -> list[str]:
    """Importantes clasificados con el esquema viejo (sin datos de tarea)."""
    with _conectar() as conn:
        return [f["gmail_id"] for f in conn.execute(
            "SELECT gmail_id FROM emails WHERE importante = 1 AND requiere_accion IS NULL"
        )]


def ids_importantes() -> list[str]:
    """Todos los importantes (para re-clasificarlos tras cambiar el criterio)."""
    with _conectar() as conn:
        return [f["gmail_id"] for f in conn.execute(
            "SELECT gmail_id FROM emails WHERE importante = 1"
        )]


def _clave_grupo(asunto: str, remitente: str) -> tuple[str, str]:
    """Normaliza para agrupar duplicados: quita Re:/Fwd:, espacios, mayúsculas."""
    a = re.sub(r"^\s*((re|fwd?|rv)\s*:\s*)+", "", asunto or "", flags=re.I)
    a = re.sub(r"\s+", " ", a).strip().lower()
    r = (remitente or "").strip().lower()
    return a, r


VENTANA_DUPLICADO = 3600  # segundos. Dobles reales (Canvas) llegan con segundos de diferencia.


def _agrupar(filas) -> list[dict]:
    """Colapsa duplicados reales en una entrada, conservando todos los ids.

    Dos filas son "el mismo email" si coinciden asunto+remitente Y llegaron
    con menos de VENTANA_DUPLICADO entre sí. La segunda condición evita
    fusionar asuntos genéricos que se repiten con días de diferencia
    ("Notificaciones recientes de Canvas" del 12 y del 15 son emails distintos).
    """
    grupos: list[dict] = []
    indice: dict[tuple, list[dict]] = {}
    for f in filas:
        if "grupo" in f.keys() and f["grupo"]:
            # Agrupados por agrupar.py: un solo representante, el más reciente.
            previo = indice.get(("g", f["grupo"]))
            if previo is None:
                nuevo = {**dict(f), "ids": [f["gmail_id"]], "repeticiones": 1}
                grupos.append(nuevo)
                indice[("g", f["grupo"])] = [nuevo]
            else:
                g = previo[0]
                g["ids"].append(f["gmail_id"])
                g["repeticiones"] += 1
                if (f["fecha_epoch"] or 0) > (g["fecha_epoch"] or 0):
                    ids, rep = g["ids"], g["repeticiones"]
                    g.clear(); g.update({**dict(f), "ids": ids, "repeticiones": rep})
            continue
        clave = _clave_grupo(f["asunto"], f["remitente"])
        candidatos = indice.setdefault(clave, [])
        destino = next(
            (g for g in candidatos if abs(g["fecha_epoch"] - f["fecha_epoch"]) <= VENTANA_DUPLICADO),
            None,
        )
        if destino is None:
            nuevo = {**dict(f), "ids": [f["gmail_id"]], "repeticiones": 1}
            grupos.append(nuevo)
            candidatos.append(nuevo)
        else:
            destino["ids"].append(f["gmail_id"])
            destino["repeticiones"] += 1
    return grupos


def tareas_pendientes() -> list[dict]:
    """Tareas pendientes agrupadas, ordenadas: con fecha primero (más cercana
    antes), luego sin fecha; a igualdad, por prioridad.
    Exige requiere_accion = 1 además de estado = 'pendiente': defensa en
    profundidad contra estados inconsistentes."""
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT * FROM emails
                WHERE estado = 'pendiente' AND requiere_accion = 1 AND {_IMPORTANTE} = 1
                ORDER BY (fecha_limite IS NULL), fecha_limite, {_ORDEN_PRIORIDAD}, fecha_epoch DESC"""
        ).fetchall()
    return _agrupar(filas)


def tareas_completadas(dias: int = 30) -> list[dict]:
    desde = int(time.time()) - dias * 24 * 3600
    with _conectar() as conn:
        filas = conn.execute(
            """SELECT * FROM emails WHERE estado = 'completada' AND completada_en >= ?
               ORDER BY completada_en DESC""",
            (desde,),
        ).fetchall()
    return _agrupar(filas)


def importantes_sin_accion(dias: int = 7) -> list[dict]:
    """Importantes informativos (sin tarea) de los últimos N días, agrupados."""
    desde = int(time.time()) - dias * 24 * 3600
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT * FROM emails
                WHERE {_IMPORTANTE} = 1 AND fecha_epoch >= ?
                  AND (requiere_accion = 0 OR requiere_accion IS NULL)
                ORDER BY {_ORDEN_PRIORIDAD}, fecha_epoch DESC""",
            (desde,),
        ).fetchall()
    return _agrupar(filas)


def estadisticas() -> dict:
    with _conectar() as conn:
        total, importantes, pendientes, completadas, corregidos = conn.execute(
            f"""SELECT COUNT(*), COALESCE(SUM({_IMPORTANTE}), 0),
                       COALESCE(SUM(estado = 'pendiente'), 0),
                       COALESCE(SUM(estado = 'completada'), 0),
                       COALESCE(SUM(correccion_importante IS NOT NULL), 0)
                FROM emails"""
        ).fetchone()
    return {"total": total, "importantes": importantes, "ruido": total - importantes,
            "pendientes": pendientes, "completadas": completadas, "corregidos": corregidos}


if __name__ == "__main__":
    inicializar()
    print(f"BD lista en {DB_PATH}")
    print(estadisticas())
