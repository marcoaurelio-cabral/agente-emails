"""
tareas.py — Gestión de tareas desde la consola
===============================================

Es el prototipo del "botón de completar" de la app. Hoy es un comando; en
la app será un tap. La LÓGICA (qué significa completar, cómo se agrupan los
duplicados, qué se guarda) vive en almacen.py y es la misma para ambos.
Este archivo solo pinta y recoge la orden.

Uso:
    python tareas.py                 -> lista las pendientes numeradas
    python tareas.py completar 3     -> marca la nº 3 como hecha
    python tareas.py completar 1 4   -> varias a la vez
    python tareas.py hechas          -> historial de completadas (30 días)

Nota: el número es la posición en la lista de ESTA ejecución. Lista primero,
luego completa. (En la app se usará el id, no la posición.)
"""

import sys
from datetime import date, timedelta

import almacen


def _plazo(fecha_limite: str | None, hoy: date) -> str:
    if not fecha_limite:
        return "▫️ sin fecha "
    f = date.fromisoformat(fecha_limite)
    if f < hoy:
        return f"🔴 VENCIDA {f.strftime('%d/%m')}"
    if f <= hoy + timedelta(days=7):
        return f"⚠️  {f.strftime('%d/%m')} ({(f - hoy).days}d)"
    return f"📅 {f.strftime('%d/%m')}      "


def listar() -> list[dict]:
    hoy = date.today()
    tareas = almacen.tareas_pendientes()
    print(f"⏰ TAREAS PENDIENTES ({len(tareas)})\n")
    for n, t in enumerate(tareas, 1):
        dup = f" (x{t['repeticiones']})" if t["repeticiones"] > 1 else ""
        print(f"{n:>3}. {_plazo(t['fecha_limite'], hoy)} · {t['accion'] or t['asunto']}{dup}")
        print(f"          {t['asunto'][:72]}")
    if not tareas:
        print("   Nada pendiente. 🎉")
    return tareas


def completar(numeros: list[int]):
    tareas = almacen.tareas_pendientes()
    for n in numeros:
        if not 1 <= n <= len(tareas):
            print(f"✗ No hay tarea nº {n} (hay {len(tareas)}).")
            continue
        t = tareas[n - 1]
        hechas = almacen.marcar_completadas(t["ids"])
        print(f"✓ Completada: {t['accion'] or t['asunto']}"
              + (f" ({hechas} emails del grupo)" if hechas > 1 else ""))


def hechas():
    tareas = almacen.tareas_completadas(30)
    print(f"✅ COMPLETADAS · últimos 30 días ({len(tareas)})\n")
    for t in tareas:
        print(f"   ✓ {t['accion'] or t['asunto']}")


if __name__ == "__main__":
    almacen.inicializar()
    args = sys.argv[1:]
    if not args:
        listar()
    elif args[0] == "completar" and len(args) > 1:
        completar([int(a) for a in args[1:]])
    elif args[0] == "hechas":
        hechas()
    else:
        print(__doc__)
