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
}


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
    }


def guardar(email: dict, c) -> None:
    """Inserta un email nuevo con su clasificación. Idempotente (OR IGNORE)."""
    campos = _campos_clasificacion(c)
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


def actualizar_clasificacion(gmail_id: str, c) -> None:
    """Re-clasifica un email ya guardado (usado en la migración de datos).
    No toca 'estado' si ya estaba completada."""
    campos = _campos_clasificacion(c)
    campos["clasificado_en"] = int(time.time())
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    with _conectar() as conn:
        conn.execute(f"UPDATE emails SET {asignaciones} WHERE gmail_id = ?",
                     [*campos.values(), gmail_id])
        conn.execute(
            """UPDATE emails SET estado = 'pendiente'
               WHERE gmail_id = ? AND requiere_accion = 1 AND estado IS NULL""",
            (gmail_id,),
        )


def marcar_completadas(ids: list[str]) -> int:
    with _conectar() as conn:
        cur = conn.execute(
            f"""UPDATE emails SET estado = 'completada', completada_en = ?
                WHERE gmail_id IN ({",".join("?" * len(ids))}) AND estado = 'pendiente'""",
            [int(time.time()), *ids],
        )
        return cur.rowcount


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
    antes), luego sin fecha; a igualdad, por prioridad."""
    with _conectar() as conn:
        filas = conn.execute(
            f"""SELECT * FROM emails
                WHERE estado = 'pendiente'
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
                WHERE importante = 1 AND fecha_epoch >= ?
                  AND (requiere_accion = 0 OR requiere_accion IS NULL)
                ORDER BY {_ORDEN_PRIORIDAD}, fecha_epoch DESC""",
            (desde,),
        ).fetchall()
    return _agrupar(filas)


def estadisticas() -> dict:
    with _conectar() as conn:
        total, importantes, pendientes, completadas = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(importante), 0),
                      COALESCE(SUM(estado = 'pendiente'), 0),
                      COALESCE(SUM(estado = 'completada'), 0)
               FROM emails"""
        ).fetchone()
    return {"total": total, "importantes": importantes, "ruido": total - importantes,
            "pendientes": pendientes, "completadas": completadas}


if __name__ == "__main__":
    inicializar()
    print(f"BD lista en {DB_PATH}")
    print(estadisticas())
