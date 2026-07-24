"""Quick DB check script."""
import sqlite3

conn = sqlite3.connect("data/app.db")

# List tables
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print("Tables:", tables)

# Check position_logs (recent)
try:
    cursor = conn.execute("SELECT id, symbol, level, event, message, created_at FROM position_logs ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    print(f"\n=== Recent logs ({len(rows)}) ===")
    for r in rows:
        print(f"  [{r[5]}] [{r[2]}] {r[1]}: {r[4]}")
except Exception as e:
    print(f"position_logs error: {e}")

# Check trades
try:
    cursor = conn.execute("SELECT id, symbol, side, position_side, event, quantity, price, margin, realized_pnl, created_at FROM trades ORDER BY id DESC LIMIT 20")
    rows = cursor.fetchall()
    print(f"\n=== Recent trades ({len(rows)}) ===")
    for r in rows:
        print(f"  [{r[9]}] {r[1]} {r[2]} {r[3]} event={r[4]} qty={r[5]} price={r[6]} margin={r[7]} pnl={r[8]}")
except Exception as e:
    print(f"trades error: {e}")

# Check position_state
try:
    cursor = conn.execute("SELECT symbol, direction, quantity, entry_price, margin, opened_at FROM position_state")
    rows = cursor.fetchall()
    print(f"\n=== Position states ({len(rows)}) ===")
    for r in rows:
        print(f"  {r[0]}: dir={r[1]} qty={r[2]} entry={r[3]} margin={r[4]} opened={r[5]}")
except Exception as e:
    print(f"position_state error: {e}")

# Check engine_state
try:
    cursor = conn.execute("SELECT key, value FROM engine_state")
    rows = cursor.fetchall()
    print(f"\n=== Engine state ({len(rows)}) ===")
    for r in rows:
        print(f"  {r[0]} = {r[1]}")
except Exception as e:
    print(f"engine_state error: {e}")

# Check strategy_configs
try:
    cursor = conn.execute("SELECT name, payload FROM strategy_configs")
    rows = cursor.fetchall()
    print(f"\n=== Strategy configs ({len(rows)}) ===")
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
except Exception as e:
    print(f"strategy_configs error: {e}")

conn.close()
