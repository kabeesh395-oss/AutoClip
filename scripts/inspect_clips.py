import sqlite3
import json

con = sqlite3.connect("AutoClip/data/autoclip.sqlite3")
con.row_factory = sqlite3.Row

print("--- CLIPS ---")
for r in con.execute("SELECT * FROM clips").fetchall():
    print(dict(r))

print("--- SOURCES ---")
for r in con.execute("SELECT * FROM sources").fetchall():
    print(dict(r))
