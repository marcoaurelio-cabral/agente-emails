"""
corregir.py — Corregir etiquetas en los DOS sitios a la vez
===========================================================

Tu verdad vive en dos sitios:
    emails.db           -> la usan la app, la API y la prueba en sombra
    dataset_real.json   -> la usan los evals
Si corriges solo uno, sombra y evals dejan de medir contra lo mismo. Este
comando actualiza los dos de una vez.

Uso:
    python corregir.py n 1a0a3e4404a4f873                   (NO me importa)
    python corregir.py s 1a0a1ab1b230b521                   (SÍ me importa)
    python corregir.py n 1a0a3e4404a4f873 1a0a8e766975f223  (varios a la vez)

Los ids salen en la prueba en sombra ("· id ...").
"""

import json
import sys
from pathlib import Path

import almacen

DATASET = Path(__file__).parent / "dataset_real.json"


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("s", "n"):
        print(__doc__)
        return
    importante = sys.argv[1] == "s"
    ids = sys.argv[2:]

    almacen.inicializar()
    casos = json.loads(DATASET.read_text(encoding="utf-8")) if DATASET.exists() else []
    por_id = {c.get("gmail_id", c["id"]): c for c in casos}

    for gid in ids:
        en_bd = almacen.corregir(gid, importante)
        caso = por_id.get(gid)
        if caso is not None:
            antes = "importante" if caso.get("importante") else "no importante"
            caso["importante"] = importante
            caso["frontera"] = False
            if not importante:
                caso["requiere_accion"] = False
                caso.pop("fecha_limite", None)
        estado = []
        estado.append("BD ✓" if en_bd else "BD ✗ (no está)")
        estado.append(f"dataset ✓ (antes: {antes})" if caso is not None else "dataset: no estaba etiquetado")
        print(f"  {gid} → {'IMPORTANTE' if importante else 'no importante'}   [{' · '.join(estado)}]")

    if casos:
        DATASET.write_text(json.dumps(casos, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
