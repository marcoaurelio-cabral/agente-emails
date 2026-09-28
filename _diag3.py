import calendario
d = calendario.eventos_deseados()
for e in d.values():
    print(e["resumen"], "|", e["fecha"], "|", e["tipo"])
