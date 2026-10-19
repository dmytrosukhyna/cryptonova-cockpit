"""Golden signals on 10 months of history (19.10.2026, owner: «голден сигналс — с ними нужно хорошо поработать»; he sent the
Cornix export of «Ai Golden Crypto (Auto Bot)» — 3 047 signals, 17.12.2025 → 19.10.2026). Runs in GitHub Actions only
(.github/workflows/research-golden-history.yml): the research container cannot reach the Binance archive.

Input: scripts/lab/data/golden_signals.json — time, coin, side, signal leverage, entry zone (no account data). The channel's
geometry is fixed: stop = 30 % ÷ leverage from the entry, five targets every 15 % ÷ leverage (checked on the 321 signals of the
app API, 18.09–18.10.2026: leverage × stop distance = 30.0 %, IQR 29.9–30.1; target step = stop ÷ 2).

Path: Binance USDT-M 5-minute candles (data.binance.vision). Entry: market (filled at the entry at the signal time) when the price
at the signal candle is within 0.6 % of the entry, else a limit order touched within 48 h (else not filled). Exits from the NEXT
5-minute candle; a candle touching the stop and a target → the stop first (cautious); no exit in 14 days → closed at the market.
Costs 0.12 % per trade. Variants (all on the same signals):
  ladder_be   as they come, equal fifths at each target, stop to the entry after TP1 (the app's current way)
  last        as they come, everything to the last target or the stop
  rev         the mirrored trade (other side, stop and targets mirrored), to the last target or the stop
  smart       with Bitcoin's last closed 4-hour candle → as they come, against it → mirrored (both to the last target / stop)
  both        the signal and its mirror together, half the margin each
Demo account each: $10 000, 5 % margin per trade, the signal's leverage capped at 20×, compounded in time order. Result: per
month and in total, the worst drawdown; plus a check of this 5-minute simulation against our 1-minute one on the overlapping
signals (data/agents.sqlite). Stdlib + numpy."""
import datetime as dt
import io
import json
import os
import sqlite3
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note  # noqa: E402

UM = "https://data.binance.vision/data/futures/um"
M5, H4, DAY = 300_000, 14_400_000, 86_400_000
COST = 0.12
MARKET_TOL = 0.006
FILL_WIN = 48 * 12                 # 48 h of 5-minute candles
HOLD = 14 * 288                    # 14 days
TODAY = dt.datetime.now(dt.timezone.utc).date()
ALIAS = {"TRUMPSOLUSDT": "TRUMPUSDT"}


def _zip(b: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]


def klines(sym: str, iv: str, d0: dt.date, d1: dt.date) -> dict | None:
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1))
    rows = []
    for mo in months_between(d0.strftime("%Y-%m"), min(d1, last_full).strftime("%Y-%m")):
        s, b = http(f"{UM}/monthly/klines/{sym}/{iv}/{sym}-{iv}-{mo}.zip", binary=True)
        if s == 200:
            rows += _zip(b)
    if d1 > last_full:
        d = max(d0, TODAY.replace(day=1))
        while d < TODAY:
            s, b = http(f"{UM}/daily/klines/{sym}/{iv}/{sym}-{iv}-{d:%Y-%m-%d}.zip", binary=True)
            if s == 200:
                rows += _zip(b)
            d += dt.timedelta(days=1)
    if not rows:
        return None
    ms = lambda v: int(v) // 1000 if int(v) > 10 ** 14 else int(v)        # noqa: E731 — some archives are in microseconds
    rows.sort(key=lambda r: ms(r[0]))
    a = np.array([[ms(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows])
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4]}


def simulate(K: dict, sig: dict, side: str, iv: int = M5) -> dict | None:
    """One trade on candles of `iv` ms → {fill_t, x: {k: (net %, close ms)} for «everything out at target k or the stop»,
    last / last_t (= k 5), ladder_be / lad_t} — net % of price after costs, no leverage."""
    t, o, h, l, c = K["t"], K["o"], K["h"], K["l"], K["c"]
    i0 = int(np.searchsorted(t, sig["t"], side="right")) - 1
    if i0 < 0 or i0 + 2 >= len(t) or sig["t"] - t[i0] > 2 * iv:
        return None
    lo, hi = sig["lo"], sig["hi"]
    E = (lo + hi) / 2 if lo and hi else (lo or hi)
    if not E:
        return None
    fill_i = None
    if l[i0] * (1 - MARKET_TOL) <= E <= h[i0] * (1 + MARKET_TOL):       # the entry traded in the signal candle → market (no look-ahead)
        fill_i = i0
    else:
        for j in range(i0 + 1, min(len(t), i0 + 1 + FILL_WIN * M5 // iv)):
            if l[j] <= max(lo, hi) and h[j] >= min(lo, hi):
                fill_i = j
                break
    if fill_i is None:
        return {"fill_t": None}
    sg = 1 if side == "LONG" else -1
    sld, step = 0.30 / sig["lev"], 0.15 / sig["lev"]
    stop0 = E * (1 - sg * sld)
    tps = [E * (1 + sg * k * step) for k in range(1, 6)]
    best, stop_t, tp_t = 0, None, [None] * 5
    lad = None
    lad_parts, lad_stop, lad_done, close_lad = 0.0, stop0, 0, None
    end = min(len(t) - 1, fill_i + HOLD * M5 // iv)
    for j in range(fill_i + 1, end + 1):
        def hit(px):                                     # the price went against us down/up to px
            return (l[j] <= px) if sg == 1 else (h[j] >= px)

        def reach(px):
            return (h[j] >= px) if sg == 1 else (l[j] <= px)
        if stop_t is None and best < 5:
            if hit(stop0):
                stop_t = t[j]
            else:
                while best < 5 and reach(tps[best]):
                    tp_t[best] = t[j]
                    best += 1
        if lad is None:
            if hit(lad_stop):
                lad = lad_parts + (5 - lad_done) / 5 * ((lad_stop / E - 1) * sg * 100)
                close_lad = t[j]
            else:
                while lad_done < 5 and reach(tps[lad_done]):
                    lad_done += 1
                    lad_parts += k_pct(lad_done, step) / 5
                    lad_stop = E                                          # stop to the entry after TP1
                if lad_done == 5:
                    lad, close_lad = lad_parts, t[j]
        if (stop_t is not None or best == 5) and lad is not None:
            break
    mtm = (c[end] / E - 1) * sg * 100
    if lad is None:
        lad, close_lad = lad_parts + (5 - lad_done) / 5 * mtm, t[end]
    x = {}
    for k in range(1, 6):
        if best >= k:
            x[k] = (k * step * 100 - COST, int(tp_t[k - 1]))
        elif stop_t is not None:
            x[k] = (-sld * 100 - COST, int(stop_t))
        else:
            x[k] = (mtm - COST, int(t[end]))
    return {"fill_t": int(t[fill_i]) if fill_i != i0 else sig["t"], "x": x, "last": x[5][0], "last_t": x[5][1],
            "ladder_be": lad - COST, "lad_t": int(close_lad)}


def k_pct(k: int, step: float) -> float:
    return k * step * 100


def account(trades: list) -> dict:
    """trades: (open_t, close_t, net %, lev, weight). 5 % margin of equity at open, leverage ≤ 20, loss ≤ the margin."""
    ev = [(o, 0, i) for i, (o, *_r) in enumerate(trades)] + [(cl, 1, i) for i, (_o, cl, *_r) in enumerate(trades)]
    E, peak, dd, marg, curve = 10_000.0, 10_000.0, 0.0, {}, []
    for tt, kind, i in sorted(ev):
        o, cl, net, lev, w = trades[i]
        if kind == 0:
            marg[i] = E * 0.05 * w
            continue
        E += marg[i] * max(net * min(lev, 20), -100) / 100
        peak = max(peak, E)
        dd = max(dd, (peak - E) / peak)
        curve.append((tt, E))
    months = {}
    prev = 10_000.0
    for mo in sorted({dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).strftime("%Y-%m") for tt, _ in curve}):
        end = [e for tt, e in curve if dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).strftime("%Y-%m") <= mo][-1]
        months[mo] = round((end / prev - 1) * 100, 1)
        prev = end
    return {"total_pct": round((E / 10_000 - 1) * 100, 1), "max_dd_pct": round(-dd * 100, 1), "months": months,
            "months_plus": sum(1 for v in months.values() if v > 0), "n_months": len(months)}


def main():
    D = json.load(open(os.path.join(os.path.dirname(__file__), "lab", "data", "golden_signals.json"), encoding="utf-8"))
    S = D["signals"]
    syms = sorted({s["sym"] for s in S})
    t_lo = dt.datetime.fromtimestamp(min(s["t"] for s in S) / 1000, dt.timezone.utc).date() - dt.timedelta(days=2)
    t_hi = TODAY
    with ThreadPoolExecutor(10) as ex:
        KS = dict(zip(syms, ex.map(lambda s: klines(ALIAS.get(s, s), "5m", t_lo, t_hi), syms)))
    btc4 = klines("BTCUSDT", "4h", t_lo - dt.timedelta(days=3), t_hi)
    missing = [s for s, k in KS.items() if k is None]
    res, skipped = [], 0
    for s in S:
        K = KS.get(s["sym"])
        if K is None:
            skipped += 1
            continue
        a = simulate(K, s, s["side"])
        b = simulate(K, s, "SHORT" if s["side"] == "LONG" else "LONG")
        if not a or not b or a.get("fill_t") is None:
            skipped += 1
            continue
        i = int(np.searchsorted(btc4["t"], s["t"], side="right")) - 2          # the last CLOSED 4 h candle
        bdir = (btc4["c"][i] - btc4["o"][i]) if i >= 0 else 0
        res.append({**s, "as": a, "rev": b, "with_btc": (bdir > 0) == (s["side"] == "LONG") if bdir else None})
    note("golden history · data", json.dumps({"signals": len(S), "simulated": len(res), "skipped": skipped, "coins": len(syms),
                                              "missing_on_binance": missing}, ensure_ascii=False))
    def leg(r, who, k):
        x = r[who]["x"][k]
        return (r[who]["fill_t"], x[1], x[0], r["lev"], 1.0)

    V = {"как приходят · лесенка": [(r["as"]["fill_t"], r["as"]["lad_t"], r["as"]["ladder_be"], r["lev"], 1.0) for r in res]}
    for k in (1, 2, 3, 5):
        V[f"как приходят · до TP{k}"] = [leg(r, "as", k) for r in res]
        V[f"наоборот · до TP{k}"] = [leg(r, "rev", k) for r in res]
    V["наоборот · лесенка"] = [(r["rev"]["fill_t"], r["rev"]["lad_t"], r["rev"]["ladder_be"], r["lev"], 1.0) for r in res]
    V["умный разворот"] = [leg(r, "as" if r["with_btc"] else "rev", 5) for r in res if r["with_btc"] is not None]
    V["вилка"] = [(*leg(r, w, 5)[:4], 0.5) for r in res for w in ("as", "rev")]
    per_trade = {}
    for k, T in V.items():
        by = {}
        for o, cl, net, lev, w in T:
            by.setdefault(dt.datetime.fromtimestamp(o / 1000, dt.timezone.utc).strftime("%Y-%m"), []).append(net)
        per_trade[k] = {"avg": round(float(np.mean([x[2] for x in T])), 3), "n": len(T),
                        "m": [round(float(np.mean(v)), 2) for _, v in sorted(by.items())]}
    note("golden history · per trade pct no leverage after costs", json.dumps(per_trade, ensure_ascii=False))
    acc = {k: account(T) for k, T in V.items()}
    note("golden history · demo account 10k at 5 pct margin", json.dumps(
        {k: {"total": v["total_pct"], "dd": v["max_dd_pct"], "plus": f"{v['months_plus']}/{v['n_months']}", "m": list(v["months"].values())}
         for k, v in acc.items()}, ensure_ascii=False))
    # is there a part of the signals that works — found on Dec–May, checked on Jun–Oct (the walk-forward test)
    split = int(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    seg = {
        "long": lambda r: r["side"] == "LONG", "short": lambda r: r["side"] == "SHORT",
        "lev<=10": lambda r: r["lev"] <= 10, "lev11-20": lambda r: 10 < r["lev"] <= 20, "lev>20": lambda r: r["lev"] > 20,
        "with_btc4h": lambda r: r["with_btc"] is True, "against_btc4h": lambda r: r["with_btc"] is False,
        "utc0-8": lambda r: dt.datetime.fromtimestamp(r["t"] / 1000, dt.timezone.utc).hour < 8,
        "utc8-16": lambda r: 8 <= dt.datetime.fromtimestamp(r["t"] / 1000, dt.timezone.utc).hour < 16,
        "utc16-24": lambda r: dt.datetime.fromtimestamp(r["t"] / 1000, dt.timezone.utc).hour >= 16,
        "btc_eth_sol": lambda r: r["sym"] in ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
    }
    out = {}
    for name, f in seg.items():
        row = {}
        for who, k in (("as", 1), ("as", 5), ("rev", 1), ("rev", 5)):
            tr = [r[who]["x"][k][0] for r in res if f(r) and r["t"] < split]
            te = [r[who]["x"][k][0] for r in res if f(r) and r["t"] >= split]
            row[f"{who}{k}"] = [round(float(np.mean(tr)), 2) if tr else None, len(tr), round(float(np.mean(te)), 2) if te else None, len(te)]
        out[name] = row
    note("golden history · segments train Dec-May vs test Jun-Oct", json.dumps(out, ensure_ascii=False))
    # hit rates: how often each target is reached before the stop (as they come)
    hr = {f"TP{k}": round(float(np.mean([r["as"]["x"][k][0] > 0 for r in res])) * 100, 1) for k in range(1, 6)}
    hr.update({f"rev TP{k}": round(float(np.mean([r["rev"]["x"][k][0] > 0 for r in res])) * 100, 1) for k in range(1, 6)})
    note("golden history · how often targets are reached before the stop pct", json.dumps(hr))
    # the 5-minute simulation against our 1-minute one on the signals both have
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "data", "agents.sqlite"))
        db = []
        for sym, side, t, sim in conn.execute("SELECT symbol, side, t_signal, sim FROM app_signals"):
            p = (json.loads(sim or "{}").get("policies") or {}).get("last") or {}
            if p.get("net") is not None:
                db.append((sym.replace("-", ""), side, t, p["net"]))
        pairs = []
        for r in res:
            m = [x for x in db if x[0] == r["sym"] and x[1] == r["side"] and abs(x[2] - r["t"]) < 15 * 60_000]
            if m:
                pairs.append((r["as"]["last"], m[0][3]))
        if pairs:
            a_, b_ = np.array(pairs).T
            note("golden history · check vs 1-minute sim", json.dumps({"n": len(pairs), "avg_5m": round(float(a_.mean()), 3),
                                                                      "avg_1m": round(float(b_.mean()), 3),
                                                                      "same_sign_pct": round(float((np.sign(a_) == np.sign(b_)).mean()) * 100)}))
    except Exception as e:  # noqa: BLE001
        note("golden history · check vs 1-minute sim", f"skipped: {e}")


def check():
    """Why the 5-minute replay and our 1-minute one disagree on the overlapping signals: the same signals on Binance 1-minute
    candles, side by side with Binance 5-minute and our 1-minute (BingX) results, plus the trades that disagree most."""
    D = json.load(open(os.path.join(os.path.dirname(__file__), "lab", "data", "golden_signals.json"), encoding="utf-8"))
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "data", "agents.sqlite"))
    db = {}
    for sym, side, t, sim in conn.execute("SELECT symbol, side, t_signal, sim FROM app_signals"):
        j = json.loads(sim or "{}")
        p = (j.get("policies") or {}).get("last") or {}
        if p.get("net") is not None:
            db[(sym.replace("-", ""), side, t)] = (p["net"], j.get("ended"), j.get("fill"), j.get("fill_t"), j.get("end_t"), j.get("sl_t"),
                                                   ((j.get("whatif") or {}).get("rev") or {}).get("last"))
    S = []
    for s in D["signals"]:
        m = [k for k in db if k[0] == s["sym"] and k[1] == s["side"] and abs(k[2] - s["t"]) < 15 * 60_000]
        if m:
            S.append((s, db[m[0]]))
    syms = sorted({s["sym"] for s, _ in S})
    d0 = dt.datetime.fromtimestamp(min(s["t"] for s, _ in S) / 1000, dt.timezone.utc).date() - dt.timedelta(days=1)
    with ThreadPoolExecutor(10) as ex:
        K1 = dict(zip(syms, ex.map(lambda x: klines(x, "1m", d0, TODAY), syms)))
        K5 = dict(zip(syms, ex.map(lambda x: klines(x, "5m", d0, TODAY), syms)))
    rows = []
    for s, (net, ended, fill, fill_t, end_t, sl_t, rev) in S:
        if K1.get(s["sym"]) is None or K5.get(s["sym"]) is None:
            continue
        a1 = simulate(K1[s["sym"]], s, s["side"], 60_000)
        a5 = simulate(K5[s["sym"]], s, s["side"])
        r1 = simulate(K1[s["sym"]], s, "SHORT" if s["side"] == "LONG" else "LONG", 60_000)
        if not a1 or not a5 or a1.get("fill_t") is None or a5.get("fill_t") is None:
            continue
        rows.append({"sym": s["sym"], "side": s["side"], "t": s["t"], "lev": s["lev"], "db": net, "b1": round(a1["last"], 3),
                     "b5": round(a5["last"], 3), "db_rev": rev, "b1_rev": round(r1["last"], 3) if r1 and r1.get("last") is not None else None,
                     "db_end": ended, "db_fill": fill, "E": (s["lo"] + s["hi"]) / 2, "db_close_h": round((end_t - s["t"]) / 3.6e6, 1) if end_t else None,
                     "b1_close_h": round((a1["last_t"] - s["t"]) / 3.6e6, 1), "b5_close_h": round((a5["last_t"] - s["t"]) / 3.6e6, 1)})
    A = lambda k: round(float(np.mean([r[k] for r in rows if r[k] is not None])), 3)            # noqa: E731
    sg = lambda a, b: round(float(np.mean([np.sign(r[a]) == np.sign(r[b]) for r in rows if r[a] is not None and r[b] is not None])) * 100)  # noqa: E731
    note("golden check · averages", json.dumps({"n": len(rows), "db_1m_bingx": A("db"), "binance_1m": A("b1"), "binance_5m": A("b5"),
                                                "db_rev": A("db_rev"), "binance_1m_rev": A("b1_rev"), "same_sign_db_b1": sg("db", "b1"),
                                                "same_sign_b1_b5": sg("b1", "b5")}))
    bad = sorted(rows, key=lambda r: -abs(r["db"] - r["b1"]))[:25]
    note("golden check · biggest gaps db vs binance 1m", json.dumps(bad, ensure_ascii=False))


if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
