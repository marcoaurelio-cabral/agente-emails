"""
evals.py — Tests del cerebro (v2, coherente con tareas y plazos)
=================================================================

Los tests unitarios de un sistema probabilístico. Cada vez que toques el
CRITERIO de cerebro.py o cambies de modelo/proveedor, corres esto. Si los
números bajan, tu "mejora" era una regresión.

Qué mide:
  IMPORTANTE  ¿acierta si el email importa o es ruido?  (casos claros)
              - falsos negativos (CAROS): importante que se fue al ruido
              - falsos positivos (molestos): ruido marcado importante
  TAREA       ¿acierta requiere_accion?  (solo en casos que lo especifican)
  PLAZO       ¿acierta fecha_limite?     (solo en casos que lo especifican;
              incluye plazos RELATIVOS: "este viernes", "in 7 days")
  CONSISTENCIA cada caso se corre REPETICIONES veces: ¿da siempre lo mismo?
  FRONTERA    casos sin respuesta correcta: se muestran, NO puntúan
  COSTE       tokens y $ de esta ejecución (para comparar modelos)

Reproducibilidad: cada caso lleva su fecha_email y "hoy" es una fecha FIJA.
Así los plazos relativos siempre significan lo mismo, corras cuando corras.

Uso:
    python evals.py            (REPETICIONES=2)
    python evals.py 1          (una repetición: más barato, sin consistencia)
    python evals.py 3          (más fino)
    $env:LLM_MODEL="claude-haiku-4-5-20251001"; python evals.py
    $env:LLM_PROVIDER="deepseek"; python evals.py
"""

import argparse
import json
import statistics
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import time

import cerebro
import servicio

DATASET = Path(__file__).parent / "dataset_evals.json"
CARPETA_RESULTADOS = Path(__file__).parent / "evals_resultados"
HOY_FIJO = date(2026, 9, 16)  # "hoy" congelado: los plazos relativos no cambian con el calendario


def etiqueta_proveedor() -> str:
    prov = cerebro._llm
    return f"{type(prov).__name__.replace('Provider', '')}:{prov.model}"


def evaluar_caso(caso: dict, repeticiones: int, hoy: date = HOY_FIJO) -> dict:
    fecha_email = date.fromisoformat(caso.get("fecha_email", hoy.isoformat()))
    # Mismo camino que producción (lectura adaptativa): se mide lo que se ejecuta,
    # y cuesta bastante menos que mandar siempre el email entero.
    e = {"remitente": caso["remitente"], "asunto": caso["asunto"], "cuerpo": caso["cuerpo"],
         "fecha_epoch": caso.get("fecha_epoch") or int(time.mktime(fecha_email.timetuple()))}
    e.update(para=caso.get("para", ""), cc=caso.get("cc", ""))
    salidas = [servicio.clasificar_llm(e, hoy, caso.get("rol", ""))
               for _ in range(repeticiones)]
    votos = [s.importante for s in salidas]
    moda, n_moda = Counter(votos).most_common(1)[0]
    s0 = salidas[0]

    r = {
        "id": caso["id"],
        "origen": caso.get("origen", "?"),
        "frontera": caso.get("frontera", False),
        "esperado": caso["importante"],
        "obtenido": moda,
        "votos": votos,
        "acierto": moda == caso["importante"],
        "consistencia": n_moda / repeticiones,
        "categoria_esperada": caso.get("categoria"),
        "categoria_obtenida": s0.categoria,
        "prioridad": s0.prioridad,
        "requiere_accion": s0.requiere_accion,
        "accion": s0.accion,
        "fecha_limite": s0.fecha_limite,
        "motivo": s0.motivo,
    }
    # Métricas de tarea/plazo solo si el caso las especifica
    if "requiere_accion" in caso:
        r["tarea_esperada"] = caso["requiere_accion"]
        r["tarea_ok"] = (s0.requiere_accion == caso["requiere_accion"])
    if "fecha_limite" in caso:
        r["fecha_esperada"] = caso["fecha_limite"]
        r["fecha_ok"] = (s0.fecha_limite == caso["fecha_limite"])
    return r


def _linea(r: dict) -> str:
    if r["frontera"]:
        icono = "🔶"
    elif r["acierto"]:
        icono = "✅"
    else:
        icono = "❌"
    partes = [f"imp={'✓' if r['acierto'] else '✗'}"]
    if "tarea_ok" in r:
        partes.append(f"tarea={'✓' if r['tarea_ok'] else '✗'}")
    if "fecha_ok" in r:
        partes.append(f"fecha={'✓' if r['fecha_ok'] else '✗ ' + (r['fecha_limite'] or '—')}")
    extra = "  ⚠️ inconsistente" if r["consistencia"] < 1.0 else ""
    return f"{icono} {r['id']:<32} {'  '.join(partes)}{extra}"


def main():
    parser = argparse.ArgumentParser(description="Evals del cerebro")
    parser.add_argument("repeticiones", nargs="?", type=int, default=2,
                        help="ejecuciones por caso (1 = más barato, sin medir consistencia)")
    parser.add_argument("--dataset", default=str(DATASET), help="ruta al dataset JSON")
    parser.add_argument("--solo-fallos", action="store_true",
                        help="repetir solo los casos que fallaron en la última ejecución (barato)")
    args = parser.parse_args()
    repeticiones = args.repeticiones
    dataset = Path(args.dataset)
    if not dataset.exists():
        print(f"No existe {dataset}.")
        return
    casos = json.loads(dataset.read_text(encoding="utf-8"))
    if args.solo_fallos:
        previos = sorted(CARPETA_RESULTADOS.glob(f"*_{dataset.stem}*.json"))
        if not previos:
            print(f"No hay ejecuciones previas de {dataset.name}: lanza primero el eval completo.")
            return
        ultimo = json.loads(previos[-1].read_text(encoding="utf-8"))
        fallidos = {r["id"] for r in ultimo["resultados"]
                    if (not r["frontera"] and not r["acierto"])
                    or r.get("tarea_ok") is False or r.get("fecha_ok") is False}
        casos = [c for c in casos if c["id"] in fallidos]
        print(f"Solo los {len(casos)} casos que fallaron en {previos[-1].name}.")
        print("Cuando dejen de fallar, lanza el eval COMPLETO para comprobar que no has roto otros.\n")
        if not casos:
            print("No quedan fallos. 🎉")
            return
    # Un dataset sin fecha_email mediría mal los plazos relativos (se calcularían
    # desde "hoy" y no desde el envío). Mejor avisar ANTES de gastar nada.
    sin_fecha = [c["id"] for c in casos if not c.get("fecha_email")]
    if sin_fecha:
        print(f"❌ {len(sin_fecha)} caso(s) sin fecha_email (p.ej. {sin_fecha[0]}).")
        print("   Los plazos se medirían mal. Si es dataset_real.json: python reparar_dataset.py")
        return
    # "Hoy" congelado en la fecha del email más reciente del dataset: los plazos
    # relativos significan siempre lo mismo, corras el eval cuando lo corras.
    fechas = [c["fecha_email"] for c in casos if c.get("fecha_email")]
    hoy = date.fromisoformat(max(fechas)) if fechas else HOY_FIJO
    proveedor = etiqueta_proveedor()
    print(f"Evals del cerebro | {dataset.name} | {proveedor} | {len(casos)} casos × {repeticiones} rep. | hoy={hoy}\n")

    resultados = []
    for caso in casos:
        try:
            r = evaluar_caso(caso, repeticiones, hoy)
        except Exception as e:
            print(f"💥 {caso['id']}: ERROR {e}")
            continue
        resultados.append(r)
        print(_linea(r))

    # ── Métricas ─────────────────────────────────────────────────────────
    claros = [r for r in resultados if not r["frontera"]]
    frontera = [r for r in resultados if r["frontera"]]
    aciertos = sum(r["acierto"] for r in claros)
    falsos_neg = [r for r in claros if r["esperado"] and not r["obtenido"]]
    falsos_pos = [r for r in claros if not r["esperado"] and r["obtenido"]]
    consistencia = statistics.mean(r["consistencia"] for r in resultados) if resultados else 0
    con_tarea = [r for r in resultados if "tarea_ok" in r]
    con_fecha = [r for r in resultados if "fecha_ok" in r]
    con_cat = [r for r in resultados if r["categoria_esperada"]]
    cat_ok = sum(1 for r in con_cat if r["categoria_obtenida"] == r["categoria_esperada"])

    print("\n" + "#" * 66)
    print(f"INFORME — {proveedor}")
    print("#" * 66)
    if not claros:
        print("  No hay casos claros evaluados (¿todos dieron error?). Nada que medir.")
        return
    print(f"  Importante (casos claros):  {aciertos}/{len(claros)}  ({aciertos/len(claros):.0%})")
    print(f"    falsos negativos (CAROS): {len(falsos_neg)}")
    for r in falsos_neg:
        print(f"        ✗ {r['id']}  → {r['motivo'][:64]}")
    print(f"    falsos positivos:         {len(falsos_pos)}")
    for r in falsos_pos:
        print(f"        ✗ {r['id']}  → {r['motivo'][:64]}")
    if con_tarea:
        t_ok = sum(r["tarea_ok"] for r in con_tarea)
        print(f"  Tarea (requiere_accion):    {t_ok}/{len(con_tarea)}  ({t_ok/len(con_tarea):.0%})")
        for r in con_tarea:
            if not r["tarea_ok"]:
                print(f"        ✗ {r['id']}: esperado={r['tarea_esperada']} obtenido={r['requiere_accion']}"
                      f"  accion='{r['accion']}'")
    if con_fecha:
        f_ok = sum(r["fecha_ok"] for r in con_fecha)
        print(f"  Plazo (fecha_limite):       {f_ok}/{len(con_fecha)}  ({f_ok/len(con_fecha):.0%})")
        for r in con_fecha:
            if not r["fecha_ok"]:
                print(f"        ✗ {r['id']}: esperado={r['fecha_esperada'] or '—'} obtenido={r['fecha_limite'] or '—'}")
    print(f"  Consistencia media:         {consistencia:.0%}")
    print(f"  Categoría (informativo):    {cat_ok}/{len(con_cat)} coinciden")

    if frontera:
        print("\n  Casos frontera (no puntúan, solo para ver el criterio):")
        for r in frontera:
            tarea = f" · tarea: {r['accion']}" if r["requiere_accion"] else ""
            print(f"      🔶 {r['id']:<28} → {'IMPORTANTE' if r['obtenido'] else 'ruido':<10}"
                  f" [{r['prioridad']}]{tarea}")

    print(f"\n  Coste de esta ejecución: {cerebro._llm.resumen_uso()}")

    # ── Guardar para comparar entre proveedores / versiones del criterio ──
    CARPETA_RESULTADOS.mkdir(exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M")
    sufijo = "_fallos" if args.solo_fallos else ""
    nombre = f"{marca}_{dataset.stem}{sufijo}_{proveedor.replace(':', '_').replace('/', '_')}.json"
    salida = {
        "proveedor": proveedor,
        "fecha": marca,
        "repeticiones": repeticiones,
        "resumen": {
            "importante": f"{aciertos}/{len(claros)}",
            "falsos_negativos": [r["id"] for r in falsos_neg],
            "falsos_positivos": [r["id"] for r in falsos_pos],
            "tarea": f"{sum(r['tarea_ok'] for r in con_tarea)}/{len(con_tarea)}" if con_tarea else None,
            "plazo": f"{sum(r['fecha_ok'] for r in con_fecha)}/{len(con_fecha)}" if con_fecha else None,
            "consistencia": consistencia,
            "uso": cerebro._llm.uso,
            "coste_usd": cerebro._llm.coste_estimado_usd(),
        },
        "resultados": resultados,
    }
    (CARPETA_RESULTADOS / nombre).write_text(
        json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Resultados guardados en evals_resultados/{nombre}")


if __name__ == "__main__":
    main()
