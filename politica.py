"""
politica.py — La política del prefiltro, en UN solo sitio
==========================================================

Jev da dos señales por email: p(importante) (el Noul) y una categoría (el
Choice). Este módulo decide qué hacer con ellas. Lo usan TRES sitios:
    servicio.py     -> para decidir de verdad en la cascada
    calibrar_jev.py -> para medir la regla contra el dataset etiquetado
    sombra_jev.py   -> para medirla contra tu bandeja real
Si la regla viviera copiada en los tres, acabarían midiendo cosas distintas
de lo que ejecutas. Una sola definición = lo que mides es lo que corre.

Las dos reglas:
    solo_noul    descartar si p < umbral
    dos_senales  descartar si p < umbral Y la categoría es descartable
                 (promocional, automatico, sospechoso). Un email 'personal',
                 'universidad', 'beca'... va SIEMPRE al LLM, dé Jev la p que dé.
                 Para perder un email importante tienen que equivocarse las
                 dos señales a la vez y en el mismo sentido.

Nota de la guía de TypeSafe: cambiar la política (umbral, regla) NO requiere
volver a llamar al modelo si el estado y las preguntas no han cambiado. Por
eso calibrar_jev.py tiene --desde-json: recalcula tablas sin gastar nada.

Configuración (.env):
    JEV_PREFILTRO=1|0          activar/desactivar el prefiltro
    JEV_REGLA=dos_senales      o solo_noul
    JEV_UMBRAL_RUIDO=0.05      conservador hasta que lo calibres
"""

import os

from dotenv import load_dotenv

load_dotenv()

REGLAS = ("solo_noul", "dos_senales")
CATEGORIAS_DESCARTABLES = frozenset({"promocional", "automatico", "sospechoso"})

USAR_PREFILTRO = os.getenv("JEV_PREFILTRO", "1") != "0"
REGLA = os.getenv("JEV_REGLA", "dos_senales")
UMBRAL_RUIDO = float(os.getenv("JEV_UMBRAL_RUIDO", "0.05"))

UMBRALES_BARRIDO = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70]
MARGEN_SEGURIDAD = 0.6  # recomendamos el 60% del último umbral sin pérdidas

if REGLA not in REGLAS:
    raise ValueError(f"JEV_REGLA='{REGLA}' no válida. Opciones: {REGLAS}")


def descartar(noul: float, categoria: str, umbral: float | None = None,
              regla: str | None = None) -> bool:
    """¿Se descarta este email sin llamar al LLM?"""
    umbral = UMBRAL_RUIDO if umbral is None else umbral
    regla = regla or REGLA
    if noul >= umbral:
        return False
    if regla == "solo_noul":
        return True
    if regla == "dos_senales":
        return categoria in CATEGORIAS_DESCARTABLES
    raise ValueError(f"Regla desconocida: {regla}")


def describir() -> str:
    return f"{REGLA} · umbral {UMBRAL_RUIDO}"


# ── Evaluación de la política (compartida por calibración y sombra) ─────────

def barrer(items: list[dict], regla: str, umbrales=UMBRALES_BARRIDO) -> list[dict]:
    """items: dicts con id, noul, categoria y esperado (True = no debe perderse).
    Para cada umbral: cuántos se descartarían y cuáles de ellos eran importantes."""
    filas = []
    for u in umbrales:
        desc = [i for i in items if descartar(i["noul"], i["categoria"], u, regla)]
        filas.append({
            "umbral": u,
            "descartados": len(desc),
            "ahorro": len(desc) / len(items) if items else 0.0,
            "falsos_negativos": [i for i in desc if i["esperado"]],
        })
    return filas


def recomendar(filas: list[dict]) -> tuple[float | None, float | None]:
    """(último umbral sin falsos negativos, umbral recomendado con margen).
    Los descartes crecen con el umbral, así que el conjunto seguro es un prefijo."""
    seguro = None
    for f in filas:
        if f["falsos_negativos"]:
            break
        seguro = f["umbral"]
    return seguro, (round(seguro * MARGEN_SEGURIDAD, 2) if seguro else None)


def informe_umbrales(items: list[dict]) -> str:
    """Tabla comparativa de las dos reglas + recomendación para cada una."""
    lineas = []
    tablas = {r: barrer(items, r) for r in REGLAS}
    lineas.append(f"  {'umbral':>7} │ {'SOLO NOUL':^27} │ {'DOS SEÑALES':^27}")
    lineas.append(f"  {'':>7} │ {'FN':>4} {'ahorro':>7}  {'':<13} │ {'FN':>4} {'ahorro':>7}  {'':<13}")
    lineas.append("  " + "─" * 73)
    for a, b in zip(tablas["solo_noul"], tablas["dos_senales"]):
        def celda(f):
            ok = "✅" if not f["falsos_negativos"] else "❌ " + f["falsos_negativos"][0]["id"][:10]
            return f"{len(f['falsos_negativos']):>4} {f['ahorro']:>7.0%}  {ok:<13}"
        lineas.append(f"  {a['umbral']:>7.2f} │ {celda(a)} │ {celda(b)}")
    lineas.append("")
    for r in REGLAS:
        seguro, rec = recomendar(tablas[r])
        if seguro is None:
            lineas.append(f"  {r:<12} ningún umbral evita falsos negativos")
        else:
            ahorro_rec = next((f["ahorro"] for f in barrer(items, r, [rec])), 0)
            lineas.append(f"  {r:<12} sin pérdidas hasta {seguro} · recomendado {rec} "
                          f"(ahorro {ahorro_rec:.0%})")
    return "\n".join(lineas)
