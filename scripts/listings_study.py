"""Shorting new perpetual listings — a study (18.10.2026, owner: «после листинга новые монеты сразу обваливаются, люди шортят
листинги на MEXC, Bybit и зарабатывают»). Runs in GitHub Actions only (.github/workflows/research-listings.yml): the research
container's proxy blocks the Binance archive.

Data: every Binance USDT-M perpetual in the public archive (data.binance.vision, delisted included) whose first 1h kline is
2023-01-01 or later = a listing. For each: 1h klines for the first 120 days and the funding-rate history of that period.

Trades (short): entry = open of the bar `delay` hours after the first bar; exit at the close `hold` days later, or at the stop
(entry × (1 + stop), filled at the stop + 1 % slippage — squeezes gap). Costs 0.14 % round trip. Funding is INCLUDED: a short
receives positive funding and pays negative funding, every 8 h (or the symbol's own interval) while the trade is open.
Also: the plain price path after listing (median change after 1 / 7 / 30 / 90 days, share down, share up > 50 %).

Output: listings.json (artifact) + the main table as the job summary and annotations. Stdlib + numpy."""
import argparse
import csv
import datetime as dt
import io
import itertools
import json
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import VISION, http, months_between, note, vision_symbols  # noqa: E402

H = 3_600_000
SINCE = "2023-01"
DAYS = 120
COST, SLIP = 0.0014, 0.01


def first_month_1h(sym: str, today: str) -> str | None:
    """First month of the symbol's 1h kline archive (one S3 listing request), if it is SINCE or later; None otherwise."""
    s, b = http("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix="
                f"data/futures/um/monthly/klines/{sym}/1h/")
    if s != 200:
        return None
    months = sorted(set(re.findall(rf"{sym}-1h-(\d{{4}}-\d{{2}})\.zip<", b)))
    if not months:                       # listed this month: only daily archives so far
        return today if http(f"{VISION}/daily/klines/{sym}/1h/{sym}-1h-{dt.date.today() - dt.timedelta(days=2):%Y-%m-%d}.zip",
                              head=True)[0] == 200 else None
    return months[0] if months[0] >= SINCE else None


def read_zip(b: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        txt = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(txt)))
    if rows and not rows[0][0].replace(".", "").replace("-", "").isdigit():
        rows = rows[1:]
    return rows


def series(sym: str, kind: str, first: str, today: dt.date) -> list:
    """kind = klines (1h) | fundingRate. Monthly archives; daily ones for the current month (klines only)."""
    out = []
    end = dt.date(int(first[:4]), int(first[5:]), 1) + dt.timedelta(days=DAYS + 35)
    for mo in months_between(first, min(end, today).strftime("%Y-%m")):
        url = (f"{VISION}/monthly/klines/{sym}/1h/{sym}-1h-{mo}.zip" if kind == "klines"
               else f"{VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip")
        s, b = http(url, binary=True)
        if s == 200:
            out += read_zip(b)
        elif kind == "klines" and mo == today.strftime("%Y-%m"):
            d = dt.date(int(mo[:4]), int(mo[5:]), 1)
            while d < today:
                s, b = http(f"{VISION}/daily/klines/{sym}/1h/{sym}-1h-{d:%Y-%m-%d}.zip", binary=True)
                if s == 200:
                    out += read_zip(b)
                d += dt.timedelta(days=1)
    return out


def load(sym: str, today: dt.date) -> dict | None:
    first = first_month_1h(sym, today.strftime("%Y-%m"))
    if not first:
        return None
    kl = series(sym, "klines", first, today)
    if len(kl) < 48:
        return None
    t = np.array([int(r[0]) // (1000 if int(r[0]) > 10 ** 14 else 1) for r in kl], dtype=np.int64)
    o = np.array([float(r[1]) for r in kl]); h = np.array([float(r[2]) for r in kl]); c = np.array([float(r[4]) for r in kl])
    q = np.array([float(r[7]) for r in kl])
    idx = np.argsort(t)
    t, o, h, c, q = t[idx], o[idx], h[idx], c[idx], q[idx]
    fr = series(sym, "fundingRate", first, today)
    ft = np.array([int(r[0]) for r in fr], dtype=np.int64) if fr else np.array([], dtype=np.int64)
    fv = np.array([float(r[2]) for r in fr]) if fr else np.array([])
    return {"sym": sym, "t": t, "o": o, "h": h, "c": c, "q": q, "ft": ft, "fv": fv, "listed": int(t[0])}


def short(L: dict, delay_h: int, hold_d: int, stop) -> tuple | None:
    t, o, h, c = L["t"], L["o"], L["h"], L["c"]
    e = delay_h
    x = e + hold_d * 24
    if x >= len(t) or t[x] - t[e] > (hold_d * 24 + 6) * H:          # a gap in the data (delisting) → skip
        return None
    ent = o[e]
    exit_i, ret = x, None
    if stop is not None:
        hit = np.where(h[e:x + 1] >= ent * (1 + stop))[0]
        if len(hit):
            exit_i = e + int(hit[0])
            ret = -(stop + SLIP)
    if ret is None:
        ret = 1 - c[x] / ent                                         # linear USDT-M short: (entry − exit) / entry
    m = (L["ft"] >= t[e]) & (L["ft"] < t[exit_i] + H)
    fund = float(L["fv"][m].sum()) if len(L["fv"]) else 0.0           # short receives +rate
    return ret + fund - COST, fund, int(L["listed"])


def study(a):
    today = dt.datetime.now(dt.timezone.utc).date()
    syms = sorted(s for s in vision_symbols() if s.endswith("USDT"))
    note("listings", f"{len(syms)} USDT-M symbols in the archive")
    with ThreadPoolExecutor(16) as ex:
        data = [d for d in ex.map(lambda s: load(s, today), syms) if d]
    note("listings", f"{len(data)} listed {SINCE} or later with ≥ 2 days of 1h data")
    res = {"asof": today.isoformat(), "n_listings": len(data),
           "per_year": {y: sum(1 for d in data if dt.datetime.fromtimestamp(d["listed"] / 1000, dt.timezone.utc).year == y) for y in (2023, 2024, 2025, 2026)}}
    path = {}
    for days in (1, 7, 30, 90):
        xs = [d["c"][1 + days * 24] / d["o"][1] - 1 for d in data if len(d["c"]) > 1 + days * 24]
        if xs:
            v = np.array(xs)
            path[f"{days}d"] = {"n": len(v), "median_pct": round(float(np.median(v)) * 100, 1), "down_pct": round(float((v < 0).mean()) * 100),
                                "down_30_pct": round(float((v < -0.3).mean()) * 100), "up_50_pct": round(float((v > 0.5).mean()) * 100, 1),
                                "max_pct": round(float(v.max()) * 100)}
    res["price_after_listing"] = path
    grid = []
    for delay, hold, stop in itertools.product((1, 4, 24, 72), (1, 3, 7, 14, 30), (None, 0.3, 0.5, 1.0)):
        rs = [r for d in data if (r := short(d, delay, hold, stop)) is not None]
        if len(rs) < 30:
            continue
        v = np.array([r[0] for r in rs]); f = np.array([r[1] for r in rs])
        yrs = np.array([dt.datetime.fromtimestamp(r[2] / 1000, dt.timezone.utc).year for r in rs])
        by = {int(y): {"n": int((yrs == y).sum()), "avg_pct": round(float(v[yrs == y].mean()) * 100, 1),
                       "median_pct": round(float(np.median(v[yrs == y])) * 100, 1), "win_pct": round(float((v[yrs == y] > 0).mean()) * 100)}
              for y in sorted(set(yrs))}
        rec = yrs >= 2025
        grid.append({"delay_h": delay, "hold_d": hold, "stop": stop, "n": len(v), "avg_pct": round(float(v.mean()) * 100, 2),
                     "median_pct": round(float(np.median(v)) * 100, 1), "win_pct": round(float((v > 0).mean()) * 100),
                     "worst_pct": round(float(v.min()) * 100), "funding_avg_pct": round(float(f.mean()) * 100, 2),
                     "years": by, "pos_years": sum(1 for b in by.values() if b["avg_pct"] > 0),
                     "recent_n": int(rec.sum()), "recent_avg_pct": round(float(v[rec].mean()) * 100, 2) if rec.any() else None,
                     # one listing = 5 % of an account, no overlap limit: account-level result per year ≈ 5 % × sum
                     "acct_5pct_by_year": {int(y): round(float(v[yrs == y].sum()) * 5, 1) for y in sorted(set(yrs))}})
    res["grid"] = grid
    # 18.10 (2nd pass): the chosen rule (4 h / 14 d / stop +30 %) split by what we can know at entry — the listing's first 4 h move,
    # its first-4 h turnover, the funding sign at entry and the BTC regime (BTC above its 200-day average, Binance archive daily)
    if a.splits:
        btc = []
        for mo in months_between("2022-01", today.strftime("%Y-%m")):
            st, bb = http(f"{VISION}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{mo}.zip", binary=True)
            if st == 200:
                btc += read_zip(bb)
        btc.sort(key=lambda r: int(r[0]))
        day_t = np.array([int(r[0]) for r in btc]); day_c = np.array([float(r[4]) for r in btc])
        def btc_bull(ms):
            i = np.searchsorted(day_t, ms) - 1
            return bool(i >= 200 and day_c[i] > day_c[i - 200:i].mean()) if i >= 0 else None
        rows = []
        for d in data:
            r = short(d, 4, 14, 0.3)
            if r is None:
                continue
            m4 = d["c"][3] / d["o"][0] - 1 if len(d["c"]) > 4 else None
            q4 = float(d["q"][:4].sum())
            f0 = None
            if len(d["fv"]):
                k = np.searchsorted(d["ft"], d["t"][4]) - 1
                f0 = float(d["fv"][k]) if k >= 0 else None
            rows.append({"ret": r[0], "m4": m4, "q4": q4, "f0": f0, "bull": btc_bull(d["t"][4]),
                         "year": dt.datetime.fromtimestamp(d["listed"] / 1000, dt.timezone.utc).year})
        def bucket(name, key):
            out = {}
            for lab, fn in key:
                v = np.array([x["ret"] for x in rows if fn(x)])
                rec = np.array([x["ret"] for x in rows if fn(x) and x["year"] >= 2025])
                if len(v) >= 15:
                    out[lab] = {"n": len(v), "avg_pct": round(float(v.mean()) * 100, 2), "win_pct": round(float((v > 0).mean()) * 100),
                                "n_2025_26": len(rec), "avg_2025_26_pct": round(float(rec.mean()) * 100, 2) if len(rec) else None}
            note(f"listings · split by {name}", json.dumps(out, ensure_ascii=False))
            return out
        qs = sorted(x["q4"] for x in rows)
        q_lo, q_hi = (qs[len(qs) // 3], qs[2 * len(qs) // 3]) if qs else (0, 0)
        res["splits"] = {
            "first_4h_move": bucket("first 4h move", [("упала ≥ 10%", lambda x: x["m4"] is not None and x["m4"] <= -0.10),
                                                      ("−10…+10%", lambda x: x["m4"] is not None and -0.10 < x["m4"] < 0.10),
                                                      ("выросла ≥ 10%", lambda x: x["m4"] is not None and x["m4"] >= 0.10),
                                                      ("выросла ≥ 30%", lambda x: x["m4"] is not None and x["m4"] >= 0.30)]),
            "first_4h_turnover": bucket("first 4h turnover", [("нижняя треть", lambda x: x["q4"] <= q_lo), ("средняя", lambda x: q_lo < x["q4"] <= q_hi),
                                                              ("верхняя треть", lambda x: x["q4"] > q_hi)]),
            "funding_at_entry": bucket("funding at entry", [("отрицательный (шорты платят)", lambda x: x["f0"] is not None and x["f0"] < 0),
                                                            ("≥ 0", lambda x: x["f0"] is not None and x["f0"] >= 0)]),
            "btc_regime": bucket("BTC regime", [("BTC выше 200-дневной", lambda x: x["bull"] is True), ("BTC ниже", lambda x: x["bull"] is False)]),
        }
    res["share_positive"] = round(sum(1 for g in grid if g["avg_pct"] > 0) / max(1, len(grid)) * 100)
    res["share_positive_2025_26"] = round(sum(1 for g in grid if (g["recent_avg_pct"] or -1) > 0) / max(1, len(grid)) * 100)
    with open(a.out, "w") as fh:
        json.dump(res, fh)
    note("listings · price after listing", json.dumps(path, ensure_ascii=False))
    best = sorted([g for g in grid if g["stop"] is not None], key=lambda g: -(g["recent_avg_pct"] or -99))[:10]
    lines = [f"| {g['delay_h']} ч | {g['hold_d']} д | {int(g['stop'] * 100)}% | {g['n']} | {g['avg_pct']} | {g['median_pct']} | {g['win_pct']} | "
             f"{g['worst_pct']} | {g['funding_avg_pct']} | {g['recent_avg_pct']} ({g['recent_n']}) | {g['pos_years']}/{len(g['years'])} |" for g in best]
    md = ["## Шорт новых листингов Binance USDT-M", f"Листингов: {len(data)} ({res['per_year']}). Вариантов: {len(grid)}, в плюсе: "
          f"{res['share_positive']}%, в плюсе на 2025–26: {res['share_positive_2025_26']}%.", "",
          "| вход | держим | стоп | n | средн. % | медиана % | в плюсе % | худшая % | фандинг % | 2025–26 % (n) | лет в плюсе |",
          "|---|---|---|---|---|---|---|---|---|---|---|", *lines]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("\n".join(md) + "\n")
    for g in best[:5]:
        note("listings · best with stop", json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="listings.json")
    ap.add_argument("--splits", action="store_true", help="split the chosen rule by what is known at entry")
    study(ap.parse_args())
