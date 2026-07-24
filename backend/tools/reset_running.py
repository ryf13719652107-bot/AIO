import sqlite3
c = sqlite3.connect(r"data/app.db")
c.execute("UPDATE engine_state SET value='false' WHERE key='running'")
c.commit()
print(list(c.execute("SELECT key, value FROM engine_state")))
c.close()
