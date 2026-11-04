"""04.11.2026 — the «откуп падения» rules E (panic climax), D (perp discount), F (OI flush) with a BTC filter, an hourly
cluster cap and other exit shapes. Research only: live rules (cnq/agents/paper_desk.py) are NOT changed.

Why: all 14 real futures trades 22.10–01.11 were E/D/F longs, 10 of them on 29–30.10 while BTC fell −4.1 % in a day;
three stops in one minute = 65 % of the loss; stop −12 % against a 24 h time exit (docs/research/backlog.md, note 04.11).

Data: fresh 18-coin set (the autopilot coins) rebuilt 04.11 from data.binance.vision — 15m klines, index klines, 5-min OI
(scripts/fetch_alt100.py --mode core18 → release lab-data-core18/alt100.npz), 2022-11-20 → 2026-11-04. Path: env CNQ_CORE18.
Signals and exits: exactly scripts/autopilot_v2.py (cand_E / cand_D / cand_F = the live constants: E −1.5 % candle on 2× volume
with breadth ≥ 0.4, one basket per 10 h, stop −12 %; F OI −12 % and price −5 % over 4 h, stop −6 %; D basis ≤ −20 bp on a 1 h
average, stop −12 %; all exit after 24 h; costs 0.14 % per round trip). Account: «Агрессивная» shares (E basket 30 %, F 30 %
per trade ≤ 3 open, D 20 % per trade ≤ 3 open), gross ≤ 3× equity, one position per coin, $10 000 from 2023-01-01.

Variants (fixed in advance, nothing tuned after seeing the result):
  base       as the rules run now
  btc2/3/4   skip a new long when BTC's 24 h change (close of the signal bar) ≤ −2 / −3 / −4 %
  ema        skip when BTC's last closed 4h candle is below its 4h EMA50 AND the EMA50 is lower than one 4h candle earlier
  cap1/cap2  at most 1 / 2 new E/D/F longs per rolling hour (4 bars), across the three rules; extra E-basket coins dropped
             (the remaining ones keep their basket-sized share — the cap lowers exposure, it does not concentrate it)
  stop8      stop −8 % instead of −12 % for E and D (F keeps its −6 %)
  tp6/tp8    take-profit +6 % / +8 % next to the 24 h exit (stops unchanged)
Pass rule (set before the run): on E+D+F together, PF per trade better than base in ≥ 3 of the 4 years 2023/24/25/26 and
at least a third of base's trades kept.
Usage: CNQ_CORE18=alt100.npz python scripts/btc_filter_study.py [--md out.md]"""
import collections
import datetime as dt
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("CNQ_CORE18", "alt100.npz")


def _load():
    z = np.load(PATH, allow_pickle=True)
    keys = ("open", "high", "low", "close", "volume_quote", "open_interest_usd", "is_imputed_metrics", "basis_index_bps")
    syms = [str(s) for s in z["syms"]]
    n = int(np.where(np.isfinite(z["close"][syms.index("BTC")]))[0][-1]) + 1     # the archive lags ~1 day: cut the empty tail
    A = {k: z[k][:, :n].astype(np.float64) for k in keys}
    return syms, z["times"][:n].astype(np.int64), A


sys.modules["ap_data"] = types.SimpleNamespace(load=_load)      # autopilot_v2 imports ap_data.load → serve the fresh set
sys.path.insert(0, HERE)
import autopilot_v2 as AP  # noqa: E402

O, H, L, C, T, times, DAY, START = AP.O, AP.H, AP.L, AP.C, AP.T, AP.times, AP.DAY, AP.START
BTC = AP.syms.index("BTC")
YEARS = ("2023", "2024", "2025", "2026")
W0 = int(dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc).timestamp() * 1000)

# --- BTC context at the close of the signal bar (= entry bar − 1) --------------------------------------------------
cb = C[BTC]
b24 = np.full(T, np.nan); b24[96:] = cb[96:] / cb[:-96] - 1
# 4h candles aligned to 00/04/08… UTC: a 4h close is known at the close of its last 15m bar
is4 = ((times + 900_000) % (4 * 3_600_000)) == 0
idx4 = np.where(is4 & np.isfinite(cb))[0]
ema = np.full(len(idx4), np.nan); a = 2 / 51
for j, i in enumerate(idx4):
    ema[j] = cb[i] if j == 0 else a * cb[i] + (1 - a) * ema[j - 1]
ema[:50] = np.nan
down4 = np.zeros(T, bool)                       # state known at bar i: last closed 4h candle below a falling EMA50
cur = False; jj = 0
for i in range(T):
    while jj < len(idx4) and idx4[jj] <= i:
        cur = bool(jj > 0 and np.isfinite(ema[jj]) and np.isfinite(ema[jj - 1]) and cb[idx4[jj]] < ema[jj] and ema[jj] < ema[jj - 1]); jj += 1
    down4[i] = cur


def rewalk(cs, stop_ed=None, tgt=None):
    """Same entries, other exit shape: stop for E/D (F keeps −6 %), optional take-profit; 24 h time exit as before."""
    out = []
    for c in cs:
        st = 0.06 if c["rule"] == "F" else (stop_ed or 0.12); e = O[c["k"], c["i"]]
        mb = 95 if c["rule"] == "D" else 96
        x, g = AP.walk(c["k"], c["i"], 1, e * (1 - st), e * (1 + tgt) if tgt else None, mb)
        out.append(dict(c, x=x, ret=g - AP.COST, sf=st))
    return out


def simulate(cands, rules, cap=None, e_share=0.30, f_share=0.30, f_open=3, d_share=0.20, d_open=3, lev=3.0, e_gap=40, eq0=10_000.0):
    ev = sorted([c for c in cands if c["rule"] in rules], key=lambda c: (c["i"], c["k"]))
    byi = collections.defaultdict(list)
    for c in ev: byi[c["i"]].append(c)
    eq = eq0; open_, trades, curve, entries = [], [], [(START, eq)], []; last_e = -10 ** 9
    for i in sorted(byi):
        still = []
        for o in sorted(open_, key=lambda o: o["x"]):
            if o["x"] < i: eq += o["pnl"]; trades.append(o); curve.append((o["x"], eq))
            else: still.append(o)
        open_ = still; held = {o["k"] for o in open_}; gross = sum(o["usd"] for o in open_)
        room = lambda: 10 ** 9 if cap is None else cap - sum(1 for t in entries if t > i - 4)
        es = [c for c in byi[i] if c["rule"] == "E" and c["k"] not in held]
        if es and i - last_e >= e_gap:
            n = eq * e_share / len(es)
            for c in es:
                if gross + n > eq * lev or room() <= 0: break
                open_.append(dict(c, pnl=n * c["ret"], usd=n)); held.add(c["k"]); gross += n; entries.append(i)
            last_e = i
        for c in byi[i]:
            if c["rule"] == "E" or c["k"] in held or room() <= 0: continue
            if c["rule"] == "F":
                if sum(1 for o in open_ if o["rule"] == "F") >= f_open: continue
                n = eq * f_share
            else:
                if sum(1 for o in open_ if o["rule"] == "D") >= d_open: continue
                n = eq * d_share
            if gross + n > eq * lev: continue
            open_.append(dict(c, pnl=n * c["ret"], usd=n)); held.add(c["k"]); gross += n; entries.append(i)
    for o in sorted(open_, key=lambda o: o["x"]): eq += o["pnl"]; trades.append(o); curve.append((o["x"], eq))
    return eq, trades, curve


def pf(r):
    r = np.asarray(r); g, l_ = r[r > 0].sum(), -r[r < 0].sum()
    return float("inf") if l_ == 0 and g > 0 else (g / l_ if l_ else float("nan"))


def stats(eq, trades, curve, eq0=10_000.0):
    curve = sorted(curve); v = np.array([x for _, x in curve]); pk = np.maximum.accumulate(v)
    r = np.array([t["ret"] for t in trades]) * 100 if trades else np.zeros(0)
    yr = {}
    e_y = eq0; byy = collections.defaultdict(list)
    for t in sorted(trades, key=lambda t: t["x"]): byy[DAY[t["x"]][:4]].append(t)
    for y in YEARS:
        ts = byy.get(y, []); pnl = sum(t["pnl"] for t in ts); rr = [t["ret"] for t in ts]
        yr[y] = dict(n=len(ts), pf=pf(rr), mean=float(np.mean(rr) * 100) if rr else float("nan"), ret=pnl / e_y * 100 if e_y else 0.0)
        e_y += pnl
    w = [t for t in trades if times[t["i"]] >= W0]; wr_ = [t["ret"] for t in w]
    eqw = eq0 + sum(t["pnl"] for t in trades if times[t["x"]] < W0)          # equity when the window starts (compounded since 2023)
    win = dict(n=len(w), wr=float(np.mean(np.array(wr_) > 0) * 100) if w else float("nan"), pf=pf(wr_),
               mean=float(np.mean(wr_) * 100) if w else float("nan"), usd=sum(t["pnl"] for t in w), pct=sum(t["pnl"] for t in w) / eqw * 100,
               stops=sum(1 for t in w if t["ret"] <= -(t["sf"] - 0.0001)))
    return dict(n=len(r), wr=float((r > 0).mean() * 100) if len(r) else float("nan"), pf=pf(r), mean=float(r.mean()) if len(r) else float("nan"),
                dd=float(((v - pk) / pk).min() * 100), final=eq, years=yr, win=win)


def main():
    E, D, F = AP.cand_E(), AP.cand_D(), AP.cand_F()
    base = E + D + F
    flt = {
        "base": lambda c: True,
        "btc2": lambda c: not (b24[c["i"] - 1] <= -0.02),
        "btc3": lambda c: not (b24[c["i"] - 1] <= -0.03),
        "btc4": lambda c: not (b24[c["i"] - 1] <= -0.04),
        "ema": lambda c: not down4[c["i"] - 1],
    }
    sets = {k: [c for c in base if f(c)] for k, f in flt.items()}
    sets["cap1"] = sets["cap2"] = base
    sets["stop8"] = rewalk(base, stop_ed=0.08)
    sets["tp6"] = rewalk(base, tgt=0.06)
    sets["tp8"] = rewalk(base, tgt=0.08)
    caps = {"cap1": 1, "cap2": 2}
    names = ["base", "btc2", "btc3", "btc4", "ema", "cap1", "cap2", "stop8", "tp6", "tp8"]
    ru = {"E": "E откуп паники", "D": "D откуп дисконта", "F": "F откуп сброса", "EDF": "E+D+F вместе"}
    res = {}
    lines = [f"data {DAY[START]} → {DAY[-1]}, coins {len(AP.syms)}; raw candidates E {len(E)}, D {len(D)}, F {len(F)}"]
    for rk, rules in (("EDF", ("E", "D", "F")), ("E", ("E",)), ("D", ("D",)), ("F", ("F",))):
        lines.append(f"\n## {ru[rk]}\n")
        lines.append("| вариант | сделок | в плюсе | PF | средняя | макс. просадка | 2023 | 2024 | 2025 | 2026 | PF 23/24/25/26 | лучше base по PF | 22.10–04.11 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for nm in names:
            s = stats(*simulate(sets[nm], rules, cap=caps.get(nm)))
            res[(rk, nm)] = s
            b = res[(rk, "base")]
            better = sum(1 for y in YEARS if s["years"][y]["pf"] > b["years"][y]["pf"]) if nm != "base" else "—"
            w = s["win"]
            wtxt = f"{w['n']} сд., {w['wr']:.0f}% в плюсе, ср. {w['mean']:+.2f}%, счёт {w['pct']:+.1f}%, стопов {w['stops']}" if w["n"] else "0 сделок"
            lines.append(f"| {nm} | {s['n']} | {s['wr']:.0f}% | {s['pf']:.2f} | {s['mean']:+.2f}% | {s['dd']:.0f}% | "
                         + " | ".join(f"{s['years'][y]['ret']:+.0f}%" for y in YEARS)
                         + " | " + "/".join(f"{s['years'][y]['pf']:.2f}" for y in YEARS) + f" | {better} | {wtxt} |")
    # placebo for the variant that passes: the same number of base trades drawn at random (per-trade PF, no re-simulation)
    rng = np.random.default_rng(4)
    bt = np.array([t["ret"] for t in simulate(sets["base"], ("E", "D", "F"))[1]])
    for nm in ("ema", "btc2", "cap1"):
        n = res[("EDF", nm)]["n"]; pl = [pf(rng.choice(bt, n, replace=False)) for _ in range(2000)]
        lines.append(f"\nплацебо {nm}: случайные {n} из {len(bt)} сделок base — PF медиана {np.median(pl):.2f}, 95% {np.percentile(pl, 95):.2f}, "
                     f"99% {np.percentile(pl, 99):.2f}; у {nm} {res[('EDF', nm)]['pf']:.2f}")
    txt = "\n".join(lines)
    print(txt)
    if "--md" in sys.argv: open(sys.argv[sys.argv.index("--md") + 1], "w").write(txt + "\n")
    return res


if __name__ == "__main__":
    main()
