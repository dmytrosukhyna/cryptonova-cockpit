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
# 18.10: every file the server should refresh each run — a step that fails «continue-on-error» leaves its file old (the signals
# feed was silently stuck for 4 h); stale or erroring files are listed and raised as GitHub warnings
WATCH = {"capital.json": 2, "yield.json": 2, "basis.json": 2, "signals_feed.json": 2, "paper.json": 2, "portfolio.json": 2,
         "ideas.json": 2, "markets.json": 2, "research.json": 3, "funding.json": 2, "tokenomics.json": 26, "channel_watch.json": 2, "channel_opps.json": 2,
         "app_signals.json": 3, "decision.json": 2}
st["stale"] = []
for f, max_h in WATCH.items():
    try:
        d = json.load(open(os.path.join("web", f)))
    except Exception as e:  # noqa: BLE001
        st["stale"].append({"file": f, "why": f"нет файла или он битый: {str(e)[:80]}"})
        continue
    a_ = d.get("asof") or d.get("time") if isinstance(d, dict) else None
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(a_, (int, float)) and a_ > 1e12 and (now - a_) / 3.6e6 > max_h:
        st["stale"].append({"file": f, "why": f"не обновлялся {(now - a_) / 3.6e6:.1f} ч"})
    elif err:
        st["stale"].append({"file": f, "why": f"ошибка: {str(err)[:100]}"})
try:   # the unlock calendar is kept by hand / by the weekly review: older than 3 weeks → it misses new unlocks
    import datetime as _dt
    built = json.load(open("web/tokenomics.json")).get("calendar_built")
    age = (_dt.date.today() - _dt.date.fromisoformat(built)).days if built else None
    if age is None or age > 21:
        st["stale"].append({"file": "scripts/lab/data/unlocks_upcoming.json",
                            "why": "календаря разлоков нет" if age is None else f"календарь разлоков собран {age} дн. назад — обновить"})
except Exception:  # noqa: BLE001
    pass
for x in st["stale"]:
    print(f"::warning title=stale {x['file']}::{x['why']}")
os.makedirs("web", exist_ok=True)
json.dump(st, open("web/status.json", "w"), ensure_ascii=False)
print(json.dumps({k: v for k, v in st.items() if k != "candles"}, ensure_ascii=False))
