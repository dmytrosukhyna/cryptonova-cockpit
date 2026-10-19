"""Is the server engine (cnq/engine.py) running the trading desks? Prints `server=true` for $GITHUB_OUTPUT when web/engine.json
was written by it less than 10 minutes ago — then the GitHub pipeline skips those steps (one writer per file). If the server goes
quiet, the next GitHub run takes the desks back by itself (automatic failover). 19.10.2026."""
import json
import time

try:
    d = json.load(open("web/engine.json", encoding="utf-8"))
    age_min = (time.time() * 1000 - float(d.get("asof") or 0)) / 60000
except Exception:  # noqa: BLE001
    age_min = None
alive = age_min is not None and age_min < 10
print(f"server={'true' if alive else 'false'}")
print(f"age_min={round(age_min, 1) if age_min is not None else 'none'}")
