"""
corregir.py — Corregir etiquetas en los DOS sitios a la vez
===========================================================

Tu verdad vive en dos sitios:
    emails.db           -> la usan la app, la API y la prueba en sombra
    dataset_real.json   -> la usan los evals
Si corriges solo uno, sombra y evals dejan de medir contra lo mismo. Este
comando actualiza los dos de una vez.

Uso por id (los ids salen en la prueba en sombra, "· id ..."):
    python corregir.py n 1a0a3e4404a4f873                   (NO me importa)
    python corregir.py s 1a0a1ab1b230b521                   (SÍ me importa)
    python corregir.py n 1a0a3e4404a4f873 1a0a8e766975f223  (varios a la vez)

Uso por asunto (busca en la BD, te enseña lo que ha encontrado y pregunta):
    python corregir.py n --asunto "UFV-INGINF/dis-"
    python corregir.py n --asunto "alifica%DESARROLLO E INTEGRACI"
  El texto se busca en cualquier parte del asunto, sin distinguir mayúsculas.
  Un % en medio significa "cualquier cosa entre medias".
"""

import json
import sys
from pathlib import Path

import almacen

DATASET = Path(__file__).parent / "dataset_real.json"


def _buscar(patron: str) -> list[dict]:
    with almacen._conectar() as conn:
        filas = conn.execute(
            "SELECT gmail_id, asunto, fecha_epoch FROM emails WHERE asunto LIKE ? ORDER BY fecha_epoch",
            (f"%{patron}%",),
        ).fetchall()
    return [dict(f) for f in filas]


def corregir(ids: list[str], importante: bool):
    casos = json.loads(DATASET.read_text(encoding="utf-8")) if DATASET.exists() else []
    por_id = {c.get("gmail_id", c["id"]): c for c in casos}

    for gid in ids:
        en_bd = almacen.corregir(gid, importante)
        caso = por_id.get(gid)
        antes = None
        if caso is not None:
            antes = "importante" if caso.get("importante") else "no importante"
            caso["importante"] = importante
            caso["frontera"] = False
            if not importante:
                caso["requiere_accion"] = False
                caso.pop("fecha_limite", None)
        estado = ["BD ✓" if en_bd else "BD ✗ (no está)",
                  f"dataset ✓ (antes: {antes})" if caso is not None else "dataset: no estaba etiquetado"]
        print(f"  {gid} → {'IMPORTANTE' if importante else 'no importante'}   [{' · '.join(estado)}]")

    if casos:
        DATASET.write_text(json.dumps(casos, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("s", "n"):
        print(__doc__)
        return
    importante = sys.argv[1] == "s"
    almacen.inicializar()

    if sys.argv[2] == "--asunto":
        if len(sys.argv) < 4:
            print("Falta el texto a buscar:  python corregir.py n --asunto \"texto\"")
            return
        encontrados = _buscar(sys.argv[3])
        if not encontrados:
            print(f"Ningún email con '{sys.argv[3]}' en el asunto.")
            return
        print(f"{len(encontrados)} email(s) con '{sys.argv[3]}' en el asunto:")
        for e in encontrados:
            print(f"  · {e['asunto'][:75]}")
        r = input(f"\n¿Marcar los {len(encontrados)} como {'IMPORTANTES' if importante else 'NO importantes'}? [s/N]: ")
        if r.strip().lower() != "s":
            print("Cancelado: no se ha cambiado nada.")
            return
        corregir([e["gmail_id"] for e in encontrados], importante)
    else:
        corregir(sys.argv[2:], importante)


if __name__ == "__main__":
    main()
