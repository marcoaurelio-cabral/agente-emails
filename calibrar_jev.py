"""
calibrar_jev.py — ¿Qué umbral usar en el prefiltro?
====================================================

La pregunta: "por debajo de qué p(importante) puedo descartar un email sin
llamar al LLM, sin esconderme nunca una beca". No se responde a ojo: se mide.

Qué hace:
  1. Pasa los 32 casos etiquetados de dataset_evals.json por Jev (1 llamada
     por caso, céntimos) y guarda la probabilidad de cada uno.
  2. Barre umbrales de 0.01 a 0.50 y para cada uno calcula:
       - FALSOS NEGATIVOS: importantes que se habrían descartado (error CARO)
       - AHORRO: % de emails que no habrían llegado al LLM
  3. Recomienda el umbral más alto con CERO falsos negativos, con margen.

Esto es exactamente la curva de compromiso recall/coste de una cascada. El
umbral no es una constante mágica: es una decisión medida sobre TUS datos.

Uso:
    python calibrar_jev.py
    python calibrar_jev.py --guardar    (escribe calibracion_jev.json)
"""

import json
import sys
from pathlib import Path

import jev

DATASET = Path(__file__).parent / "dataset_evals.json"
SALIDA = Path(__file__).parent / "calibracion_jev.json"
UMBRALES = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]


def main():
    if not jev.disponible():
        print("Jev no disponible: falta TYPESAFE_API_KEY o el paquete typesafe-sdk.")
        print("  pip install typesafe-sdk   y añade TYPESAFE_API_KEY=... a tu .env")
        return

    casos = json.loads(DATASET.read_text(encoding="utf-8"))
    print(f"Evaluando {len(casos)} casos etiquetados con Jev...\n")

    medidos, fallidos = [], []
    for c in casos:
        r = jev.evaluar(c["remitente"], c["asunto"], c["cuerpo"])
        if r is None:
            fallidos.append(c["id"])
            if jev.cortocircuito_abierto():
                break
            continue
        medidos.append({
            "id": c["id"], "esperado": c["importante"], "frontera": c.get("frontera", False),
            "noul": r["noul"], "categoria_jev": r["categoria"], "confianza": r["confianza"],
            "modelo": r["modelo"],
        })
        marca = "⭐" if c["importante"] else "  "
        print(f"{marca} p={r['noul']:.3f}  [{r['categoria']:<12}] {c['id']}")

    # Una calibración con huecos no vale: el umbral saldría de datos incompletos.
    if fallidos:
        print(f"\n❌ Jev falló en {len(fallidos)} caso(s): {', '.join(fallidos[:5])}"
              + (" ..." if len(fallidos) > 5 else ""))
        print(f"   Último error: {jev.uso['ultimo_error']}")
        print(f"   Modelo pedido: {jev.MODELO}  (si no existe, cambia JEV_MODEL en el .env)")
        print("   Calibración ABORTADA: no se recomienda ningún umbral con datos incompletos.")
        return

    # Los casos frontera no cuentan para el recall: no hay respuesta correcta.
    claros = [m for m in medidos if not m["frontera"]]
    importantes = [m for m in claros if m["esperado"]]
    ruido = [m for m in claros if not m["esperado"]]

    print("\n" + "=" * 68)
    print("SEPARACIÓN (cuanto más lejos estén los dos grupos, mejor filtra)")
    print("=" * 68)
    if importantes:
        p_min = min(m["noul"] for m in importantes)
        peor = min(importantes, key=lambda m: m["noul"])
        print(f"  Importantes: p mínima = {p_min:.3f}  ({peor['id']})")
        print(f"               p media  = {sum(m['noul'] for m in importantes)/len(importantes):.3f}")
    if ruido:
        p_max = max(m["noul"] for m in ruido)
        peor_r = max(ruido, key=lambda m: m["noul"])
        print(f"  Ruido:       p máxima = {p_max:.3f}  ({peor_r['id']})")
        print(f"               p media  = {sum(m['noul'] for m in ruido)/len(ruido):.3f}")

    print("\n" + "=" * 68)
    print("UMBRALES  (descartar sin LLM si p < umbral)")
    print("=" * 68)
    print(f"  {'umbral':>7}  {'falsos neg.':>12}  {'ahorro LLM':>11}  veredicto")
    seguro = None
    for u in UMBRALES:
        fn = [m for m in importantes if m["noul"] < u]
        filtrados = [m for m in medidos if m["noul"] < u]
        ahorro = len(filtrados) / len(medidos)
        if not fn:
            seguro = u
            veredicto = "✅ sin pérdidas"
        else:
            veredicto = "❌ pierde: " + ", ".join(m["id"] for m in fn[:2])
        print(f"  {u:>7.2f}  {len(fn):>12}  {ahorro:>10.0%}  {veredicto}")

    print("\n" + "=" * 68)
    if seguro:
        # Margen de seguridad: el dataset es pequeño y la bandeja real traerá
        # casos raros. Nos quedamos por debajo del último umbral sin pérdidas.
        recomendado = round(seguro * 0.6, 3)
        print(f"  Último umbral sin falsos negativos: {seguro}")
        print(f"  RECOMENDADO (con margen de seguridad): {recomendado}")
        print(f"\n  Ponlo en tu .env:   JEV_UMBRAL_RUIDO={recomendado}")
    else:
        print("  Ningún umbral evita falsos negativos: NO uses el prefiltro todavía.")
        print("  Revisa las instrucciones de jev.py o desactívalo (JEV_PREFILTRO=0).")
    print(f"\n  Coste de esta calibración: {jev.resumen_uso()}")

    if "--guardar" in sys.argv:
        SALIDA.write_text(json.dumps({"modelo": medidos[0]["modelo"] if medidos else jev.MODELO,
                                      "medidos": medidos, "umbral_seguro": seguro},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Guardado en {SALIDA.name}")


if __name__ == "__main__":
    main()
