import sqlite3
c = sqlite3.connect("emails.db")
c.row_factory = sqlite3.Row
q = """SELECT gmail_id, asunto, tipo_entidad, entidad, fecha_entidad, requiere_accion, accion, fecha_limite, estado, grupo
       FROM emails
       WHERE COALESCE(correccion_importante, importante) = 1
         AND (fecha_entidad >= date('now') OR fecha_limite >= date('now'))
       ORDER BY COALESCE(fecha_entidad, fecha_limite)"""
for r in c.execute(q):
    print(dict(r))
