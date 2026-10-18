"""Shorting before large token unlocks — a study (18.10.2026; research: about 90 % of 16 000+ unlocks pushed the price down,
from about 30 days before, team / investor unlocks the worst — Keyrock 2024). Runs in GitHub Actions only
(.github/workflows/research-unlocks.yml): the research container's proxy blocks the Binance archive.

Events: scripts/lab/data/unlocks_2024_2026.json (≈ 300 dated unlocks from public sources, each with a source URL; recurring-rule
rows and future events are skipped). Prices: Binance USDT-M perpetual daily klines + funding from data.binance.vision; an event
counts only if its perp traded ≥ 45 days before the unlock and ≥ 7 days after.

Trades (short the token): entry = the daily open `k` days before the unlock (k = 30 / 14 / 7 / 3 / 1); exit = the daily close
`j` days after it (j = 0 / 3 / 7); optional stop (entry × (1 + s), filled 1 % worse); costs 0.14 %; funding received / paid.
Also a HEDGED version: short the token + long BTC for the same sum — what is left is the token's move against the market (the
market's own fall is not an edge). Splits: event class (one-off cliff vs periodic tranche), recipients (team / investors vs
other), size (% of circulating supply), year. Output: annotations + listings-style job summary. Stdlib + numpy."""
import datetime as dt
import io
import itertools
import json
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import VISION, http, months_between, note  # noqa: E402

DAY = 86_400_000
COST, SLIP = 0.0014, 0.01


def read_zip(b: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        rows = [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines()]
    return [r for r in rows if r and r[0][:1].isdigit()]


def daily(sym: str, d0: dt.date, d1: dt.date) -> dict | None:
    """{day_ms: (open, high, low, close)} from the monthly 1d archives (daily ones for the current month)."""
    out = {}
    today = dt.datetime.now(dt.timezone.utc).date()
    for mo in months_between(d0.strftime("%Y-%m"), min(d1, today).strftime("%Y-%m")):
        s, b = http(f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{mo}.zip", binary=True)
        rows = read_zip(b) if s == 200 else []
        if s != 200 and mo == today.strftime("%Y-%m"):
            d = dt.date(int(mo[:4]), int(mo[5:]), 1)
            while d < today:
                s2, b2 = http(f"{VISION}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip", binary=True)
                if s2 == 200:
                    rows += read_zip(b2)
                d += dt.timedelta(days=1)
        for r in rows:
            t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
            out[t - t % DAY] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    return out or None


def funding(sym: str, d0: dt.date, d1: dt.date) -> list:
    out = []
    for mo in months_between(d0.strftime("%Y-%m"), d1.strftime("%Y-%m")):
        s, b = http(f"{VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip", binary=True)
        if s == 200:
            out += [(int(r[0]), float(r[2])) for r in read_zip(b)]
    return out


def load(ev: dict) -> dict | None:
    sym = ev["symbol"].upper() + "USDT"
    day = dt.date.fromisoformat(ev["date"])
    k = daily(sym, day - dt.timedelta(days=50), day + dt.timedelta(days=10))
    if not k:
        return None
    t0 = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    if min(k) > t0 - 45 * DAY or max(k) < t0 + 7 * DAY:
        return None
    return {"ev": ev, "k": k, "t0": t0, "f": funding(sym, day - dt.timedelta(days=35), day + dt.timedelta(days=10))}


def trade(L: dict, btc: dict, kb: int, ja: int, stop) -> tuple | None:
    k, t0 = L["k"], L["t0"]
    te, tx = t0 - kb * DAY, t0 + ja * DAY
    if te not in k or tx not in k or te not in btc or tx not in btc:
        return None
    ent = k[te][0]
    ret = None
    if stop is not None:
        for t in range(te, tx + DAY, DAY):
            if t in k and k[t][1] >= ent * (1 + stop):
                ret, tx_eff = -(stop + SLIP), t
                break
    if ret is None:
        ret, tx_eff = 1 - k[tx][3] / ent, tx
    fund = sum(v for ts, v in L["f"] if te <= ts < tx_eff + DAY)
    b_ret = btc[min(tx_eff, tx)][3] / btc[te][0] - 1          # BTC over the same days (a long hedge)
    short = ret + fund - COST
    return short, short + b_ret - COST                        # raw short, short token + long BTC


def stats(xs):
    v = np.array([x for x in xs if x is not None])
    if len(v) < 8:
        return None
    return {"n": len(v), "avg_pct": round(float(v.mean()) * 100, 2), "median_pct": round(float(np.median(v)) * 100, 2),
            "win_pct": round(float((v > 0).mean()) * 100), "worst_pct": round(float(v.min()) * 100, 1)}


def focus_notes(loaded, btc, pc):
    """Do the strong groups (big unlocks, one-off cliffs) still work in 2026, or is the edge fading everywhere?"""
    groups = [("all", lambda e: True), ("≥ 20% circ", lambda e: pc(e) >= 20), ("≥ 5% circ", lambda e: pc(e) >= 5),
              ("one-off cliff", lambda e: e["event_class"] == "one_off_cliff"),
              ("cliff ≥ 5%", lambda e: e["event_class"] == "one_off_cliff" and pc(e) >= 5)]
    for kb, ja in ((30, 0), (14, 0), (30, 3)):
        out = {}
        for lab, fn in groups:
            rs = [(x["ev"]["date"][:4], trade(x, btc, kb, ja, 0.35)) for x in loaded if fn(x["ev"])]
            out[lab] = {y: stats([r[1] for yy, r in rs if r and (y == "all" or yy == y)]) for y in ("all", "2024", "2025", "2026")}
        note(f"unlocks · focus {kb} d before → +{ja} d, stop 35%, hedged", json.dumps(out, ensure_ascii=False))
    worst = sorted(((trade(x, btc, 30, 0, None) or (0, 0))[1], x["ev"]["symbol"], x["ev"]["date"]) for x in loaded)[:8]
    note("unlocks · worst (30 d → 0, no stop, hedged)", "; ".join(f"{s} {d} {r * 100:.0f}%" for r, s, d in worst))


def main():
    data = json.load(open(os.path.join(os.path.dirname(__file__), "lab", "data", "unlocks_2024_2026.json"), encoding="utf-8"))
    today = dt.datetime.now(dt.timezone.utc).date()
    evs = [e for e in data["events"] if e.get("date") and not e.get("recurring") and not e.get("future")
           and dt.date.fromisoformat(e["date"]) <= today - dt.timedelta(days=8)]
    with ThreadPoolExecutor(12) as ex:
        loaded = [x for x in ex.map(load, evs) if x]
    btc = daily("BTCUSDT", dt.date(2023, 11, 1), today)
    focus = "--focus" in sys.argv      # a 2nd workflow step: GitHub keeps only 10 notices per step
    if not focus:
        note("unlocks", f"{len(evs)} events, {len(loaded)} with a Binance perp ≥ 45 d before and ≥ 7 d after; "
                        f"one-off cliffs {sum(1 for x in loaded if x['ev']['event_class'] == 'one_off_cliff')}, "
                        f"team/investors {sum(1 for x in loaded if x['ev']['recipients'] in ('team', 'investors', 'team+investors'))}")
    pc = lambda e: e.get("pct_circ") or 0            # noqa: E731
    if focus:
        return focus_notes(loaded, btc, pc)
    grid = []
    for kb, ja, stop in itertools.product((30, 14, 7, 3, 1), (0, 3, 7), (None, 0.2, 0.35)):
        rs = [(x, trade(x, btc, kb, ja, stop)) for x in loaded]
        rs = [(x, r) for x, r in rs if r]
        raw, hed = [r[0] for _, r in rs], [r[1] for _, r in rs]
        yrs = [x["ev"]["date"][:4] for x, _ in rs]
        grid.append({"enter_days_before": kb, "exit_days_after": ja, "stop": stop, "raw": stats(raw), "hedged": stats(hed),
                     "hedged_by_year": {y: stats([h for h, yy in zip(hed, yrs) if yy == y]) for y in ("2024", "2025", "2026")}})
    ok = [g for g in grid if g["hedged"]]
    best = sorted(ok, key=lambda g: -g["hedged"]["avg_pct"])[:4]
    share = round(sum(1 for g in ok if g["hedged"]["avg_pct"] > 0) / max(1, len(ok)) * 100)
    note("unlocks · grid", f"{len(ok)} variants; hedged (short token + long BTC) positive in {share}%; "
                           f"raw short positive in {round(sum(1 for g in ok if g['raw'] and g['raw']['avg_pct'] > 0) / max(1, len(ok)) * 100)}%")
    for g in best:
        note("unlocks · best hedged", json.dumps(g, ensure_ascii=False))
    # splits for a fixed, simple rule: enter 14 days before, exit on the unlock day, no stop
    def split(name, groups):
        out = {}
        for lab, fn in groups:
            rs = [trade(x, btc, 14, 0, None) for x in loaded if fn(x["ev"])]
            out[lab] = {"raw": stats([r[0] for r in rs if r]), "hedged": stats([r[1] for r in rs if r])}
        note(f"unlocks · split {name} (14 d before → day 0)", json.dumps(out, ensure_ascii=False))
    team = ("team", "investors", "team+investors")
    split("class", [("one-off cliff", lambda e: e["event_class"] == "one_off_cliff"), ("periodic", lambda e: e["event_class"] == "periodic_tranche")])
    split("recipients", [("team/investors", lambda e: e["recipients"] in team), ("other", lambda e: e["recipients"] not in team)])
    split("size", [("< 2%", lambda e: 0 < pc(e) < 2), ("2–5%", lambda e: 2 <= pc(e) < 5), ("5–20%", lambda e: 5 <= pc(e) < 20), ("≥ 20%", lambda e: pc(e) >= 20)])
    split("year", [(y, (lambda yy: lambda e: e["date"][:4] == yy)(y)) for y in ("2024", "2025", "2026")])
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Шорт перед разлоками\n```\n" + json.dumps(best, ensure_ascii=False, indent=1) + "\n```\n")


if __name__ == "__main__":
    main()
