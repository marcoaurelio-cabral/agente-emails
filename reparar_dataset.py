"""
reparar_dataset.py — Arreglar dataset_real.json SIN perder tus etiquetas
========================================================================

Si etiquetaste con una versión anterior de etiquetar.py, tu dataset puede
tener huecos que el eval no perdona. Este script los repara en su sitio.
Tus respuestas (importante / dudoso / tarea / plazo) NO se tocan: son lo
valioso y no se pueden reconstruir.

Qué arregla:
  1. Falta "origen"        -> el eval hacía KeyError tras pagar las llamadas.
  2. Falta "fecha_email"   -> se saca de la BD. Sin ella, "en 7 días" o "el
                              jueves" se calculaban desde un día equivocado.
  3. Falta "gmail_id"      -> para reanudar el etiquetado sin repetir.
  4. Plazos mal escritos   -> "25/09" pasa a "2026-09-25". Lo irreparable se
                              quita (mejor no evaluar ese plazo que evaluarlo mal).
  5. Cuerpos contaminados  -> si el texto guardado es el RESUMEN del LLM (lo que
                              pasaba cuando fallaba la descarga de Gmail), se
                              vuelve a descargar el email real. Evaluar el
                              resumen del LLM es medir al LLM contra sí mismo.
  6. "No importante" sin tarea -> requiere_accion = False (si no te importa,
                              no hay nada que hacer). Así también se mide que el
                              modelo no invente tareas en el ruido.

Seguro: hace copia en dataset_real.backup.json antes de tocar nada, y es
idempotente (ejecutarlo dos veces no cambia nada la segunda).

Uso:  python reparar_dataset.py
      python reparar_dataset.py --cabeceras   (añade Para/CC, tu rol y la hora
                                               exacta de cada email, desde Gmail;
                                               los necesita la memoria entre emails)
"""

import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import almacen
import gmail

DATASET = Path(__file__).parent / "dataset_real.json"
BACKUP = Path(__file__).parent / "dataset_real.backup.json"
MAX_CUERPO = 4000


def _normalizar_fecha(valor: str, anio: int) -> str | None:
    v = (valor or "").strip()
    if not v:
        return ""
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", v)
    if m:
        a = int(m[3]) if m[3] else anio
        a = a + 2000 if a < 100 else a
        try:
            return date(a, int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return None
    return None


def main():
    if not DATASET.exists():
        print(f"No existe {DATASET.name}: no hay nada que reparar.")
        return

    casos = json.loads(DATASET.read_text(encoding="utf-8"))
    shutil.copy(DATASET, BACKUP)
    almacen.inicializar()

    ids = [c.get("gmail_id", c["id"]) for c in casos]
    meta = almacen.metadatos(ids)
    cambios = {"origen": 0, "fecha_email": 0, "gmail_id": 0, "plazo": 0,
               "plazo_quitado": [], "tarea_no": 0, "contaminados": [], "sin_bd": []}

    a_redescargar = []
    for c in casos:
        gid = c.get("gmail_id", c["id"])
        m = meta.get(gid)

        if "gmail_id" not in c:
            c["gmail_id"] = gid
            cambios["gmail_id"] += 1
        if "origen" not in c:
            c["origen"] = "etiquetado_por_marco"
            cambios["origen"] += 1
        if not c.get("fecha_email"):
            if m and m["fecha_epoch"]:
                c["fecha_email"] = date.fromtimestamp(m["fecha_epoch"]).isoformat()
                cambios["fecha_email"] += 1
            else:
                cambios["sin_bd"].append(gid)

        anio = date.fromisoformat(c["fecha_email"]).year if c.get("fecha_email") else date.today().year
        if "fecha_limite" in c:
            nueva = _normalizar_fecha(c["fecha_limite"], anio)
            if nueva is None:
                cambios["plazo_quitado"].append((gid, c["fecha_limite"]))
                del c["fecha_limite"]
            elif nueva != c["fecha_limite"]:
                c["fecha_limite"] = nueva
                cambios["plazo"] += 1

        if not c.get("importante") and not c.get("frontera") and "requiere_accion" not in c:
            c["requiere_accion"] = False
            cambios["tarea_no"] += 1

        cuerpo = (c.get("cuerpo") or "").strip()
        resumen = ((m or {}).get("resumen") or "").strip()
        if (not cuerpo or cuerpo.startswith("(Fallo al descargar")
                or (resumen and cuerpo == resumen)):
            a_redescargar.append(gid)

    # Re-descarga de los contaminados. Si falla, que se vea (nada de fallbacks silenciosos).
    if a_redescargar:
        print(f"Re-descargando {len(a_redescargar)} email(s) con el cuerpo contaminado...")
        por_id = {c["gmail_id"]: c for c in casos}
        for e in gmail.iterar_por_ids(a_redescargar):
            por_id[e["id"]]["cuerpo"] = e["cuerpo"][:MAX_CUERPO]
            cambios["contaminados"].append(e["id"])

    # Cabeceras para la memoria entre emails (solo si se pide, y solo lo que falte)
    if "--cabeceras" in sys.argv:
        faltan = [c["gmail_id"] for c in casos if "rol" not in c or "fecha_epoch" not in c]
        if faltan:
            print(f"Trayendo Para/CC y hora exacta de {len(faltan)} email(s) desde Gmail...")
            por_id = {c["gmail_id"]: c for c in casos}
            for e in gmail.iterar_por_ids(faltan):
                c = por_id[e["id"]]
                c.update(para=e["para"], cc=e["cc"], fecha_epoch=e["fecha_epoch"], rol=gmail.rol_de(e))
        # Recalcular el rol de TODOS (sin llamar a Gmail): cambia si añades
        # direcciones a MIS_DIRECCIONES.
        for c in casos:
            if "para" in c:
                c["rol"] = gmail.rol_de(c)
        if True:
            roles = {}
            for c in casos:
                roles[c.get("rol", "?")] = roles.get(c.get("rol", "?"), 0) + 1
            print(f"  roles: {roles}")

    DATASET.write_text(json.dumps(casos, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nReparado {DATASET.name} ({len(casos)} casos). Copia previa en {BACKUP.name}.")
    print(f"  origen añadido ............. {cambios['origen']}")
    print(f"  fecha_email desde la BD .... {cambios['fecha_email']}")
    print(f"  gmail_id añadido ........... {cambios['gmail_id']}")
    print(f"  plazos normalizados ........ {cambios['plazo']}")
    print(f"  'no importa' => sin tarea .. {cambios['tarea_no']}")
    print(f"  cuerpos re-descargados ..... {len(cambios['contaminados'])}")
    for gid, v in cambios["plazo_quitado"]:
        print(f"  ⚠️  plazo irreparable quitado en {gid}: '{v}' (ese plazo no se evaluará)")
    for gid in cambios["sin_bd"]:
        print(f"  ⚠️  {gid} no está en la BD: sin fecha_email (el eval lo rechazará)")
    total_imp = sum(bool(c.get("importante")) and not c.get("frontera") for c in casos)
    print(f"\nTus etiquetas intactas: {total_imp} importantes · "
          f"{sum(bool(c.get('frontera')) for c in casos)} dudosos · "
          f"{len(casos) - total_imp - sum(bool(c.get('frontera')) for c in casos)} no importantes")
    print("\nSiguiente:  python evals.py --dataset dataset_real.json 1")


if __name__ == "__main__":
    main()
