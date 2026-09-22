"""
etiquetar.py — Tú dices la verdad (etiquetado a ciegas)
========================================================

El eval mide la distancia entre lo que el sistema CREE que te importa y lo
que DE VERDAD te importa. Lo segundo solo lo sabes tú. Este script te enseña
tus emails reales uno a uno y te pregunta; nada más.

A CIEGAS: no te enseña lo que opinó el LLM ni Jev. Si lo vieras antes de
responder, te anclaría y el eval acabaría midiendo si estás de acuerdo con
el modelo, no si el modelo te sirve. Responde por instinto.

Orden: primero los emails que más enseñan (desacuerdos Jev/LLM, importantes,
dudosos) y al final el ruido evidente. Si paras a mitad, lo hecho es lo útil.

Qué guarda:
  - dataset_real.json  → tus etiquetas + el texto del email. PRIVADO: está en
                         .gitignore, NO se sube nunca (son tus correos).
  - emails.db          → tu respuesta como CORRECCIÓN (etiqueta de oro): a
                         partir de ahora tu palabra manda sobre la del modelo,
                         y la prueba en sombra la usa como referencia.

Reanudable: puedes salir con q y seguir otro día; no repite lo ya etiquetado.

Uso:
    python etiquetar.py              (todos, empezando por los más útiles)
    python etiquetar.py --n 40       (como mucho 40 en esta sesión)

Después:
    python evals.py --dataset dataset_real.json
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import almacen
import gmail

SALIDA = Path(__file__).parent / "dataset_real.json"
MAX_CUERPO_GUARDADO = 4000   # lo que se guarda para que el eval lo re-clasifique
MAX_CUERPO_MOSTRADO = 900    # lo que se te enseña en pantalla


def _cargar() -> list[dict]:
    if SALIDA.exists():
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    return []


def _guardar(casos: list[dict]):
    SALIDA.write_text(json.dumps(casos, ensure_ascii=False, indent=2), encoding="utf-8")


def _preguntar(texto: str, validas: set[str]) -> str:
    while True:
        r = input(texto).strip().lower()
        if r in validas:
            return r
        print(f"    (responde {' / '.join(sorted(validas))})")


def _pedir_fecha(fecha_email: date) -> str | None:
    """AAAA-MM-DD, DD/MM (año del email), vacío = sin plazo, ? = no lo sé.
    Devuelve '' (sin plazo), None (no evaluar) o la fecha ISO."""
    while True:
        r = input("    ¿Plazo? [DD/MM, AAAA-MM-DD, Enter = sin plazo, ? = no sé]: ").strip()
        if r == "":
            return ""
        if r == "?":
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r):
            try:
                return date.fromisoformat(r).isoformat()
            except ValueError:
                pass
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", r)
        if m:
            try:
                return date(fecha_email.year, int(m[2]), int(m[1])).isoformat()
            except ValueError:
                pass
        print("    (formato no válido)")


def main():
    limite = None
    if "--n" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--n") + 1])

    almacen.inicializar()
    casos = _cargar()
    # c.get(..., c["id"]): compatible con entradas antiguas que solo tenían "id"
    hechos = {c.get("gmail_id", c["id"]) for c in casos}
    pendientes = [c for c in almacen.candidatos_etiquetado() if c["gmail_id"] not in hechos]
    if limite:
        pendientes = pendientes[:limite]
    if not pendientes:
        print(f"Nada pendiente: ya tienes {len(casos)} emails etiquetados en {SALIDA.name}.")
        return

    print(f"{len(casos)} ya etiquetados · {len(pendientes)} en esta sesión")
    print("Responde por instinto. No se te enseña lo que opinó el modelo.\n")
    print("  s = sí me importa · n = no · d = dudoso · o = omitir · q = guardar y salir\n")
    input("Pulsa Enter para empezar...")

    por_id = {c["gmail_id"]: c for c in pendientes}
    nuevos = 0
    for i, e in enumerate(gmail.iterar_por_ids(list(por_id)), 1):
        fecha_email = date.fromtimestamp(e["fecha_epoch"])
        os.system("cls" if os.name == "nt" else "clear")   # un email cada vez, sin distracciones
        print("─" * 72)
        print(f"[{i}/{len(pendientes)}]  {fecha_email.strftime('%d/%m/%Y')}")
        print(f"De:     {e['remitente'][:68]}")
        print(f"Asunto: {e['asunto'][:68]}\n")
        cuerpo = e["cuerpo"]
        print(cuerpo[:MAX_CUERPO_MOSTRADO] + (" [...]" if len(cuerpo) > MAX_CUERPO_MOSTRADO else ""))
        print()

        r = _preguntar("¿Te importa? [s/n/d/o/q]: ", {"s", "n", "d", "o", "q"})
        if r == "q":
            break
        if r == "o":
            continue

        caso = {
            "id": f"etq_{e['id'][:12]}",
            "gmail_id": e["id"],
            "origen": "etiquetado_por_marco",
            "fecha_email": fecha_email.isoformat(),
            "remitente": e["remitente"],
            "asunto": e["asunto"],
            "cuerpo": cuerpo[:MAX_CUERPO_GUARDADO],
            # 'd' = dudoso: cuenta como frontera (no puntúa) y, ante la duda, importante
            "importante": r in ("s", "d"),
            "frontera": r == "d",
        }
        if r == "s":
            t = _preguntar("    ¿Tienes que HACER algo? [s/n/? no sé]: ", {"s", "n", "?"})
            if t != "?":
                caso["requiere_accion"] = (t == "s")
                if t == "s":
                    plazo = _pedir_fecha(fecha_email)
                    if plazo is not None:
                        caso["fecha_limite"] = plazo
        elif r == "n":
            caso["requiere_accion"] = False

        casos.append(caso)
        _guardar(casos)   # guardado inmediato: a prueba de cortes
        nuevos += 1
        # Tu respuesta es una etiqueta de oro: manda sobre el modelo desde ya.
        # (Las dudosas no se registran: no hay veredicto que imponer.)
        if r in ("s", "n"):
            almacen.corregir(e["id"], r == "s")

    total_imp = sum(c["importante"] and not c.get("frontera") for c in casos)
    total_dud = sum(bool(c.get("frontera")) for c in casos)
    print("─" * 72)
    print(f"\nEsta sesión: {nuevos} etiquetados. Total: {len(casos)} "
          f"({total_imp} importantes · {total_dud} dudosos · "
          f"{len(casos) - total_imp - total_dud} no importantes)")
    print(f"Guardado en {SALIDA.name} (privado, fuera de git).")
    print("\nSiguiente:  python evals.py --dataset dataset_real.json")


if __name__ == "__main__":
    main()
