"""Write web/status.json: what the server run produced and how fresh each file is (for the panel's «Сервер» row)."""
import json
import os
import sqlite3
import time

now = int(time.time() * 1000)
st = {"time": now, "files": {}}
for f in ("terminal.json", "decision.json", "paper.json", "portfolio.json"):
    p = os.path.join("web", f)
    try:
        d = json.load(open(p))
        st["files"][f] = {"asof": d.get("asof") or d.get("time"), "ok": True}
    except Exception as e:  # noqa: BLE001
        st["files"][f] = {"ok": False, "error": str(e)[:120]}
try:
    c = sqlite3.connect("data/cnq.sqlite")
    rows = c.execute("SELECT symbol, MAX(open_time), COUNT(*) FROM candles WHERE interval='15m' GROUP BY symbol").fetchall()
    st["candles"] = {r[0]: {"last": r[1], "n": r[2]} for r in rows}
except Exception as e:  # noqa: BLE001
    st["candles_error"] = str(e)[:120]
try:
    c = sqlite3.connect("data/agents.sqlite")
    st["paper_trades"] = c.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    st["spot_positions"] = c.execute("SELECT COUNT(*) FROM spot_portfolio WHERE status='OPEN'").fetchone()[0]
except Exception as e:  # noqa: BLE001
    st["state_error"] = str(e)[:120]
os.makedirs("web", exist_ok=True)
json.dump(st, open("web/status.json", "w"), ensure_ascii=False)
print(json.dumps({k: v for k, v in st.items() if k != "candles"}, ensure_ascii=False))
