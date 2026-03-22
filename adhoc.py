import sqlite3
conn = sqlite3.connect('ephemera.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for row in rows:
    print(dict(row))