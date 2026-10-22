"""Why the panel's A/B forecast misses (backlog #11, 21.10.2026).

Reads the resolved forecasts in data/agents.sqlite (table forecasts, written by cnq/agents/calibration.py) and scores
a fixed list of variants — no fitting, every variant is named here before the run:
  V0 current p_up; V1 the inverse 1-p ("do the opposite"); V2 the first-touch geometry alone
  (random walk: P(up edge first) = distance to the lower edge / both distances); V3 geometry + all current terms;
  V4 current without the two slow terms (long regime, BTC regime); V5 geometry + the fast terms only (= the shadow
  forecast in calibration.shadow_p); V6 coin flip.
Shown: hit rate and Brier for the whole sample, first/second half of the days and each day (the 18 coins move
together, so the honest sample size is the number of DAYS, not forecasts). Optional --since <ms> scores only
forecasts made after a cut-off (forward check). Output: stdout + GitHub annotations.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents.calibration import shadow_p  # noqa: E402


def _clip(x: float) -> float:
    return max(.22, min(.78, x))


def _geom(r: dict) -> float:
    du, dd = r["up"] - r["price"], r["price"] - r["dn"]
    return dd / (du + dd) if du > 0 and dd > 0 else .5


VARIANTS = {
    "V0 текущий": lambda r: r["p"],
    "V1 обратный 1-p": lambda r: 1 - r["p"],
    "V2 геометрия": _geom,
    "V3 геометрия+все": lambda r: _clip(_geom(r) + sum(r["c"].values())),
    "V4 без long/btc": lambda r: _clip(r["p"] - r["c"].get("long", 0) - r["c"].get("btc", 0)),
    "V5 тень": lambda r: shadow_p(r["price"], r["up"], r["dn"], r["c"]),
    "V6 монетка": lambda r: .5,
}


def load(path: str, since: int = 0) -> list[dict]:
    c = sqlite3.connect(path)
    q = "SELECT time,symbol,p_up,price,up_target,dn_target,outcome,components FROM forecasts WHERE outcome IS NOT NULL AND time>=? ORDER BY time"
    return [{"t": t, "day": t // 86_400_000, "s": s, "p": p, "price": pr, "up": u, "dn": d, "y": 1.0 if o == "UP" else 0.0,
             "c": json.loads(cm or "{}")} for t, s, p, pr, u, d, o, cm in c.execute(q, (since,))]


def score(f, rs: list[dict]) -> tuple[float, float]:
    n = len(rs) or 1
    return (sum((f(r) >= .5) == (r["y"] == 1) for r in rs) / n, sum((f(r) - r["y"]) ** 2 for r in rs) / n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/agents.sqlite")
    ap.add_argument("--since", type=int, default=0)
    a = ap.parse_args()
    R = load(a.db, a.since)
    if not R:
        print("::warning title=calibration::no resolved forecasts"); return
    days = sorted({r["day"] for r in R}); h = len(days) // 2
    dname = lambda d: datetime.datetime.fromtimestamp(d * 86400, datetime.UTC).strftime("%d.%m")
    up = sum(r["y"] for r in R) / len(R)
    slow_on = sum(1 for r in R if r["c"].get("long", 0) + r["c"].get("btc", 0) > 0) / len(R)
    head = f"n={len(R)}, дней={len(days)} ({dname(days[0])}–{dname(days[-1])}), доля UP={up:.3f}, медленный плюс включён в {slow_on:.0%}"
    print(head); print(f"::notice title=calibration · sample::{head}")
    for k, f in VARIANTS.items():
        a_, b_, c_ = score(f, R), score(f, [r for r in R if r["day"] in days[:h]]), score(f, [r for r in R if r["day"] in days[h:]])
        pd = " ".join(f"{dname(d)}:{score(f, [r for r in R if r['day'] == d])[0]:.2f}" for d in days)
        line = f"hit {a_[0]:.3f} Brier {a_[1]:.4f} | 1-я пол. {b_[0]:.3f}/{b_[1]:.4f} | 2-я {c_[0]:.3f}/{c_[1]:.4f} | {pd}"
        print(f"{k:20s} {line}"); print(f"::notice title=calibration · {k}::{line}")
    comps: dict[str, list[bool]] = {}
    for r in R:
        for k, v in r["c"].items():
            if v:
                comps.setdefault(k, []).append((v > 0) == (r["y"] == 1))
    cl = " · ".join(f"{k} {sum(v)/len(v):.2f} (n={len(v)})" for k, v in sorted(comps.items()))
    print("terms:", cl); print(f"::notice title=calibration · terms (sign right)::{cl}")


if __name__ == "__main__":
    main()
