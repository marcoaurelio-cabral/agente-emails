"""
agrupar.py — ¿Estos emails hablan de LO MISMO? (algoritmo, sin IA)
===================================================================

Separación de responsabilidades:
    cerebro.py  -> ¿me importa este email?  (IA)  + extrae unas claves
    agrupar.py  -> ¿ya tengo esto?          (código, determinista, 0 tokens)

Dos emails van al mismo grupo si:
  1. comparten REFERENCIA (localizador, nº de pedido): mismo viaje seguro; o
  2. tienen el mismo TIPO, una ENTIDAD parecida ("Venecia" ~ "Venice (Italia)")
     y fechas compatibles (fecha del evento a ±3 días o, si falta, emails a
     menos de 30 días uno de otro).

Con los grupos:
  - el informe y la app enseñan UNA línea por grupo (el email más reciente,
    con cuántos hay detrás): da igual cuál llegó primero;
  - si un email "confirma que algo está hecho", se cierran las tareas
    pendientes ANTERIORES de su mismo grupo (se puede reabrir).

Revisar los grupos formados:  python agrupar.py
"""

import difflib
import re
import unicodedata
from datetime import date

UMBRAL_PARECIDO = 0.8
DIAS_FECHA = 3
DIAS_SIN_FECHA = 30
TIPOS_AGRUPABLES = {"viaje", "reunion", "evento", "entrega", "tramite"}

# Traducciones mínimas para que "Venice" y "Venecia" coincidan.
_SINONIMOS = {"venice": "venecia", "rome": "roma", "florence": "florencia", "milan": "milano",
              "london": "londres", "new york": "nueva york", "vienna": "viena",
              "brussels": "bruselas", "warsaw": "varsovia", "gdansk": "gdansk"}


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for en, es in _SINONIMOS.items():
        t = re.sub(rf"\b{en}\b", es, t)
    return t


def _parecidas(a: str, b: str) -> bool:
    a, b = normalizar(a), normalizar(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= UMBRAL_PARECIDO


def misma_cosa(a: dict, b: dict) -> bool:
    """a, b: dicts con referencia, tipo_entidad, entidad, fecha_entidad, fecha_epoch."""
    if a.get("referencia") and a.get("referencia") == b.get("referencia"):
        return True
    tipo = a.get("tipo_entidad")
    if tipo not in TIPOS_AGRUPABLES or tipo != b.get("tipo_entidad"):
        return False
    if not _parecidas(a.get("entidad", ""), b.get("entidad", "")):
        return False
    fa, fb = a.get("fecha_entidad"), b.get("fecha_entidad")
    if fa and fb:
        return abs((date.fromisoformat(fa) - date.fromisoformat(fb)).days) <= DIAS_FECHA
    return abs((a.get("fecha_epoch") or 0) - (b.get("fecha_epoch") or 0)) <= DIAS_SIN_FECHA * 86400


def agrupar(filas: list[dict]) -> dict[str, str]:
    """Union-find: {gmail_id: id_de_grupo}. El id del grupo es el gmail_id del
    email más antiguo del grupo (estable entre ejecuciones)."""
    padre = {f["gmail_id"]: f["gmail_id"] for f in filas}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    ordenadas = sorted(filas, key=lambda f: f.get("fecha_epoch") or 0)
    for i, a in enumerate(ordenadas):
        for b in ordenadas[i + 1:]:
            if misma_cosa(a, b):
                ra, rb = raiz(a["gmail_id"]), raiz(b["gmail_id"])
                if ra != rb:
                    # la raíz es siempre la del email más antiguo
                    antes = min((ra, rb), key=lambda x: next(f["fecha_epoch"] for f in filas if f["gmail_id"] == x))
                    despues = rb if antes == ra else ra
                    padre[despues] = antes
    return {gid: raiz(gid) for gid in padre}


def tareas_a_cerrar(filas: list[dict], grupos: dict[str, str]) -> list[tuple[str, str]]:
    """[(id de la tarea, id del email que la confirma)]: tareas pendientes de un
    grupo en el que ha llegado DESPUÉS un email que confirma que está hecho."""
    cierres = []
    for c in filas:
        if not c.get("confirma_hecho"):
            continue
        for t in filas:
            if (t.get("estado") == "pendiente" and t["gmail_id"] != c["gmail_id"]
                    and grupos.get(t["gmail_id"]) == grupos.get(c["gmail_id"])
                    and (t.get("fecha_epoch") or 0) < (c.get("fecha_epoch") or 0)):
                cierres.append((t["gmail_id"], c["gmail_id"]))
    return cierres


if __name__ == "__main__":
    import almacen
    almacen.inicializar()
    filas = almacen.filas_para_agrupar()
    grupos = agrupar(filas)
    miembros: dict[str, list[dict]] = {}
    for f in filas:
        miembros.setdefault(grupos[f["gmail_id"]], []).append(f)
    multiples = {g: m for g, m in miembros.items() if len(m) > 1}
    print(f"{len(filas)} emails con claves · {len(multiples)} grupos con más de un email\n")
    for g, m in multiples.items():
        f0 = m[0]
        print(f"■ {f0['tipo_entidad']} · {f0['entidad']} · {f0['fecha_entidad'] or 'sin fecha'}"
              + (f" · ref {f0['referencia']}" if f0['referencia'] else ""))
        for f in sorted(m, key=lambda x: x["fecha_epoch"] or 0):
            print(f"     - {(f['asunto'] or '')[:70]}")
