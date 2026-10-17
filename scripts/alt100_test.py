"""Top-100 altcoins: do the autopilot rules (B, E, F, D) and more aggressive variants give more / better trades than on the
18 majors? — docs/research/alt100.md (17.10.2026)

Data: Binance USDT-M perpetual 15m klines + 15m index-price klines + 5-min open interest from the public archive
(scripts/fetch_alt100.py, workflow research-alt100.yml), 18 core coins + the altcoins of the CoinGecko top-100 that have an
OKX USDT swap and Binance perp history since ≤ 2024-01. The rules and the portfolio are autopilot_v2's own functions: its
loader is pointed at this file before import (same trick as universe_test.py).

Honest out-of-sample protocol
  * in-sample (IS) = 2023-01-01 … 2024-12-31: every per-coin filter and every parameter choice is made here only;
  * out-of-sample (OOS) = 2025-01-01 … end of data: reported separately, nothing is tuned on it;
  * signals in the first 30 days of a coin's history are dropped (rule B's 30-day volatility, listing-week moves);
  * random baseline: for a market rule, the exact average long return of ALL entries of the same coin in the same year with
    the same holding time (minus the same costs); for a limit rule, the same orders with the entry depth taken from a random
    other hour of the same coin (docs/research/range.md);
  * t-statistic over days (all trades of one day averaged first, so one crash over 40 coins counts once).
Rule E: «market falls» (breadth ≥ 0.4) is measured on the 18 core coins only, so adding coins never changes the trigger.
Costs: market rules 0.14 % round trip (taker 0.05 % + slippage 0.02 % per side). Limit rules: maker 0.02 % per side for a
limit entry / limit target; a stop or time exit is a market order (taker 0.055 % + slippage 0.02 %); a limit buy at P fills
only if a later 15m bar trades at ≤ P·(1 − 0.01 %) during the hour after it was placed; the fill bar can hit the stop
(counted), never the target; one bar touching both → stop.

Usage: python scripts/alt100_test.py alt100.npz [--md out.md] [--cut YYYY-MM-DD] [sections …]
       sections: data validate market limit coins portfolio lab (default: all)
       python scripts/alt100_test.py --annotate results.md PART      (publishes results as workflow annotations)"""
import sys, os, collections, datetime as dt, time, numpy as np


def annotate(path, part):
    """Split the markdown into ≤ 3 900-char chunks on line boundaries; part p emits chunks 20p … 20p+19
    (10 as notices, 10 as warnings: GitHub keeps ≤ 10 annotations per level per step, ≤ 4 096 chars each)."""
    lines = open(path).read().split("\n"); chunks, cur = [], ""
    for ln in lines:
        while len(ln) > 3800: chunks.append(cur) if cur else None; cur = ""; chunks.append(ln[:3800]); ln = ln[3800:]
        if len(cur) + len(ln) + 1 > 3800: chunks.append(cur); cur = ""
        cur += ln + "\n"
    if cur: chunks.append(cur)
    tot = len(chunks)
    for j in range(20 * part, min(tot, 20 * part + 20)):
        lvl = "notice" if j - 20 * part < 10 else "warning"
        m = chunks[j].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{lvl} title=alt100 {j + 1}/{tot}::{m}", flush=True)


if len(sys.argv) > 1 and sys.argv[1] == "--annotate":
    annotate(sys.argv[2], int(sys.argv[3])); sys.exit()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ap_data  # noqa: E402
import pandas as pd  # noqa: E402

NPZ = sys.argv[1]
ARGS = sys.argv[2:]
MD = ARGS[ARGS.index("--md") + 1] if "--md" in ARGS else None
CUT = ARGS[ARGS.index("--cut") + 1] if "--cut" in ARGS else None
SECT = [a for a in ARGS if not a.startswith("--") and a not in (MD, CUT)] or ["data", "validate", "market", "limit", "coins", "portfolio", "lab"]
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
KEYS = ["open", "high", "low", "close", "volume_quote", "open_interest_usd", "is_imputed_metrics", "basis_index_bps"]
GAP = 3 * 96                              # a hole > 3 days inside a coin's history = relisting / ticker reuse → keep only after it
LATE = int(dt.datetime(2024, 2, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
# a ticker that names a different coin in the ranking than in the exchange history → not tested
REUSED = {"LIT": "CoinGecko's LIT (#89) is Lighter (launched 12.2025); Binance LITUSDT history since 2022 is Litentry, another coin"}
CLEAN = {}


def _load():
    z = np.load(NPZ, allow_pickle=True)
    syms = [str(s).replace("USDT", "") for s in z["syms"]]
    times = z["times"].astype(np.int64)
    n = len(times) if CUT is None else int(np.searchsorted(times, int(dt.datetime.strptime(CUT, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 86400_000))
    times = times[:n]
    A = {c: z[c][:, :n].astype(np.float64) for c in KEYS if c in z.files}
    oi = A["open_interest_usd"]
    if "imp_keep" not in z.files:                                   # archive data: a bar's metrics are real iff OI is present
        A["is_imputed_metrics"] = np.where(np.isfinite(oi) & (oi > 0), 0.0, 1.0)
    b = A["basis_index_bps"]; b[~(np.abs(b) < 1500)] = np.nan
    for k in range(len(syms)):                                      # relisting / reused ticker: keep the part after the last long hole
        f = np.isfinite(A["close"][k]); idx = np.flatnonzero(f)
        if not len(idx): continue
        d = np.diff(idx); g = np.flatnonzero(d > GAP)
        if len(g):
            cut = idx[g[-1] + 1]
            for c in A: A[c][k, :cut] = np.nan if c != "is_imputed_metrics" else 1.0
            CLEAN[syms[k]] = f"gap of {d[g[-1]] / 96:.0f} days before {dt.datetime.utcfromtimestamp(times[cut] / 1000):%Y-%m-%d} — older data dropped"
    RANK.update({str(s).replace("USDT", ""): int(r) for s, r in zip(z["syms"], z["rank"])} if "rank" in z.files else {})
    NOTES.extend(map(str, z["notes"]) if "notes" in z.files else [])
    return syms, times, A


RANK, NOTES = {}, []
ap_data.load = _load
import autopilot_v2 as ap  # noqa: E402  (module globals now hold these arrays)

np.seterr(all="ignore")
syms, times, NS, T, START, DAY = ap.syms, ap.times, ap.NS, ap.T, ap.START, ap.DAY
O, H, L, C, V = ap.O, ap.H, ap.L, ap.C, ap.V
COST = ap.COST
YEAR = np.array([int(d[:4]) for d in DAY])
YEARS = [y for y in (2023, 2024, 2025, 2026) if (YEAR == y).any()]
OOS = int(np.searchsorted(times, 1735689600000))          # 2025-01-01
first = np.array([np.argmax(np.isfinite(C[k])) if np.isfinite(C[k]).any() else T for k in range(NS)])
core = [k for k, s in enumerate(syms) if s in CORE18]
alts_all = [k for k in range(NS) if syms[k] not in CORE18]
def _flat(k):                                                 # stale archive series (e.g. a delisted perp kept at one price)
    seg = slice(max(START, first[k]), T); h, l = H[k, seg], L[k, seg]; ok = np.isfinite(h)
    return ok.sum() > 0 and (h[ok] == l[ok]).mean() > 0.3


FLAT = {syms[k] for k in range(NS) if first[k] < T and _flat(k)}
elig = [k for k in alts_all if first[k] < T and times[first[k]] < LATE and syms[k] not in REUSED and syms[k] not in FLAT]
late = [k for k in alts_all if k not in elig and syms[k] not in REUSED and syms[k] not in FLAT]
alts = [k for k in elig if RANK.get(syms[k], 0) <= 125]       # the hypothesis: altcoins of the top 100 (≈ market-cap rank ≤ 125 incl. stables)
alts2 = [k for k in elig if 125 < RANK.get(syms[k], 0) < 9999]  # extra independent check: ranks 101–200
alts3 = [k for k in elig if RANK.get(syms[k], 0) >= 9999]     # survivorship check: listed by 2023-01 but not in today's top-250 (fallen / delisted)
# point-in-time universe (no hindsight): only in the survivorship data set (every perp listed by 2023-01, delisted ones kept) —
# alts ranked by their 2024 average daily volume, i.e. what a trader could see on 2025-01-01
_y24 = YEAR == 2024
VOL24 = np.nansum(V[:, _y24], 1) / max(1, _y24.sum() / 96)
pit = sorted([k for k in elig if VOL24[k] > 0], key=lambda k: -VOL24[k]) if alts3 else []
altsV, altsV2 = pit[:40], pit[40:80]
GROUPS = ([("alts top-100", alts)] + ([("alts 101–200", alts2)] if alts2 else []) + ([("alts outside today's top-250", alts3)] if alts3 else [])
          + ([("alts top-40 by 2024 volume (point-in-time)", altsV), ("alts 41–80 by 2024 volume", altsV2)] if altsV else []))
WARM = 30 * 96
DAYS = (times[-1] - times[START]) / 86400_000
DAYS_IS = (times[OOS] - times[START]) / 86400_000 if OOS < T else DAYS
DAYS_OOS = max(1e-9, (times[-1] - times[min(OOS, T - 1)]) / 86400_000)
NORMA = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40, d_share=0.15, d_open=3)
OUT = []


def P(s=""):
    print(s, flush=True); OUT.append(s)


def breadth(rows):
    r = ap.r24[rows]; av = np.isfinite(r).sum(0)
    return np.where(av >= 8, (r < -0.03).sum(0) / np.maximum(av, 1), 0)


ap.BR = breadth(core)                                     # E trigger: the 18 majors only


def clean(cs): return [c for c in cs if c["i"] - first[c["k"]] >= WARM and c["i"] >= START]


# --------------------------------------------------------------------------------------------- market-order rules
def gen_B(sig=3.5, wick=0.5, tag="B"):
    """autopilot_v2.cand_B with the σ multiple and wick share as parameters (sig=3.5, wick=0.5 = live rule)."""
    out = []
    for k in range(NS):
        r = np.nan_to_num(np.r_[0, C[k, 1:] / C[k, :-1] - 1])
        s = np.sqrt(np.convolve(r * r, np.ones(2880), "full")[:T] / 2880); a = ap.atr15(k); rng = H[k] - L[k]
        wk = (np.minimum(O[k], C[k]) - L[k]) / rng
        for i in np.where((r <= -sig * s) & (wk >= wick) & (np.arange(T) >= START))[0]:
            if i + 34 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; stop = L[k, i] - 0.5 * a[i]
            if not e - stop > 0: continue
            x, g = ap.walk(k, i + 1, 1, stop, e + (e - stop), 32)
            out.append(dict(rule="B", tag=tag, k=k, i=i + 1, x=x, ret=g - COST, sf=(e - stop) / e + COST, H=32))
    return clean(out)


def gen_E(br=0.4, hold=96, tag="E"):
    """autopilot_v2.cand_E (candle −1.5 % on 2× volume while ≥ 40 % of the 18 majors are down > 3 % in 24 h), hold as a parameter."""
    out = []
    for k in range(NS):
        cs = np.cumsum(np.nan_to_num(V[k])); vav = np.full(T, np.nan); vav[97:] = (cs[96:-1] - cs[:-97]) / 96
        for i in np.where((C[k] / O[k] - 1 <= -0.015) & (V[k] >= 2 * vav) & (ap.BR >= br) & (np.arange(T) >= START))[0]:
            if i + hold + 2 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; x, g = ap.walk(k, i + 1, 1, e * 0.88, None, hold)
            out.append(dict(rule="E", tag=tag, k=k, i=i + 1, x=x, ret=g - COST, sf=0.12, H=hold))
    return clean(out)


def gen_F(oi=-0.12, px=-0.05, hold=96, stop=0.06, tag="F", delay=1):
    return clean([dict(c, tag=tag, H=hold) for c in ap.cand_F(oi=oi, px=px, hold=hold, stop=stop, delay=delay)])


def gen_D(th=-20.0, hold=96, tag="D", chronic=None, delay=1):
    """Rule D (1-hour average basis ≤ th bp → long, exit after `hold` bars, stop −12 %). chronic=x: skip coins whose
    basis median over the previous 7 days is already ≤ x bp (permanently discounted perps: the discount is not a panic)."""
    cs = ap.cand_D(th=th, hold=hold, delay=delay)
    if chronic is not None:
        b = np.where(ap.IMP == 0, ap.A["basis_index_bps"], np.nan)
        med = pd.DataFrame(b[:, ::4].T).rolling(168, min_periods=84).median().to_numpy().T      # hourly samples, 7 days
        cs = [c for c in cs if not (med[c["k"], (c["i"] - 2) // 4] <= chronic)]
    return clean([dict(c, tag=tag, H=hold) for c in cs])


# random baseline: mean long return of every entry of coin k in year y held H bars (exit at the close of bar i0+H)
_RB = {}


def rb(Hb):
    if Hb not in _RB:
        r = np.full((NS, T), np.nan); r[:, :T - Hb] = C[:, Hb:] / O[:, :T - Hb] - 1
        m = np.full((NS, 2030), np.nan)
        for y in YEARS:
            sel = (YEAR == y) & (np.arange(T) >= START)
            m[:, y] = np.nanmean(r[:, sel], 1) - COST
        _RB[Hb] = m
    return _RB[Hb]


def tday(cs):
    if len(cs) < 3: return np.nan
    d = collections.defaultdict(list)
    for c in cs: d[DAY[c["i"]]].append(c["ret"])
    v = np.array([np.mean(x) for x in d.values()])
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 and v.std() > 0 else np.nan


def base_of(c):
    if "rnd" in c: return c["rnd"]
    return rb(c["H"])[c["k"], YEAR[c["i"]]]


def S(cs):
    a = np.array([c["ret"] for c in cs]) * 100
    return (len(a), a.mean() if len(a) else np.nan)


def row(label, cs, is_days=None, oos_days=None):
    """One table row: n, per day, avg, WR, per-year avg, IS avg, OOS avg, OOS minus random, t(OOS days), coins + in OOS."""
    n, a = S(cs)
    if not n: return f"| {label} | 0 | | | | " + " | " * len(YEARS) + " | | | |"
    wr = np.mean([c["ret"] > 0 for c in cs]) * 100
    ys = []
    for y in YEARS:
        m, v = S([c for c in cs if YEAR[c["i"]] == y]); ys.append(f"{v:+.2f}" if m else "—")
    ins = [c for c in cs if c["i"] < OOS]; oos = [c for c in cs if c["i"] >= OOS]
    ni, ai = S(ins); no, ao = S(oos)
    edge = (np.nanmean([c["ret"] - base_of(c) for c in oos]) * 100) if no else np.nan
    kc = collections.defaultdict(float); dsum = collections.defaultdict(float)
    for c in oos: kc[c["k"]] += c["ret"]; dsum[DAY[c["i"]]] += c["ret"]
    pos = f"{sum(v > 0 for v in kc.values())}/{len(kc)}"
    top5 = set(sorted(dsum, key=dsum.get)[-5:])
    _, a5 = S([c for c in oos if DAY[c["i"]] not in top5])
    return (f"| {label} | {n} | {n / DAYS:.2f} | {a:+.2f}% | {wr:.0f}% | " + " | ".join(ys) +
            f" | {ai:+.2f}% ({ni}) | {ao:+.2f}% ({no}) | {a5:+.2f}% | {edge:+.2f} | {tday(oos):.1f} | {pos} |")


HDR = ("| rule · coins | trades | per day | avg | WR | " + " | ".join(str(y) for y in YEARS) +
       " | IS 23–24 avg (n) | OOS 25–26 avg (n) | OOS w/o 5 best days | OOS − random, pp | t OOS (days) | coins + OOS |")
SEP = "|---|---|---|---|---|" + "---|" * len(YEARS) + "---|---|---|---|---|---|"


def on(cs, ks):
    ks = set(ks); return [c for c in cs if c["k"] in ks]


def is_pass(cs, ks, nmin):
    """Per-coin filter decided on IS only: ≥ nmin trades in 2023–2024 and average > 0."""
    out = []
    for k in ks:
        n, a = S([c for c in cs if c["k"] == k and c["i"] < OOS])
        if n >= nmin and a > 0: out.append(k)
    return out


# ----------------------------------------------------------------------------------------------- limit-order rules
MAKER, TAKER, SLIP, TICK = 0.0002, 0.00055, 0.0002, 0.0001
C_TGT, C_MKT = 2 * MAKER, MAKER + TAKER + SLIP
NH = T // 4; H0 = START // 4


def grp(x): return x[:, :NH * 4].reshape(NS, NH, 4)


H4, L4 = grp(H), grp(L)
H1 = np.fmax.reduce(H4, 2); L1 = np.fmin.reduce(L4, 2); C1 = grp(C)[:, :, 3]


def roll(x, n, fn):
    return getattr(pd.DataFrame(x.T).rolling(n, min_periods=n), fn)().to_numpy().T


def shift(x, j):
    out = np.full_like(x, np.nan); out[:, j:] = x[:, :-j] if j else x; return out


_pc = np.c_[np.full(NS, np.nan), C1[:, :-1]]
ATR1 = roll(np.fmax(H1 - L1, np.fmax(abs(H1 - _pc), abs(L1 - _pc))), 14, "mean")


def exit_trade(k, j, p, sl, tp, side, hold):
    if side > 0:
        if L[k, j] <= sl: return j, sl / p - 1 - C_MKT
    else:
        if H[k, j] >= sl: return j, 1 - sl / p - C_MKT
    end = min(T - 1, j + hold)
    lo = L[k, j + 1:end + 1]; hi = H[k, j + 1:end + 1]
    if side > 0:
        s = np.flatnonzero(lo <= sl); t = np.flatnonzero(hi >= tp * (1 + TICK)) if np.isfinite(tp) else np.zeros(0, int)
    else:
        s = np.flatnonzero(hi >= sl); t = np.flatnonzero(lo <= tp * (1 - TICK)) if np.isfinite(tp) else np.zeros(0, int)
    js = s[0] if len(s) else 10 ** 9; jt = t[0] if len(t) else 10 ** 9
    if js == jt == 10 ** 9:
        while end > j and not np.isfinite(C[k, end]): end -= 1
        return end, side * (C[k, end] / p - 1) - C_MKT
    if js <= jt:
        x = j + 1 + js; o = O[k, x]
        fill = (min(sl, o) if side > 0 else max(sl, o)) if np.isfinite(o) else sl
        return x, side * (fill / p - 1) - C_MKT
    return j + 1 + jt, side * (tp / p - 1) - C_TGT


def sim_orders(Pr, SL, TP, OK, side, hold, coins=None):
    """Orders computed at the close of hour h rest during hour h+1 (15m bars 4h+4 … 4h+7); one position per coin.
    Returns an array of trades [k, entry bar, exit bar, net return, stop distance + costs]."""
    Pn = Pr[:, :-1]
    with np.errstate(invalid="ignore"):
        fm = (L4[:, 1:, :] <= Pn[..., None] * (1 - TICK)) if side > 0 else (H4[:, 1:, :] >= Pn[..., None] * (1 + TICK))
    ok = OK[:, :-1] & np.isfinite(Pn) & fm.any(2); ok[:, :H0] = False
    fb = fm.argmax(2)
    out = []
    for k in (range(NS) if coins is None else coins):
        free = first[k] + WARM
        for h in np.flatnonzero(ok[k]):
            i = 4 * h + 3
            if i < free: continue
            if i + 5 + hold >= T: break
            j = 4 * (h + 1) + fb[k, h]; p = Pr[k, h]
            x, r = exit_trade(k, j, p, SL[k, h], TP[k, h], side, hold)
            out.append((k, j, x, r, abs(p - SL[k, h]) / p + C_MKT))
            free = x + 1
    return np.array(out, dtype=np.float64).reshape(-1, 5)


def dicts(arr, rule, tag):
    return [dict(rule=rule, tag=tag, k=int(a[0]), i=int(a[1]), x=int(a[2]), ret=a[3], sf=a[4]) for a in arr]


def randomize(Pr, SL, TP, OK, seed):
    """Same hours, same stop / target distances in ATR units, entry depth below (above) the close from a random other hour."""
    rng = np.random.default_rng(seed); a = ATR1
    P2, SL2, TP2 = Pr.copy(), SL.copy(), TP.copy()
    for k in range(NS):
        hs = np.flatnonzero(OK[k] & np.isfinite(Pr[k]) & (a[k] > 0))
        if not len(hs): continue
        dep = (C1[k, hs] - Pr[k, hs]) / a[k, hs]; sd = (Pr[k, hs] - SL[k, hs]) / a[k, hs]; td = (TP[k, hs] - Pr[k, hs]) / a[k, hs]
        pm = rng.permutation(len(hs))
        P2[k, hs] = C1[k, hs] - dep[pm] * a[k, hs]; SL2[k, hs] = P2[k, hs] - sd * a[k, hs]; TP2[k, hs] = P2[k, hs] + td * a[k, hs]
    return P2, SL2, TP2


def with_random(spec, cs, seeds=(1, 2)):
    """Attach to every trade the coin-year average of the randomized version (so `row` can report OOS − random)."""
    Pr, SL, TP, OK, side, hold = spec
    acc = collections.defaultdict(list)
    for sd in seeds:
        P2, SL2, TP2 = randomize(Pr, SL, TP, OK, sd)
        for a in sim_orders(P2, SL2, TP2, OK, side, hold, coins=sorted({c["k"] for c in cs})):
            acc[(int(a[0]), YEAR[int(a[1])])].append(a[3])
    m = {kv: np.mean(v) for kv, v in acc.items()}
    for c in cs: c["rnd"] = m.get((c["k"], YEAR[c["i"]]), np.nan)
    return cs


def spec_range(N, e, t, s, hold_h, upper=0.25):
    """Range-low limit (lab rule L is N=336 h, e=0.30, t=0.80, s=0.25, upper 0.55, hold 72 h): range = last N closed hours;
    buy limit at low + e·width while the price is between that and low + (e + upper)·width; target low + t·width;
    stop low − s·width."""
    lo = roll(L1, N, "min"); hi = roll(H1, N, "max"); w = hi - lo
    Pr = lo + e * w; SL = lo - s * w; TP = lo + t * w
    OK = (w > 0) & (Pr < C1) & (C1 <= lo + (e + upper) * w)
    return Pr, SL, TP, OK, 1, hold_h * 4


def pivots(x, w, low):
    pv = np.zeros(x.shape, bool); n = x.shape[1]; c = x[:, w:n - w]; m = np.ones(c.shape, bool)
    for d in range(1, w + 1):
        a, b = x[:, w - d:n - w - d], x[:, w + d:n - w + d]
        m &= (c < a) & (c < b) if low else (c > a) & (c > b)
    pv[:, w:n - w] = m & np.isfinite(c)
    return pv


_LV = {}


def levels(w, look):
    """Lab rules N/Q (cnq/agents/paper_desk.py): fractal lows/highs (strictly beyond w neighbours each side) among the last
    `look` closed hours; support = highest low in [0.98·p, 0.999·p), resistance = lowest high in (1.001·p, 1.02·p];
    N target = lowest fractal high above p, Q target = highest fractal low below p."""
    if (w, look) not in _LV:
        vl = np.where(pivots(L1, w, True), L1, np.nan); vh = np.where(pivots(H1, w, False), H1, np.nan); p = C1
        sup = np.full_like(p, np.nan); res = sup.copy(); up = sup.copy(); dn = sup.copy()
        for j in range(w, look):
            sl, sh = shift(vl, j), shift(vh, j)
            sup = np.fmax(sup, np.where((sl >= 0.98 * p) & (sl < 0.999 * p), sl, np.nan))
            res = np.fmin(res, np.where((sh > 1.001 * p) & (sh <= 1.02 * p), sh, np.nan))
            up = np.fmin(up, np.where(sh > p, sh, np.nan)); dn = np.fmax(dn, np.where(sl < p, sl, np.nan))
        _LV[(w, look)] = sup, res, up, dn
    return _LV[(w, look)]


def spec_N(w=2, look=48, m=0.6, hold_h=24):
    sup, _, up, _ = levels(w, look)
    SL = sup - m * ATR1; TP = np.where(np.isfinite(up), up, sup + 1.5 * (sup - SL))
    return sup, SL, TP, np.isfinite(sup) & (ATR1 > 0), 1, hold_h * 4


def spec_Q(w=2, look=48, m=0.6, hold_h=24):
    _, res, _, dn = levels(w, look)
    SL = res + m * ATR1; TP = np.where(np.isfinite(dn), dn, res - 1.5 * (SL - res))
    return res, SL, TP, np.isfinite(res) & (ATR1 > 0), -1, hold_h * 4


def spec_RC(N=48, k=2.0, m=0.5, hold_h=24):
    """Linear-regression channel on the last N hourly closes: buy limit at (fit one hour ahead) − k·σ(residuals),
    target = the fitted line, stop = entry − m·σ. A narrow, fast channel (2–4 days)."""
    y = C1; x = np.arange(N, dtype=float); sx, sxx = x.sum(), (x * x).sum()
    yy = np.nan_to_num(y); cy = np.cumsum(yy, 1); cyy = np.cumsum(yy * yy, 1); idx = np.arange(NH)[None, :]
    ciy = np.cumsum(yy * idx, 1)
    def rs(c): out = np.full_like(c, np.nan); out[:, N - 1:] = c[:, N - 1:] - np.c_[np.zeros(NS), c[:, :-N]][:, :NH - N + 1]; return out
    Sy, Syy, Siy = rs(cy), rs(cyy), rs(ciy)
    Sxy = Siy - (idx - N + 1) * Sy                                   # Σ x·y with x = 0 … N−1 inside the window
    b = (N * Sxy - sx * Sy) / (N * sxx - sx * sx); a = (Sy - b * sx) / N
    var = np.maximum(0, (Syy - a * Sy - b * Sxy) / (N - 2)); sd = np.sqrt(var)
    nn = roll(np.isfinite(y).astype(float), N, "sum"); bad = ~(nn == N)
    mid = a + b * N; Pr = mid - k * sd; SL = Pr - m * sd; TP = mid
    Pr[bad] = np.nan
    return Pr, SL, TP, np.isfinite(Pr) & (sd > 0) & (Pr < C1), 1, hold_h * 4


# --------------------------------------------------------------------------------------------------------- sections
P(f"# Alt-100 test — {NS} coins, {DAY[START]} → {DAY[-1]} (IS 2023–2024, OOS {DAY[min(OOS, T - 1)]} → {DAY[-1]})\n")
t0 = time.time()

if "data" in SECT:
    P("## Data\n")
    P(f"Core ({len(core)}): {' '.join(syms[k] for k in core)}  ")
    P(f"Alts top-100 ({len(alts)}): " + " ".join(f"{syms[k]}#{RANK.get(syms[k], 0)}" for k in alts) + "  ")
    if alts2: P(f"Alts ranks 101–200 ({len(alts2)}): " + " ".join(f"{syms[k]}#{RANK.get(syms[k], 0)}" for k in alts2) + "  ")
    if alts3: P(f"Alts outside today's top-250 ({len(alts3)}): " + " ".join(syms[k] for k in alts3) + "  ")
    if FLAT: P(f"Excluded (stale / flat archive series): {' '.join(sorted(FLAT))}  ")
    if altsV:
        P(f"Point-in-time top-40 alts by 2024 volume: {' '.join(f'{syms[k]}#{RANK.get(syms[k], 0) if RANK.get(syms[k], 0) < 9999 else chr(8212)}' for k in altsV)}  ")
        P(f"41–80: {' '.join(f'{syms[k]}#{RANK.get(syms[k], 0) if RANK.get(syms[k], 0) < 9999 else chr(8212)}' for k in altsV2)}  ")
        dl = [syms[k] for k in pit[:80] if not np.isfinite(C[k, T - 200:]).any()]
        P(f"Of these 80, no longer trading at the end of the data (delisted / renamed): {' '.join(dl) or '—'}  ")
    if late: P(f"Alts dropped (history starts after 2024-01 once cleaned): {' '.join(syms[k] for k in late)}  ")
    for s, msg in CLEAN.items(): P(f"Cleaned {s}: {msg}  ")
    for s, msg in REUSED.items():
        if s in syms: P(f"Excluded {s}: {msg}  ")
    P("\n| coin | rank | first bar | OI bars real | basis median bp | basis ≤ −20 share | 2023–26 move | largest 15m jump |")
    P("|---|---|---|---|---|---|---|---|")
    b = ap.A["basis_index_bps"]
    for k in (core + alts + alts2 if len(alts3) < 40 else core):
        seg = slice(max(START, first[k]), T)
        oi = (ap.IMP[k, seg] == 0).mean() * 100; bb = b[k, seg]
        cl = C[k, seg][np.isfinite(C[k, seg])]; mv = cl[-1] / cl[0] - 1 if len(cl) else np.nan
        jr = np.nanmax(np.abs(np.log(O[k, seg][1:] / C[k, seg][:-1]))) * 100
        P(f"| {syms[k]} | {RANK.get(syms[k], 0)} | {DAY[first[k]]} | {oi:.0f}% | {np.nanmedian(bb):+.1f} | {np.nanmean(bb <= -20) * 100:.2f}% | {mv * 100:+.0f}% | {jr:.0f}% |")
    P()

# ---- candidates (computed once) ----
MK = {}
if any(s in SECT for s in ("validate", "market", "coins", "portfolio", "lab", "altpilot", "lists", "labx")):
    MK["B"] = gen_B(); MK["B30"] = gen_B(3.0, tag="B30"); MK["B25"] = gen_B(2.5, tag="B25"); MK["B30w3"] = gen_B(3.0, 0.3, "B30w3")
    MK["E"] = gen_E(); MK["E8h"] = gen_E(hold=32, tag="E8h"); MK["E30"] = gen_E(br=0.3, tag="E30")
    MK["F"] = gen_F(); MK["F8"] = gen_F(-0.08, -0.04, tag="F8"); MK["F8h"] = gen_F(hold=32, tag="F8h")
    MK["D"] = gen_D(); MK["D15"] = gen_D(-15, tag="D15"); MK["D8h"] = gen_D(hold=32, tag="D8h"); MK["Dch"] = gen_D(chronic=-10, tag="Dch")
    MK["D15ch"] = gen_D(-15, chronic=-10, tag="D15ch"); MK["Dlate"] = gen_D(tag="Dlate", delay=2); MK["D25"] = gen_D(-25, tag="D25")
    MK["F8hlate"] = gen_F(hold=32, tag="F8hlate", delay=2)
    print(f"candidates built {time.time() - t0:.0f}s: " + ", ".join(f"{k} {len(v)}" for k, v in MK.items()), flush=True)

if "validate" in SECT:
    P("## Check: the 18 core coins from the archive vs the autopilot research set (2023-01 → 2026-09-09)\n")
    cut = int(np.searchsorted(times, 1788987600000)) if times[-1] >= 1788987600000 else T - 1
    ref = dict(B=(605, None), E=(4740, None), F=(650, None), D=(523, None))
    P("| rule | candidates here | research set | avg here |")
    P("|---|---|---|---|")
    for r in "BEFD":
        cs = [c for c in on(MK[r], core) if c["x"] <= cut]
        P(f"| {r} | {len(cs)} | {ref[r][0]} | {S(cs)[1]:+.2f}% |")
    cs = [c for r in "BEFD" for c in on(MK[r], core) if c["x"] <= cut]
    eq, tr, cu = ap.simulate([c for c in cs if c["rule"] != "D"], ("B", "E", "F"), **{k: v for k, v in NORMA.items() if k[0] != "d"})
    P(f"\n«норма» B+E+F here ${eq:,.0f} (research set $48,497); ", )
    eq, tr, cu = ap.simulate(cs, ("B", "E", "F", "D"), **NORMA)
    P(f"«норма»+D here ${eq:,.0f} (research set $70,639).\n")

if "market" in SECT:
    P("## Market-order rules: core 18 vs alts (avg = net per trade after 0.14 %; years = avg %)\n")
    P(HDR); P(SEP)
    for tag, cs in MK.items():
        ex = {"B": 5, "E": 20, "F": 5, "D": 3}[tag[0]]
        pas = is_pass(cs, alts, ex)
        P(row(f"{tag} · core", on(cs, core)))
        for gn, G in GROUPS:
            p2 = is_pass(cs, G, ex)
            P(row(f"{tag} · {gn}", on(cs, G))); P(row(f"{tag} · {gn} IS-pass ({len(p2)})", on(cs, p2)))
    P("\nIS-pass = coins with ≥ 5 trades (E: 20, D: 3) in 2023–2024 and average > 0 there; OOS columns show what happened next.\n")

if "coins" in SECT:
    P("## Per alt coin, base rules (IS 2023–24 avg (n) → OOS 2025–26 avg (n))\n")
    P("| coin | B | E | F | D |")
    P("|---|---|---|---|---|")
    for k in (alts + alts2 if len(alts3) < 40 else []):
        cells = []
        for r in "BEFD":
            ci = [c for c in MK[r] if c["k"] == k and c["i"] < OOS]; co = [c for c in MK[r] if c["k"] == k and c["i"] >= OOS]
            ni, ai = S(ci); no, ao = S(co)
            cells.append((f"{ai:+.1f} ({ni})" if ni else "— (0)") + " → " + (f"{ao:+.1f} ({no})" if no else "— (0)"))
        P(f"| {syms[k]} | " + " | ".join(cells) + " |")
    for r, nmin in (("B", 5), ("E", 20), ("F", 5), ("D", 3)):
        P(f"\nIS-pass {r}: " + "; ".join(f"{gn}: {' '.join(syms[k] for k in is_pass(MK[r], G, nmin)) or '—'}" for gn, G in GROUPS) + "  ")
    P()

LM, LA, BEST = {}, {}, {}


def fam(tag): return "RC" if tag.startswith("RC") else tag[0]


def arr_stats(arr, ks, lo=None, hi=None):
    if not len(arr): return 0, np.nan
    m = np.isin(arr[:, 0], ks)
    if lo is not None: m &= arr[:, 1] >= lo
    if hi is not None: m &= arr[:, 1] < hi
    return int(m.sum()), (arr[m, 3].mean() * 100 if m.any() else np.nan)


if any(s in SECT for s in ("limit", "lab")):
    SPECS = {"L": lambda: spec_range(336, 0.30, 0.80, 0.25, 72, upper=0.25), "N": lambda: spec_N(), "Q": lambda: spec_Q()}
    for N in (24, 72, 168):
        for e in (0.1, 0.3):
            for t in (0.5, 0.8):
                for sx in (0.1, 0.25):
                    SPECS[f"K{N}e{e}t{t}s{sx}"] = (lambda N=N, e=e, t=t, sx=sx: spec_range(N, e, t, sx, max(24, N)))
    for w in (1, 2):
        for look in (24, 48):
            for m in (0.3, 0.6, 1.0):
                if (w, look, m) != (2, 48, 0.6):
                    SPECS[f"N{w}/{look}m{m}"] = (lambda w=w, look=look, m=m: spec_N(w, look, m))
                    SPECS[f"Q{w}/{look}m{m}"] = (lambda w=w, look=look, m=m: spec_Q(w, look, m))
    for N in (48, 96):
        for kk in (1.5, 2.0):
            for m in (0.5, 1.0):
                SPECS[f"RC{N}k{kk}m{m}"] = (lambda N=N, kk=kk, m=m: spec_RC(N, kk, m))
    for tag, mk in SPECS.items():
        LA[tag] = sim_orders(*mk(), coins=core + alts + alts2)
    print(f"limit variants built {time.time() - t0:.0f}s ({len(SPECS)})", flush=True)
    for f_ in ("K", "N", "Q", "RC"):                    # IS choice per family on alts (≥ 300 IS trades), then judged OOS
        cand = [(arr_stats(LA[t], alts, hi=OOS)[1], t) for t in LA if fam(t) == f_ and arr_stats(LA[t], alts, hi=OOS)[0] >= 300]
        if cand: BEST[f_] = max(cand)[1]
    for tag in dict.fromkeys(["L", "N", "Q"] + list(BEST.values())):
        LM[tag] = with_random(SPECS[tag](), dicts(LA[tag], fam(tag), tag))

if "limit" in SECT:
    P("## Limit-order rules (maker entry; lab rules L/N/Q exactly as on the lab account + narrower variants)\n")
    P("Live lab rules — L: 14-day range, limit at 30 % from the low, target 80 %, stop 25 % below the low, 72 h; "
      "N: 1h fractal support within 2 % below, stop 0.6 ATR(1h) under it, target next fractal high, 24 h; Q: mirror short. "
      "K: same as L on a 1/3/7-day range; RC: 2–4-day regression channel, limit at the lower band, target the middle.\n")
    P(HDR); P(SEP)
    for tag in LM:
        P(row(f"{tag} · core", on(LM[tag], core)))
        P(row(f"{tag} · alts", on(LM[tag], alts)))
        pas = is_pass(LM[tag], alts, 30)
        P(row(f"{tag} · alts IS-pass ({len(pas)})", on(LM[tag], pas)))
        if alts2: P(row(f"{tag} · alts 101–200", on(LM[tag], alts2)))
    P(f"\nIS-chosen best per family (alts, IS only): {BEST}. OOS − random for limit rules = vs the same orders at a random depth.\n")
    P("All variants on alts — trades/day · IS avg · OOS avg (n) · core OOS avg:  ")
    for tag, arr in LA.items():
        n_all = arr_stats(arr, alts)[0]; ni, ai = arr_stats(arr, alts, hi=OOS); no, ao = arr_stats(arr, alts, lo=OOS); _, co = arr_stats(arr, core, lo=OOS)
        x2 = f" · 101–200 OOS {arr_stats(arr, alts2, lo=OOS)[1]:+.3f}%" if alts2 else ""
        P(f"{tag}: {n_all / DAYS:.1f}/d · {ai:+.3f}% · {ao:+.3f}% ({no}) · core {co:+.3f}%{x2};  ")
    P()


def run(cands, rules, i0=START, extra=None, **kw):
    p = dict(NORMA); p.update(kw)
    cs = [c for c in cands if c["i"] >= i0]
    eq, tr, cu = ap.simulate(cs, rules, extra=extra, **p)
    cu = [(i0, 10_000.0)] + sorted(cu[1:]); v = np.array([x for _, x in cu]); pk = np.maximum.accumulate(v)
    days = (times[-1] - times[i0]) / 86400_000
    y = collections.defaultdict(float); ru = collections.defaultdict(float)
    for t in tr: y[DAY[t["x"]][:4]] += t["pnl"]; ru[t["rule"]] += t["pnl"]
    return dict(final=eq, cagr=(eq / 1e4) ** (365.25 / days) - 1, dd=((v - pk) / pk).min(), n=len(tr), tpd=len(tr) / days,
                years={k: round(x) for k, x in sorted(y.items())}, rules={k: round(x) for k, x in sorted(ru.items())})


def pf_row(label, cands, rules, extra=None, **kw):
    a = run(cands, rules, START, extra, **kw); b = run(cands, rules, OOS, extra, **kw)
    yrs = " · ".join(f"{k[2:]}: {v:+,}" for k, v in a["years"].items())
    return (f"| {label} | ${a['final']:,.0f} | {a['cagr'] * 100:+.0f}% | {a['dd'] * 100:.1f}% | {a['tpd']:.2f} | {yrs} | "
            f"${b['final']:,.0f} | {b['cagr'] * 100:+.0f}% | {b['dd'] * 100:.1f}% | {b['tpd']:.2f} | {b['rules']} |")


PFH = ("| portfolio («норма» + D, $10k) | full: final | CAGR | max DD | trades/day | P&L by year | OOS 2025→: final | CAGR | max DD | "
       "trades/day | OOS P&L by rule |")
PFS = "|---|---|---|---|---|---|---|---|---|---|---|"


def relabel(cs, letter): return [dict(c, rule=letter) for c in cs]


EX = dict(b=dict(risk=0.01, open=4), f=dict(share=0.25, open=3), d=dict(share=0.15, open=3))
NMIN = dict(B=5, E=20, F=5, D=3)


def best_days(cs, n):
    d = collections.defaultdict(float)
    for c in cs: d[DAY[c["i"]]] += c["ret"]
    return set(sorted(d, key=d.get)[-n:])


if "portfolio" in SECT:
    P("## Portfolio: core «норма»+D plus alt-coin candidates\n")
    P("New letter (b, f, d) = alt trades with their own cap and the same size as the core rule (b: risk 1 % ×4, f: 25 % ×3, d: 15 % ×3); "
      "same letter = they share the core rule's cap. IS-pass coin lists are chosen on 2023–2024 only. "
      "OOS columns: a fresh $10k from 2025-01-01.\n")
    base = [c for r in "BEFD" for c in on(MK[r], core)]
    R4 = ("B", "E", "F", "D")
    P(PFH); P(PFS)
    P(pf_row("core 18 (now)", base, R4))
    for gn, G in GROUPS:
        pas = {r: is_pass(MK[r], G, n) for r, n in NMIN.items()}
        for r, lt in (("B", "b"), ("F", "f"), ("D", "d")):
            P(pf_row(f"+ {lt} on {gn} IS-pass ({len(pas[r])})", base + relabel(on(MK[r], pas[r]), lt), R4 + (lt,), extra={lt: EX[lt]}))
            P(pf_row(f"+ {lt} on all {gn}", base + relabel(on(MK[r], G), lt), R4 + (lt,), extra={lt: EX[lt]}))
            if gn == "alts top-100": P(pf_row(f"+ {r} on {gn} IS-pass, same letter", base + on(MK[r], pas[r]), R4))
        P(pf_row(f"+ E on {gn} (same basket)", base + on(MK["E"], G), R4))
        fd = base + relabel(on(MK["F"], pas["F"]), "f") + relabel(on(MK["D"], pas["D"]), "d")
        P(pf_row(f"+ f + d on {gn} IS-pass", fd, R4 + ("f", "d"), extra=EX))
        f8 = base + relabel(on(MK["F8h"], is_pass(MK["F8h"], G, 5)), "f") + relabel(on(MK["D"], pas["D"]), "d")
        P(pf_row(f"+ f(8 h) + d on {gn} IS-pass", f8, R4 + ("f", "d"), extra=EX))
        bfd = fd + relabel(on(MK["B"], pas["B"]), "b")
        P(pf_row(f"+ b + f + d on {gn} IS-pass", bfd, R4 + ("b", "f", "d"), extra=EX))
    if len(GROUPS) > 1:
        AG = [k for _, G in GROUPS for k in G]
        pas = {r: is_pass(MK[r], AG, n) for r, n in NMIN.items()}
        fd = base + relabel(on(MK["F"], pas["F"]), "f") + relabel(on(MK["D"], pas["D"]), "d")
        P(pf_row(f"+ f + d on ALL alt groups IS-pass (f {len(pas['F'])}, d {len(pas['D'])})", fd, R4 + ("f", "d"), extra=EX))

    for RG, RGN in ([(altsV, "point-in-time top-40")] if altsV else [(alts, "top-100")]):
        P(f"\n**Robustness of «+ d» and «+ f» on {RGN} alts (IS-pass coins)** — sizing, late entry, higher alt costs, best days removed:\n")
        P(PFH); P(PFS)
        pd_ = is_pass(MK["D"], RG, 3); pf_ = is_pass(MK["F"], RG, 5)
        dA, fA = on(MK["D"], pd_), on(MK["F"], pf_)
        for lab_, extra in (("d 10 % ×3", dict(d=dict(share=0.10, open=3))), ("d 15 % ×2", dict(d=dict(share=0.15, open=2))),
                            ("d 15 % ×5", dict(d=dict(share=0.15, open=5))), ("d 20 % ×3", dict(d=dict(share=0.20, open=3)))):
            P(pf_row(f"+ {lab_}", base + relabel(dA, "d"), R4 + ("d",), extra=extra))
        P(pf_row("+ d, entry 15 min later", base + relabel(on(MK["Dlate"], pd_), "d"), R4 + ("d",), extra=EX))
        P(pf_row("+ d, threshold −25 bp", base + relabel(on(MK["D25"], pd_), "d"), R4 + ("d",), extra=EX))
        P(pf_row("+ d, exit after 8 h", base + relabel(on(MK["D8h"], pd_), "d"), R4 + ("d",), extra=EX))
        P(pf_row("+ d, chronic-discount coins skipped", base + relabel(on(MK["Dch"], pd_), "d"), R4 + ("d",), extra=EX))
        for extra_cost in (0.0016, 0.0036):
            P(pf_row(f"+ d, alt costs {0.14 + extra_cost * 100:.2f} % round trip", base + [dict(c, rule="d", ret=c["ret"] - extra_cost) for c in dA],
                     R4 + ("d",), extra=EX))
        for nd in (5, 10, 20):
            bd = best_days(dA, nd)
            P(pf_row(f"+ d without its {nd} best days", base + relabel([c for c in dA if DAY[c["i"]] not in bd], "d"), R4 + ("d",), extra=EX))
        P(pf_row("+ d without 10–11.10.2025", base + relabel([c for c in dA if DAY[c["i"]] not in ("2025-10-10", "2025-10-11")], "d"), R4 + ("d",), extra=EX))
        P(pf_row("+ f, entry 15 min later (8 h hold)", base + relabel(on(MK["F8hlate"], pf_), "f"), R4 + ("f",), extra=EX))
        P(pf_row("+ f 8 h hold", base + relabel(on(MK["F8h"], pf_), "f"), R4 + ("f",), extra=EX))
        P(pf_row("+ f 15 % ×3", base + relabel(fA, "f"), R4 + ("f",), extra=dict(f=dict(share=0.15, open=3))))
        P(pf_row("+ f, alt costs 0.30 %", base + [dict(c, rule="f", ret=c["ret"] - 0.0016) for c in fA], R4 + ("f",), extra=EX))
        bd = best_days(fA, 10)
        P(pf_row("+ f without its 10 best days", base + relabel([c for c in fA if DAY[c["i"]] not in bd], "f"), R4 + ("f",), extra=EX))

    P("\n**Aggressive market variants** (replace the rule on core + top-100 alts; everything else «норма»+D on core):\n")
    P(PFH); P(PFS)
    allk = core + alts
    def swap(r, tag, ks=allk): return [c for c in base if c["rule"] != r] + on(MK[tag], ks)
    for r, tag in (("B", "B30"), ("B", "B25"), ("E", "E8h"), ("E", "E30"), ("F", "F8"), ("F", "F8h"), ("D", "D15"),
                   ("D", "D8h"), ("D", "Dch"), ("D", "D15ch")):
        P(pf_row(f"{tag} on core only", swap(r, tag, core), R4))
        P(pf_row(f"{tag} on core + alts", swap(r, tag), R4))
    P()

if "lab" in SECT or "altpilot" in SECT:
    P("## Separate accounts: alt-only autopilot and aggressive candidates ($10k each)\n")
    P(PFH.replace("portfolio («норма» + D, $10k)", "account")); P(PFS)
    R4 = ("B", "E", "F", "D")
    for gn, G in GROUPS:
        alt_only = [c for r in "BEFD" for c in on(MK[r], G)]
        P(pf_row(f"alt-only autopilot «норма»+D, all {gn}", alt_only, R4))
        P(pf_row(f"alt-only, F + D only, all {gn}", [c for r in "FD" for c in on(MK[r], G)], ("F", "D")))
        P(pf_row(f"alt-only, D only 15 % ×3, all {gn}", on(MK["D"], G), ("D",)))
        P(pf_row(f"alt-only, D only 10 % ×6, all {gn}", on(MK["D"], G), ("D",), d_share=0.10, d_open=6))
    for tag, sh, op in (("D15", 0.10, 6), ("D15ch", 0.10, 6), ("D8h", 0.10, 6), ("Dch", 0.15, 3)):
        P(pf_row(f"{tag} only, {int(sh * 100)} % ×{op}, core + top-100 alts", [dict(c, rule="D") for c in on(MK[tag], core + alts)], ("D",),
                 d_share=sh, d_open=op))
    P(pf_row("E only (basket 20 %), top-100 alts", on(MK["E"], alts), ("E",)))
    P(pf_row("E8h only (basket 20 %), top-100 alts", [dict(c, rule="E") for c in on(MK["E8h"], alts)], ("E",)))
    P(pf_row("F8h only 25 % ×3, core + top-100 alts", [dict(c, rule="F") for c in on(MK["F8h"], core + alts)], ("F",)))
    P(pf_row("F8 (OI −8 %, price −4 %) only 25 % ×3, core + top-100 alts", [dict(c, rule="F") for c in on(MK["F8"], core + alts)], ("F",)))
    P()

if "lab" in SECT:
    P("## Lab account as configured now (risk 0.5 % per trade by stop distance, ≤ 12 open, one position per coin)\n")
    P(PFH.replace("portfolio («норма» + D, $10k)", "set")); P(PFS)
    LAB = dict(risk=0.005, max_open=12, e_share=0, f_share=0, f_open=0, d_share=0, d_open=0)
    def lab(cs): return [dict(c, rule="B") for c in cs]         # B branch of simulate = risk-sized, max_open cap
    P(pf_row("L+N+Q (live lab rules) on core 18", lab(on(LM["L"] + LM["N"] + LM["Q"], core)), ("B",), **LAB))
    P(pf_row("L+N+Q on core + top-100 alts", lab(on(LM["L"] + LM["N"] + LM["Q"], core + alts)), ("B",), **LAB))
    P(pf_row("L only on core 18", lab(on(LM["L"], core)), ("B",), **LAB))
    P(pf_row("L only on core + top-100 alts", lab(on(LM["L"], core + alts)), ("B",), **LAB))
    P(pf_row("N only on core 18", lab(on(LM["N"], core)), ("B",), **LAB))
    P(pf_row("Q only on core 18", lab(on(LM["Q"], core)), ("B",), **LAB))
    for f_, tag in BEST.items():
        P(pf_row(f"{tag} (IS best {f_}) on core + alts", lab(on(LM[tag], core + alts)), ("B",), **LAB))
    for tag in ("B25", "B30", "D15", "E8h", "F8", "D15ch"):
        if tag in MK: P(pf_row(f"{tag} alone on core + alts (risk 0.5 % by stop)", lab(on(MK[tag], core + alts)), ("B",), **LAB))
    for sh in (0.05, 0.02):                                    # the same rules at a small fixed size instead of 0.5 % risk
        P(pf_row(f"L+N+Q on core 18, fixed {sh * 100:.0f} % of the account per trade, ≤ 12 open",
                 relabel(on(LM["L"] + LM["N"] + LM["Q"], core), "n"), ("n",), extra=dict(n=dict(share=sh, open=12)), **LAB))
    sz = {t: np.median([c["sf"] for c in LM[t]]) * 100 for t in ("L", "N", "Q")}
    P(f"\nMedian stop distance (incl. costs): L {sz['L']:.2f} %, N {sz['N']:.2f} %, Q {sz['Q']:.2f} % → at 0.5 % risk a position is "
      f"{50 / sz['N']:.0f} % (N) / {50 / sz['Q']:.0f} % (Q) of the account, capped at 300 %.")
    P()

if "lists" in SECT:
    P("## IS-pass coin lists (decided on 2023–2024 only)\n")
    for tag, nmin in (("B", 5), ("B30", 5), ("E", 20), ("F", 5), ("F8h", 5), ("D", 3), ("D25", 3), ("Dch", 3)):
        for gn, G in GROUPS:
            ps = is_pass(MK[tag], G, nmin)
            P(f"{tag} · {gn} ({len(ps)}/{len(G)}): {' '.join(syms[k] for k in ps) or '—'}  ")
    P()

if "labx" in SECT:
    PG, PGN = (altsV, "point-in-time top-40 alts") if altsV else (alts, "top-100 alts")
    P(f"## Lab candidates without hindsight: core 18 + {PGN} (separate $10k each)\n")
    P(PFH.replace("portfolio («норма» + D, $10k)", "account")); P(PFS)
    R4 = ("B", "E", "F", "D")
    def acc(label, cs, rules, **kw): P(pf_row(label, cs, rules, **kw))
    base = [c for r in "BEFD" for c in on(MK[r], core)]
    acc("reference: core «норма»+D", base, R4)
    acc("core, E breadth 0.3 instead of 0.4", [c for c in base if c["rule"] != "E"] + on(MK["E30"], core), R4)
    acc("core, E exit after 8 h", [c for c in base if c["rule"] != "E"] + [dict(c, rule="E") for c in on(MK["E8h"], core)], R4)
    acc("core, D exit after 8 h", [c for c in base if c["rule"] != "D"] + [dict(c, rule="D") for c in on(MK["D8h"], core)], R4)
    agg = (on(MK["B25"], core) + [dict(c, rule="E") for c in on(MK["E30"], core)] + [dict(c, rule="F") for c in on(MK["F8"], core)]
           + [dict(c, rule="D") for c in on(MK["D15"], core)])
    acc("core, all loosened: B −2.5σ, E 0.3, F OI −8 %/−4 %, D −15 bp", agg, R4)
    acc("D −15 bp only on core 18, 10 % ×6", [dict(c, rule="D") for c in on(MK["D15"], core)], ("D",), d_share=0.10, d_open=6)
    acc("D −20 bp only on core 18, 15 % ×3", on(MK["D"], core), ("D",))
    pd25 = is_pass(MK["D25"], PG, 3)
    acc(f"D −25 bp only on {PGN} IS-pass ({len(pd25)}), 15 % ×3", [dict(c, rule="D") for c in on(MK["D25"], pd25)], ("D",))
    acc(f"core «норма»+D + d −25 bp on {PGN} IS-pass", base + relabel(on(MK["D25"], pd25), "d"), R4 + ("d",), extra=EX)
    pdc = is_pass(MK["Dch"], PG, 3)
    acc(f"core «норма»+D + d (chronic skipped) on {PGN} IS-pass ({len(pdc)})", base + relabel(on(MK["Dch"], pdc), "d"), R4 + ("d",), extra=EX)
    acc(f"E basket only on {PGN} (breadth on core)", on(MK["E"], PG), ("E",))
    acc(f"B −2.5σ on core + {PGN}, risk 0.5 % ×8", on(MK["B25"], core + PG), ("B",), risk=0.005, max_open=8)
    acc(f"F 8 h on core + {PGN}, 15 % ×3", [dict(c, rule="F") for c in on(MK["F8h"], core + PG)], ("F",), f_share=0.15)
    P()

P(f"_run time {time.time() - t0:.0f} s_")
if MD: open(MD, "w").write("\n".join(OUT) + "\n")
