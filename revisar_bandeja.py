"""
revisar_bandeja.py — v3: tareas y plazos
=========================================

Flujo:
    0. Migración de datos: los importantes que se clasificaron con el esquema
       viejo (sin tarea/plazo) se re-clasifican una sola vez. Son pocos y
       baratos; los ruidos no se tocan.
    1-3. Igual que antes: ids de la semana -> filtrar conocidos -> procesar
       en streaming los nuevos.
    4. Informe, ahora en este orden:
         ⏰ TAREAS PENDIENTES  (por plazo; vencidas primero)
         ⭐ IMPORTANTES informativos (sin acción)
         🗑 RUIDO de esta ejecución
       Los duplicados (Canvas manda dobles) aparecen agrupados.

Para gestionar tareas (ver, completar, historial): python tareas.py

Uso:
    python revisar_bandeja.py
    python revisar_bandeja.py "is:unread"
    python revisar_bandeja.py "newer_than:7d" 300
    python revisar_bandeja.py --reclasificar     (tras cambiar CRITERIO: repasa los importantes)
"""

import sys
import time
from datetime import date, timedelta

import almacen
import gmail
from cerebro import clasificar

CONSULTA_POR_DEFECTO = "newer_than:7d"
MAX_EMAILS = 150
DIAS_INFORME = 7


def _fecha_corta(epoch: int) -> str:
    return time.strftime("%d/%m", time.localtime(epoch)) if epoch else "??/??"


def _etiqueta_plazo(fecha_limite: str | None, hoy: date) -> str:
    """🔴 vencida · ⚠️ esta semana · 📅 más adelante · ▫️ sin fecha"""
    if not fecha_limite:
        return "▫️ sin fecha "
    f = date.fromisoformat(fecha_limite)
    if f < hoy:
        return f"🔴 VENCIDA {f.strftime('%d/%m')}"
    if f <= hoy + timedelta(days=7):
        return f"⚠️  {f.strftime('%d/%m')} ({(f - hoy).days}d)"
    return f"📅 {f.strftime('%d/%m')}      "


def _reclasificar(ids: list[str], motivo: str):
    """Vuelve a pasar emails ya guardados por el cerebro (no toca 'completada')."""
    if not ids:
        return
    print(f"{motivo}: {len(ids)} emails. Re-clasificando...")
    for i, e in enumerate(gmail.iterar_por_ids(ids), 1):
        c = clasificar(e["remitente"], e["asunto"], e["cuerpo"],
                       fecha_email=date.fromtimestamp(e["fecha_epoch"]))
        almacen.actualizar_clasificacion(e["id"], c)
        if i % 10 == 0 or i == len(ids):
            print(f"  ...{i}/{len(ids)}")
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    consulta = args[0] if args else CONSULTA_POR_DEFECTO
    maximo = int(args[1]) if len(args) > 1 else MAX_EMAILS
    hoy = date.today()

    almacen.inicializar()

    # Migración (una vez) o reclasificación completa (cuando cambias el criterio):
    #   python revisar_bandeja.py --reclasificar
    if "--reclasificar" in flags:
        _reclasificar(almacen.ids_importantes(), "Reclasificación por cambio de criterio")
    else:
        _reclasificar(almacen.ids_importantes_sin_migrar(), "Migración de importantes antiguos")

    print(f"Consultando Gmail ('{consulta}', máx {maximo})...")
    ids = gmail.listar_ids(consulta, maximo)
    conocidos = almacen.ids_conocidos(ids)
    nuevos_ids = [i for i in ids if i not in conocidos]
    print(f"{len(ids)} emails en la consulta · {len(conocidos)} ya clasificados · "
          f"{len(nuevos_ids)} nuevos por procesar\n")

    nuevos_ruido = []
    total = len(nuevos_ids)
    if total:
        for i, e in enumerate(gmail.iterar_por_ids(nuevos_ids), 1):
            c = clasificar(e["remitente"], e["asunto"], e["cuerpo"],
                           fecha_email=date.fromtimestamp(e["fecha_epoch"]), hoy=hoy)
            almacen.guardar(e, c)
            if not c.importante:
                nuevos_ruido.append((e, c))
            if i % 10 == 0 or i == total:
                print(f"  ...clasificados {i}/{total}")
        print()

    set_nuevos = set(nuevos_ids)

    # ── ⏰ Tareas pendientes ─────────────────────────────────────────────
    tareas = almacen.tareas_pendientes()
    print("=" * 70)
    print(f"⏰ TAREAS PENDIENTES ({len(tareas)}) · 🆕 = nueva hoy")
    print("=" * 70)
    for n, t in enumerate(tareas, 1):
        nuevo = "🆕" if any(i in set_nuevos for i in t["ids"]) else "  "
        dup = f" (x{t['repeticiones']})" if t["repeticiones"] > 1 else ""
        print(f"{n:>3}. {nuevo} {_etiqueta_plazo(t['fecha_limite'], hoy)} · {t['accion'] or t['asunto']}{dup}")
        print(f"          [{t['prioridad']}/{t['categoria']}] {t['asunto'][:70]}")
        print(f"          → {t['resumen']}\n")
    if tareas:
        print("   Marca una como hecha:  python tareas.py completar N\n")

    # ── ⭐ Importantes sin acción ───────────────────────────────────────
    info = almacen.importantes_sin_accion(DIAS_INFORME)
    print("=" * 70)
    print(f"⭐ IMPORTANTES informativos · últimos {DIAS_INFORME} días ({len(info)})")
    print("=" * 70)
    for f in info:
        nuevo = "🆕" if any(i in set_nuevos for i in f["ids"]) else "  "
        dup = f" (x{f['repeticiones']})" if f["repeticiones"] > 1 else ""
        print(f"{nuevo} [{f['prioridad'].upper():5}] [{f['categoria']}] {_fecha_corta(f['fecha_epoch'])} · {f['asunto'][:70]}{dup}")
        print(f"          → {f['resumen']}\n")

    # ── 🗑 Ruido nuevo ──────────────────────────────────────────────────
    if nuevos_ruido:
        print("=" * 70)
        print(f"🗑  RUIDO de esta ejecución ({len(nuevos_ruido)}) — revisa por si se coló algo")
        print("=" * 70)
        for e, c in nuevos_ruido:
            print(f"   · {e['asunto'][:58]:<58}  [{c.motivo[:45]}]")

    s = almacen.estadisticas()
    print(f"\nBD: {s['total']} emails · {s['importantes']} importantes · "
          f"{s['pendientes']} tareas pendientes · {s['completadas']} completadas")


if __name__ == "__main__":
    main()
