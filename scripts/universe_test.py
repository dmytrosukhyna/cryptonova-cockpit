"""Universe test: do rules B (cascade buyback) and E (panic-climax basket) stay profitable on ~25 more coins?

Data: OKX 15m USDT-swap candles from scripts/fetch_history.py (workflow research-data.yml), path as argv[1].
Rules and portfolio simulation are taken unchanged from scripts/autopilot_v2.py: its loader is pointed at the OKX
file before import, so cand_B / cand_E / simulate run on these arrays (no OI → rule F is not tested).
  * E breadth (BR) is recomputed on the original 18 coins only, so new coins don't change the trigger;
    the variant «breadth over all coins» is reported too.
  * Signals in the first 30 days after a coin's first OKX bar are dropped (rule B's 30-day volatility is not
    warmed up yet, and listing-week moves are not tradeable). Applied to every coin the same way.
  * Robustness filter decided on 2023–2024 only: coin's B+E trades (E with 18-coin breadth) avg > 0 and n ≥ 10,
    coin listed on OKX (first bar) by 2024-06-30.
    Portfolios are then judged on 2025-01-01 → now (out-of-sample), start $10k.
Usage: python scripts/universe_test.py okx15m.npz [--md out.md]"""
import sys, os, collections, datetime as dt, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ap_data

NPZ = sys.argv[1]
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()


def _load():
    z = np.load(NPZ, allow_pickle=True)
    A = {c: z[c].astype(np.float64) if c in z.files else None for c in ap_data.COLS}
    shape = A['close'].shape
    for c in ap_data.COLS:
        if A[c] is None: A[c] = np.full(shape, np.nan)
    return [str(s) for s in z['syms']], z['times'].astype(np.int64), A


ap_data.load = _load
import autopilot_v2 as ap  # noqa: E402  (module globals now hold the OKX arrays)

syms, times, NS, T = ap.syms, ap.times, ap.NS, ap.T
NORMA = dict(rules=('B', 'E'), risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40)
YEAR = np.array([int(d[:4]) for d in ap.DAY])
OOS = int(np.searchsorted(times, 1735689600000))      # 2025-01-01
core = [k for k, s in enumerate(syms) if s in CORE18]
first = np.array([np.argmax(np.isfinite(ap.C[k])) for k in range(NS)])
WARM = 30 * 96
LATE = 1719705600000                                  # 2024-06-30: later listings have < 6 months in-sample → not eligible for (b)


def breadth(rows):
    r = ap.r24[rows]; av = np.isfinite(r).sum(0)
    with np.errstate(invalid='ignore'):
        return np.where(av >= 8, (r < -0.03).sum(0) / np.maximum(av, 1), 0)


def clean(cs): return [c for c in cs if c['i'] - first[c['k']] >= WARM]


B = clean(ap.cand_B())
ap.BR = breadth(core); E18 = clean(ap.cand_E(br=0.4))
ap.BR = breadth(list(range(NS))); EALL = clean(ap.cand_E(br=0.4))


def stats(cs):
    a = np.array([c['ret'] for c in cs]) * 100
    return (len(a), a.mean() if len(a) else np.nan, (a > 0).mean() * 100 if len(a) else np.nan)


def run(cands, coins, i0, e_coins=None):
    """Simulate «норма» B+E on the given coins with entries from bar i0; stats over [i0, end].
    e_coins: if given, rule E trades only these coins (B still trades `coins`)."""
    ks = set(coins); ke = set(e_coins) if e_coins is not None else ks
    cs = [c for c in cands if (c['k'] in (ke if c['rule'] == 'E' else ks)) and c['i'] >= i0]
    eq, tr, cu = ap.simulate(cs, **NORMA)
    cu = [(i0, 10_000.0)] + sorted(cu[1:]); v = np.array([x for _, x in cu]); pk = np.maximum.accumulate(v)
    days = (times[-1] - times[i0]) / 86400_000
    y = collections.defaultdict(float)
    for t in tr: y[ap.DAY[t['x']][:4]] += t['pnl']
    return dict(final=eq, cagr=(eq / 1e4) ** (365.25 / days) - 1, dd=((v - pk) / pk).min(), n=len(tr), tpd=len(tr) / days,
                years={k: round(x) for k, x in sorted(y.items())}, nb=sum(t['rule'] == 'B' for t in tr), ne=sum(t['rule'] == 'E' for t in tr))


out = []
P = lambda s='': (print(s, flush=True), out.append(s))
P(f"# OKX universe test — {len(syms)} coins, {ap.DAY[ap.START]} → {ap.DAY[-1]}\n")
P(f"Coins in file: {' '.join(syms)}")
P(f"Missing from the 18: {' '.join(c for c in CORE18 if c not in syms) or '—'}")
_z = np.load(NPZ, allow_pickle=True)
P(f"Fetch notes: {'; '.join(map(str, _z['notes'])) if 'notes' in _z.files else '—'}\n")

# ---- per coin -----------------------------------------------------------------------------------------------------
yrs = sorted(set(YEAR[ap.START:]))
P("| coin | from | B n | B avg | E n | E avg | " + " | ".join(f"B+E {y}" for y in yrs) + " | 23–24 avg (n) | 25–26 avg (n) | pass |")
P("|---|---|---|---|---|---|" + "---|" * len(yrs) + "---|---|---|")
passed, rows = [], {}
for k, s in enumerate(syms):
    bk = [c for c in B if c['k'] == k]; ek = [c for c in E18 if c['k'] == k]; be = bk + ek
    ins = [c for c in be if YEAR[c['i']] <= 2024]; oos = [c for c in be if YEAR[c['i']] >= 2025]
    ni, ai, _ = stats(ins); no, ao, _ = stats(oos)
    ok = ni >= 10 and ai > 0 and times[first[k]] <= LATE
    if ok and s not in CORE18: passed.append(k)
    cells = []
    for y in yrs:
        n, a, _ = stats([c for c in be if YEAR[c['i']] == y]); cells.append(f"{a:+.2f}% ({n})" if n else "—")
    rows[s] = dict(ins=(ni, ai), oos=(no, ao))
    P(f"| {s}{'' if s not in CORE18 else '*'} | {ap.DAY[first[k]]} | {len(bk)} | {stats(bk)[1]:+.2f}% | {len(ek)} | {stats(ek)[1]:+.2f}% | "
      + " | ".join(cells) + f" | {ai:+.2f}% ({ni}) | {ao:+.2f}% ({no}) | {'yes' if ok else 'no'} |")
P("\n\\* = one of the current 18 coins. avg = average net return per trade, costs included (0.14% round trip).\n")

# ---- rule level, pooled ---------------------------------------------------------------------------------------------
new = [k for k in range(NS) if syms[k] not in CORE18]
P("| group | rule | " + " | ".join(str(y) for y in yrs) + " | all |")
P("|---|---|" + "---|" * (len(yrs) + 1))
for gname, ks in (("18 core", core), ("new coins", new), ("new, passed filter", passed)):
    for rn, cs in (("B", B), ("E (18-coin breadth)", E18), ("E (all-coin breadth)", EALL)):
        g = [c for c in cs if c['k'] in set(ks)]
        cells = []
        for y in yrs:
            n, a, w = stats([c for c in g if YEAR[c['i']] == y]); cells.append(f"{a:+.2f}% n={n}" if n else "—")
        n, a, w = stats(g)
        P(f"| {gname} | {rn} | " + " | ".join(cells) + f" | {a:+.2f}% n={n} WR {w:.0f}% |")
P()

# ---- portfolios -----------------------------------------------------------------------------------------------------
P(f"Filter (2023–2024 only, B+E avg > 0, n ≥ 10, listed by 2024-06-30) passed: {', '.join(syms[k] for k in passed)}")
P(f"Failed: {', '.join(syms[k] for k in new if k not in passed)}\n")
sets = (("(a) 18 coins", core, None), ("(b) 18 + passed filter", core + passed, None), ("(c) all coins", list(range(NS)), None),
        ("(d) B on 18 + passed, E on 18", core + passed, core), ("(e) B on all, E on 18", list(range(NS)), core))
for title, i0 in (("Full period 2023-01 → now", ap.START), ("Out-of-sample 2025-01 → now (filter chosen on 2023–2024)", OOS)):
    P(f"### {title}, «норма» B+E, start $10k\n")
    P("| set | coins | E breadth | final | CAGR | max DD | trades | per day | B / E | P&L by year |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for nm, ks, ke in sets:
        for bn, E in (("18", E18), ("all", EALL)):
            if (nm.startswith("(a)") or ke is not None) and bn == "all": continue
            r = run(B + E, ks, i0, ke)
            P(f"| {nm} | {len(ks)} | {bn} | ${r['final']:,.0f} | {r['cagr'] * 100:+.1f}% | {r['dd'] * 100:.1f}% | {r['n']} | {r['tpd']:.2f} | "
              f"{r['nb']} / {r['ne']} | {r['years']} |")
    P()

if '--md' in sys.argv:
    open(sys.argv[sys.argv.index('--md') + 1], 'w').write("\n".join(out) + "\n")
