"""
revisar_bandeja.py — CLI (interfaz de consola)
==============================================

Ya no contiene lógica: solo parsea argumentos, llama a servicio.revisar()
y PINTA. Toda la inteligencia está en servicio.py, compartida con api.py.

Uso:
    python revisar_bandeja.py
    python revisar_bandeja.py "is:unread"
    python revisar_bandeja.py "newer_than:7d" 300
    python revisar_bandeja.py --reclasificar     (tras cambiar CRITERIO)
"""

import sys
import time
from datetime import date, timedelta

import almacen
import servicio


def _fecha_corta(epoch: int) -> str:
    return time.strftime("%d/%m", time.localtime(epoch)) if epoch else "??/??"


def _etiqueta_plazo(fecha_limite: str | None, hoy: date) -> str:
    if not fecha_limite:
        return "▫️ sin fecha "
    f = date.fromisoformat(fecha_limite)
    if f < hoy:
        return f"🔴 VENCIDA {f.strftime('%d/%m')}"
    if f <= hoy + timedelta(days=7):
        return f"⚠️  {f.strftime('%d/%m')} ({(f - hoy).days}d)"
    return f"📅 {f.strftime('%d/%m')}      "


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    consulta = args[0] if args else "newer_than:7d"
    maximo = int(args[1]) if len(args) > 1 else 150
    hoy = date.today()

    print(f"Revisando Gmail ('{consulta}', máx {maximo})...")
    r = servicio.revisar(consulta, maximo,
                         reclasificar_todo="--reclasificar" in flags,
                         progreso=lambda m: print(f"  ...{m}"))

    if r["cambios_reclasificacion"]:
        etiqueta = {"importante": ("ruido", "IMPORTANTE"), "tarea": ("informativo", "TAREA")}
        print(f"\n  Cambios respecto a la clasificación anterior ({len(r['cambios_reclasificacion'])}):")
        for c in r["cambios_reclasificacion"]:
            e = etiqueta[c["campo"]]
            print(f"    · {e[c['antes']]} → {e[c['ahora']]:<11} {c['asunto'][:60]}")

    print(f"\n{r['en_gmail']} emails en la consulta · {r['ya_clasificados']} ya clasificados · "
          f"{r['nuevos']} nuevos ({len(r['nuevos_importantes'])} importantes)\n")
    set_nuevos = {x["id"] for x in r["nuevos_importantes"]}

    # ── ⏰ Tareas pendientes ─────────────────────────────────────────────
    tareas = almacen.tareas_pendientes()
    print("=" * 70)
    print(f"⏰ TAREAS PENDIENTES ({len(tareas)}) · 🆕 = nueva hoy")
    print("=" * 70)
    for n, t in enumerate(tareas, 1):
        nuevo = "🆕" if any(i in set_nuevos for i in t["ids"]) else "  "
        dup = f" (x{t['repeticiones']})" if t["repeticiones"] > 1 else ""
        print(f"{n:>3}. {nuevo} {_etiqueta_plazo(t['fecha_limite'], hoy)} · {t['accion']}{dup}")
        print(f"          [{t['prioridad']}/{t['categoria']}] {t['asunto'][:70]}")
        print(f"          → {t['resumen']}\n")
    if tareas:
        print("   Marca una como hecha:  python tareas.py completar N\n")

    # ── ⭐ Importantes sin acción ───────────────────────────────────────
    info = almacen.importantes_sin_accion(7)
    print("=" * 70)
    print(f"⭐ IMPORTANTES informativos · últimos 7 días ({len(info)})")
    print("=" * 70)
    for f in info:
        nuevo = "🆕" if any(i in set_nuevos for i in f["ids"]) else "  "
        dup = f" (x{f['repeticiones']})" if f["repeticiones"] > 1 else ""
        print(f"{nuevo} [{f['prioridad'].upper():5}] [{f['categoria']}] {_fecha_corta(f['fecha_epoch'])} · {f['asunto'][:70]}{dup}")
        print(f"          → {f['resumen']}\n")

    # ── 🗑 Ruido nuevo ──────────────────────────────────────────────────
    if r["nuevos_ruido"]:
        print("=" * 70)
        print(f"🗑  RUIDO de esta ejecución ({len(r['nuevos_ruido'])}) — revisa por si se coló algo")
        print("=" * 70)
        for x in r["nuevos_ruido"]:
            print(f"   · {x['asunto'][:58]:<58}  [{x['motivo'][:45]}]")

    s = almacen.estadisticas()
    print(f"\nBD: {s['total']} emails · {s['importantes']} importantes · "
          f"{s['pendientes']} tareas pendientes · {s['completadas']} completadas · "
          f"{s['corregidos']} corregidos por ti")
    print(f"LLM ({r['modelo']}): {r['llamadas_llm']} llamadas · {r['tokens_llm']:,} tokens en esta ejecución"
          .replace(",", "."))
    if r["prefiltro_activo"] or r["errores_jev"]:
        print(f"Jev: {r['filtrados_por_jev']} de {r['nuevos']} resueltos sin LLM · {r['uso_jev']}")


if __name__ == "__main__":
    main()
