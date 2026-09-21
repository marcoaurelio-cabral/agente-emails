"""
calibrar_jev.py — ¿Qué regla y qué umbral usar en el prefiltro? (v2)
====================================================================

Pasa los casos etiquetados de dataset_evals.json por Jev y compara las DOS
reglas de politica.py (solo_noul vs dos_senales) en una sola tabla: para cada
umbral, falsos negativos (el error CARO) y ahorro de llamadas al LLM.

Los casos 'frontera' cuentan para el ahorro pero no como falsos negativos:
no hay respuesta correcta.

Uso:
    python calibrar_jev.py                 (llama a Jev, ~0,001 $)
    python calibrar_jev.py --guardar       (y escribe calibracion_jev.json)
    python calibrar_jev.py --desde-json    (recalcula con el JSON guardado,
                                            SIN llamar a Jev: útil para probar
                                            políticas sin gastar)

Cuándo volver a llamar a Jev: si cambias las preguntas o el estado en
jev.py, o la versión del modelo. Si solo cambias la política, --desde-json.
"""

import json
import sys
from pathlib import Path

import jev
import politica

DATASET = Path(__file__).parent / "dataset_evals.json"
SALIDA = Path(__file__).parent / "calibracion_jev.json"


def medir_con_jev(casos: list[dict]) -> tuple[list[dict], list[str]]:
    medidos, fallidos = [], []
    for c in casos:
        r = jev.evaluar(c["remitente"], c["asunto"], c["cuerpo"])
        if r is None:
            fallidos.append(c["id"])
            if jev.cortocircuito_abierto():
                break
            continue
        medidos.append({
            "id": c["id"], "esperado_importante": c["importante"],
            "frontera": c.get("frontera", False),
            "noul": r["noul"], "categoria": r["categoria"], "confianza": r["confianza"],
            "modelo": r["modelo"],
        })
        marca = "⭐" if c["importante"] else "  "
        print(f"{marca} p={r['noul']:.3f}  [{r['categoria']:<12}] {c['id']}")
    return medidos, fallidos


def main():
    desde_json = "--desde-json" in sys.argv

    if desde_json:
        if not SALIDA.exists():
            print(f"No existe {SALIDA.name}: ejecuta primero con --guardar.")
            return
        datos = json.loads(SALIDA.read_text(encoding="utf-8"))
        # Compatibilidad con el formato de la v1 del script (esperado / categoria_jev).
        medidos = [{
            **m,
            "esperado_importante": m.get("esperado_importante", m.get("esperado")),
            "categoria": m.get("categoria", m.get("categoria_jev")),
        } for m in datos["medidos"]]
        print(f"Recalculando con {SALIDA.name} (modelo {datos.get('modelo', '¿?')}) — sin llamar a Jev.\n")
    else:
        if not jev.disponible():
            print("Jev no disponible: falta TYPESAFE_API_KEY o el paquete typesafe-sdk.")
            return
        casos = json.loads(DATASET.read_text(encoding="utf-8"))
        print(f"Evaluando {len(casos)} casos etiquetados con Jev ({jev.MODELO})...\n")
        medidos, fallidos = medir_con_jev(casos)
        if fallidos:
            print(f"\n❌ Jev falló en {len(fallidos)} caso(s): {', '.join(fallidos[:5])}")
            print(f"   Último error: {jev.uso['ultimo_error']}")
            print(f"   Modelo pedido: {jev.MODELO}  (si no existe, cambia JEV_MODEL en el .env)")
            print("   Calibración ABORTADA: no se recomienda nada con datos incompletos.")
            return

    # 'esperado' = no debe perderse. Los frontera no cuentan como pérdida.
    items = [{"id": m["id"], "noul": m["noul"], "categoria": m["categoria"],
              "esperado": m["esperado_importante"] and not m["frontera"]} for m in medidos]

    claros = [m for m in medidos if not m["frontera"]]
    imp = [m for m in claros if m["esperado_importante"]]
    rui = [m for m in claros if not m["esperado_importante"]]
    print("\n" + "=" * 77)
    print("SEPARACIÓN (casos claros)")
    print("=" * 77)
    if imp:
        peor = min(imp, key=lambda m: m["noul"])
        print(f"  Importantes: p mínima {peor['noul']:.3f} ({peor['id']}, {peor['categoria']}) · "
              f"media {sum(m['noul'] for m in imp)/len(imp):.3f}")
        protegidos = [m for m in imp if m["categoria"] not in politica.CATEGORIAS_DESCARTABLES]
        expuestos = [m for m in imp if m["categoria"] in politica.CATEGORIAS_DESCARTABLES]
        print(f"               {len(protegidos)} en categorías protegidas · "
              f"{len(expuestos)} en categorías descartables"
              + (f" (el más bajo: {min(expuestos, key=lambda m: m['noul'])['id']}, "
                 f"p={min(m['noul'] for m in expuestos):.3f})" if expuestos else ""))
    if rui:
        peor_r = max(rui, key=lambda m: m["noul"])
        print(f"  Ruido:       p máxima {peor_r['noul']:.3f} ({peor_r['id']}) · "
              f"media {sum(m['noul'] for m in rui)/len(rui):.3f}")

    print("\n" + "=" * 77)
    print("UMBRALES  (FN = importantes que se perderían · ahorro = % sin LLM)")
    print("=" * 77)
    print(politica.informe_umbrales(items))
    print(f"\n  Política activa ahora: {politica.describir()}")

    if not desde_json:
        print(f"  Coste de esta calibración: {jev.resumen_uso()}")
        if "--guardar" in sys.argv:
            SALIDA.write_text(json.dumps({"modelo": medidos[0]["modelo"] if medidos else jev.MODELO,
                                          "medidos": medidos}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            print(f"  Guardado en {SALIDA.name}")


if __name__ == "__main__":
    main()
