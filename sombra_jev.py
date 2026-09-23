"""
sombra_jev.py — Prueba EN SOMBRA sobre tu bandeja real
=======================================================

El dataset de evals tiene 32 casos equilibrados. Tu bandeja real es otra
cosa: ~80% ruido, remitentes que se repiten, casos raros. Antes de subir el
umbral, hay que ver qué haría el prefiltro con TUS emails.

"En sombra" significa: se ejecuta de verdad, pero NO decide nada. Cada email
que el LLM ya clasificó se pasa por Jev, se guarda su opinión (jev_noul,
jev_categoria) y se compara con la referencia. Ninguna decisión cambia.

Referencia: tu corrección si la hay; si no, la etiqueta del LLM. OJO: el LLM
acierta ~90%, no el 100%. Cuando Jev y el LLM discrepan, el script te lista
los emails para que TÚ decidas quién tenía razón. Si era Jev, corrige ese
email desde la app/API (POST /emails/{id}/corregir): se convierte en una
etiqueta de oro y la próxima sombra ya lo contará bien.

Uso:
    python sombra_jev.py                  (todos los emails decididos por el LLM)
    python sombra_jev.py --limite 40      (prueba rápida)
    python sombra_jev.py --umbral 0.3     (listar desacuerdos a ese umbral)
    python sombra_jev.py --desde-bd --umbral 0.6
                                          (GRATIS: sin Gmail ni Jev, con la opinión
                                           de Jev ya guardada en la BD)

--desde-bd sirve para probar POLÍTICA (umbral, regla, remitentes protegidos)
sin gastar nada. Si cambias las PREGUNTAS de jev.py, ejecuta una vez sin
--desde-bd para que Jev vuelva a opinar con las preguntas nuevas.

Coste: una llamada a Jev por email (~0,00004 $ cada una) + lecturas de Gmail.
Guarda sombra_jev.json SIN asuntos ni remitentes: solo ids y números.
"""

import json
import sys
from pathlib import Path

import almacen
import gmail
import jev
import politica

SALIDA = Path(__file__).parent / "sombra_jev.json"


def _arg(nombre: str, tipo, defecto):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return tipo(sys.argv[i + 1])
    return defecto


def main():
    limite = _arg("--limite", int, None)
    umbral_revision = _arg("--umbral", float, None)

    desde_bd = "--desde-bd" in sys.argv
    if not desde_bd and not jev.disponible():
        print("Jev no disponible: falta TYPESAFE_API_KEY o el paquete typesafe-sdk.")
        return

    almacen.inicializar()
    ref = almacen.emails_para_sombra()
    if limite:
        ref = ref[:limite]
    if not ref:
        print("No hay emails clasificados por el LLM en la BD. Ejecuta antes revisar_bandeja.py.")
        return

    def _medido(r, noul, categoria):
        return {"id": r["gmail_id"], "asunto": r["asunto"], "remitente": r["remitente"],
                "noul": noul, "categoria": categoria,
                "esperado": bool(r["importante_ref"]),
                "corregido": r["correccion_importante"] is not None}

    if desde_bd:
        medidos = [_medido(r, r["jev_noul"], r["jev_categoria"])
                   for r in ref if r["jev_noul"] is not None and r["jev_categoria"]]
        print(f"Recalculando la política con la opinión de Jev guardada en la BD: "
              f"{len(medidos)} emails · 0 llamadas · 0 €")
        if len(medidos) < len(ref):
            print(f"  ({len(ref) - len(medidos)} emails sin opinión de Jev guardada: no entran)")
        if not medidos:
            print("  No hay opiniones guardadas: ejecuta una vez sin --desde-bd.")
            return
    else:
        por_id = {r["gmail_id"]: r for r in ref}
        print(f"Prueba en sombra: {len(ref)} emails reales · Jev {jev.MODELO} · "
              f"NO se cambia ninguna decisión\n")
        medidos, fallidos = [], []
        for i, e in enumerate(gmail.iterar_por_ids(list(por_id)), 1):
            pre = jev.evaluar(e["remitente"], e["asunto"], e["cuerpo"])
            if pre is None:
                fallidos.append(e["id"])
                if jev.cortocircuito_abierto():
                    break
                continue
            almacen.guardar_prefiltro(e["id"], pre)
            medidos.append(_medido(por_id[e["id"]], pre["noul"], pre["categoria"]))
            if i % 20 == 0 or i == len(ref):
                print(f"  ...{i}/{len(ref)}")
        if fallidos:
            print(f"\n❌ Jev falló en {len(fallidos)} email(s). Último error: {jev.uso['ultimo_error']}")
            print("   Prueba ABORTADA: con huecos, las cifras no serían fiables.")
            return

    importantes = [m for m in medidos if m["esperado"]]
    ruido = [m for m in medidos if not m["esperado"]]

    # ── Qué ve Jev en tu bandeja ─────────────────────────────────────────
    print("\n" + "=" * 77)
    print(f"TU BANDEJA: {len(medidos)} emails · {len(importantes)} importantes · {len(ruido)} ruido "
          f"({len(ruido)/len(medidos):.0%})")
    print("=" * 77)
    cats = {}
    for m in medidos:
        cats.setdefault(m["categoria"], []).append(m)
    for cat, ms in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        n_imp = sum(m["esperado"] for m in ms)
        media = sum(m["noul"] for m in ms) / len(ms)
        prot = " " if cat in politica.CATEGORIAS_DESCARTABLES else "🛡"
        print(f"  {prot} {cat:<12} {len(ms):>4} emails · {n_imp:>3} importantes · p media {media:.2f}")
    print("  (🛡 = categoría protegida: con 'dos_senales' siempre va al LLM)")

    # ── Tabla de umbrales ────────────────────────────────────────────────
    print("\n" + "=" * 77)
    print("UMBRALES sobre tu bandeja real  (referencia: LLM + tus correcciones)")
    print("=" * 77)
    print(politica.informe_umbrales(medidos))

    # ── Desacuerdos: lo que TÚ tienes que mirar ──────────────────────────
    _, rec = politica.recomendar(politica.barrer(medidos, politica.REGLA))
    u = umbral_revision or rec or politica.UMBRAL_RUIDO
    perdidos = [m for m in importantes if politica.descartar(m["noul"], m["categoria"], u,
                                                            remitente=m.get("remitente"))]
    print("\n" + "=" * 77)
    print(f"DESACUERDOS a umbral {u} ({politica.REGLA}): Jev descartaría, el LLM dijo IMPORTANTE")
    print("=" * 77)
    if not perdidos:
        print("  Ninguno. ✅")
    for m in sorted(perdidos, key=lambda m: m["noul"]):
        marca = "  ← TÚ dijiste que era importante: FALLO REAL" if m["corregido"] else ""
        print(f"  p={m['noul']:.3f} [{m['categoria']:<11}] {m['asunto'][:52]}{marca}")
        print(f"        de: {m['remitente'][:60]} · id {m['id']}")
    if any(not m["corregido"] for m in perdidos):
        print("\n  Para los que NO has corregido: ¿quién tenía razón?")
        print("    - Jev (no era importante) -> corrígelo en /docs:")
        print("        POST /emails/{id}/corregir  {\"importante\": false}")
        print("    - El LLM (sí lo era) -> fallo real del prefiltro: NO subas a este umbral.")
    if any(m["corregido"] for m in perdidos):
        print("\n  Hay fallos confirmados por ti: este umbral es DEMASIADO alto.")

    dudosos = [m for m in importantes if 0.3 <= m["noul"] < 0.6]
    if dudosos:
        print(f"\n  Importantes donde Jev dudaba (0.3 ≤ p < 0.6): {len(dudosos)}")
        for m in sorted(dudosos, key=lambda m: m["noul"])[:8]:
            print(f"    p={m['noul']:.3f} [{m['categoria']:<11}] {m['asunto'][:55]}")

    if politica.REGLA == "dos_senales":
        expuestos = sorted((m for m in importantes
                            if m["categoria"] in politica.CATEGORIAS_DESCARTABLES
                            and not politica.protegido(m.get("remitente"))),
                           key=lambda m: m["noul"])
        print("\n" + "=" * 77)
        print("IMPORTANTES EXPUESTOS (categoría descartable + remitente sin proteger)")
        print("  El primero marca el techo del umbral: por encima de su p, se pierde.")
        print("=" * 77)
        if not expuestos:
            print("  Ninguno: con tus datos, ningún umbral pierde importantes.")
            print("  (Emails NUEVOS de remitentes no vistos sí pueden perderse: revisa el ruido.)")
        for m in expuestos:
            print(f"  p={m['noul']:.3f} [{m['categoria']:<13}] {m['asunto'][:44]}")
            print(f"        proteger:  {politica.direccion(m['remitente'])}")
        if expuestos:
            print(f"\n  Techo actual: umbral < {expuestos[0]['noul']:.3f}. Para subirlo, añade la dirección")
            print("  del primero a remitentes_protegidos.txt y vuelve a ejecutar con --desde-bd (gratis).")

    print(f"\n  Política activa ahora: {politica.describir()}")
    print(f"  Coste de la prueba: {'0 € (desde la BD)' if desde_bd else jev.resumen_uso()}")

    # Sin asuntos ni remitentes: el archivo se puede commitear sin exponer tu correo.
    SALIDA.write_text(json.dumps({
        "modelo": jev.MODELO,
        "medidos": [{k: m[k] for k in ("id", "noul", "categoria", "esperado", "corregido")}
                    for m in medidos],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Guardado en {SALIDA.name} (sin texto de tus emails)")


if __name__ == "__main__":
    main()
