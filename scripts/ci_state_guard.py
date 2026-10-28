"""State guard for the GitHub pipeline (28.10.2026) — the spot portfolio department must never run on a ROLLED-BACK database.

What happened (evidence: web/portfolio.json history): on 23.10 07:21 UTC run 39916945010 of site.yml got no department state from
the Actions cache and worked on the copy of data/agents.sqlite that is committed in the repository — frozen at 20.10 09:43 UTC since
the database passed the 95 MB commit guard. «CRYPTO NOVA портфель» (the model the REAL OKX account mirrors) was thrown back to its
20.10 state and replayed the 22.10 decisions at 23.10 08:06 (XMR / USELESS / CASHCAT sold again, the core topped up again, HYPE
bought again); every action between 20.10 09:43 and 23.10 08:06 is gone from its ledger, and the candle store had to be rebuilt
(the candles step took 40 minutes). Had the 20.10 copy been used today, the model would hold NEAR and AAVE again and the autopilot
would buy them back on the real account.

The guard compares the database with what the pipeline already PUBLISHED: the latest index point of «CRYPTO NOVA портфель» in
data/agents.sqlite (spot_ideas_value) against the latest point in web/portfolio.json (portfolios.ideas.history). If the database is
older by more than MAX_LAG_H hours (or empty while a portfolio was published), the state is stale: it prints a GitHub error,
writes data/state_guard.json and outputs stale=true — site.yml then skips the spot portfolio step, so web/portfolio.json keeps the last
good model and the autopilot keeps the account as it is (no new buys or sells from a wrong state) until the state is restored.
Nothing else is changed. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

MAX_LAG_H = 2.0


def db_last(path: str = "data/agents.sqlite") -> int | None:
    if not os.path.exists(path):
        return None
    try:
        c = sqlite3.connect(path)
        try:
            r = c.execute("SELECT MAX(time) FROM spot_ideas_value").fetchone()
        finally:
            c.close()
        return int(r[0]) if r and r[0] else None
    except Exception:  # noqa: BLE001
        return None


def published_last(path: str = "web/portfolio.json") -> int | None:
    try:
        with open(path, encoding="utf-8") as f:
            pf = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    p = ((pf or {}).get("portfolios") or {}).get("ideas") or {}
    pts = [h.get("time") for h in (p.get("history_all") or []) + (p.get("history") or []) if isinstance(h, dict) and h.get("time")]
    return int(max(pts)) if pts else None


def verdict(t_db: int | None, t_pub: int | None, now: int, max_lag_h: float = MAX_LAG_H) -> dict:
    """{"stale": bool, "why_ru", "db_t", "pub_t", "lag_h", "checked"}"""
    lag = None if (t_db is None or t_pub is None) else (t_pub - t_db) / 3_600_000
    if t_pub is None:
        stale, why = False, "Опубликованного портфеля нет — сравнивать не с чем."
    elif t_db is None:
        stale, why = True, "В базе нет истории «CRYPTO NOVA портфель», а портфель уже опубликован — база потеряна."
    elif lag > max_lag_h:
        stale, why = True, (f"База отстаёт от опубликованного портфеля на {lag:.1f} ч — это старая копия состояния; "
                            "шаг спот-портфеля пропущен, чтобы портфель (и реальный счёт за ним) не откатился назад.")
    else:
        stale, why = False, "База совпадает с опубликованным портфелем."
    return {"stale": stale, "why_ru": why, "db_t": t_db, "pub_t": t_pub, "lag_h": None if lag is None else round(lag, 2), "checked": now}


def main(argv=None) -> int:
    now = int(time.time() * 1000)
    v = verdict(db_last(), published_last(), now)
    os.makedirs("data", exist_ok=True)
    with open("data/state_guard.json", "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"stale={'true' if v['stale'] else 'false'}\n")
    if v["stale"]:
        print(f"::error title=state rollback::{v['why_ru']} (db {v['db_t']}, published {v['pub_t']})")
    else:
        print("state ok:", v["why_ru"], f"(lag {v['lag_h']} h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
