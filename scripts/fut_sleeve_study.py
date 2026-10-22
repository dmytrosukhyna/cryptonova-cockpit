"""Backlog #31 (22.10.2026): «Фьючерсы · CRYPTO NOVA» = average of «Сбалансированная» (bal) and «Импульс BTC» (imp, rule K).
K is idle most of the time, so half of the sleeve sits in cash. Question: what if the idle half works in bal?

Inputs
  bal — web/backtest.json: the bal whole-portfolio backtest (scripts/autopilot_v2.py, 18 coins, B/E/F, risk 1 %, costs incl.),
        equity at each trade exit 2023-01 → 2026-09 (exit points, no intra-trade marks → drawdowns are understated).
  K   — simulated here with the LIVE rules (cnq/agents/paper_desk.py): BTC close / close 4 bars ago ≥ +2.5 % (15m) → next bar
        open, long the 5 most traded alts of the last 24 h among the point-in-time top 50 by 30-day volume (wide set, delisted
        incl.), 20 % of the K account each, stop 8 × ATR(30 bars 15m), exit after 12 h; one entry per coin per 4 h; 0.14 % costs.
        Data: release lab-data-all2023 (alt100.npz). Path: env CNQ_LAB_WIDE (default alt100.npz).
Variants (fixed in advance, 3 — no fitting):
  now    — two separate $5 000 halves, never rebalanced (what capital.py does: the average of two $10 000 indices);
  share  — one account: bal runs on the whole sum while K is flat; on a day K holds positions bal gets 50 % and K 50 %;
  stack  — bal always on the whole sum, K adds its 50 % on top when it fires (upper bound: gross exposure higher on K days).
Acceptance (the backlog row): drawdown of the total not deeper than «now», and both halves of the history positive."""
import datetime as dt
import json
import os
import sys

import numpy as np

DAY = 86_400_000
ROOT = os.path.join(os.path.dirname(__file__), "..")


def bal_daily(days):
    d = json.load(open(os.path.join(ROOT, "web", "backtest.json")))
    cu = sorted(d["curve"])
    eq = np.empty(len(days))
    j, last = 0, cu[0][1]
    for i, t in enumerate(days):
        while j < len(cu) and cu[j][0] < t + DAY:
            last = cu[j][1]
            j += 1
        eq[i] = last
    return eq


def k_trades(path):
    z = np.load(path, allow_pickle=True)
    syms = [str(s) for s in z["syms"]]
    T = z["times"].astype(np.int64)
    O, H, L, C, V = (z[k].astype(np.float64) for k in ("open", "high", "low", "close", "volume_quote"))
    rank = z["rank"] if "rank" in z.files else None
    b = syms.index("BTC")
    bc = C[b]
    r4 = np.full(len(T), np.nan)
    r4[4:] = bc[4:] / bc[:-4] - 1
    trig = np.where(r4 >= 0.025)[0]
    # 30-day volume rank (point in time) and 24 h turnover
    Vn = np.nan_to_num(V)
    cs = np.cumsum(Vn, axis=1)
    def win(n, i):
        return cs[:, i] - (cs[:, i - n] if i >= n else 0)
    tr = np.maximum(H - L, np.maximum(abs(H - np.roll(C, 1, axis=1)), abs(L - np.roll(C, 1, axis=1))))
    trades, last_entry, busy_until = [], {}, []
    for i in trig:
        if i + 1 >= len(T) or i < 2880:
            continue
        v30 = win(2880, i)
        top = [k for k in np.argsort(-v30)[:50] if k != b and np.isfinite(C[k, i]) and v30[k] > 0]
        v24 = win(96, i)
        cand = sorted(top, key=lambda k: -v24[k])
        busy_until = [x for x in busy_until if x > i]
        for k in cand:
            if len(busy_until) >= 5:
                break
            if last_entry.get(k, -10**9) > i - 16:
                continue
            e = O[k, i + 1]
            if not np.isfinite(e) or e <= 0:
                continue
            at = np.nanmean(tr[k, i - 29:i + 1])
            if not np.isfinite(at) or at <= 0:
                at = e * 0.003
            stop = e - 8 * at
            end = min(len(T) - 1, i + 1 + 48)
            x, xi = None, end
            for j in range(i + 1, end + 1):
                if np.isfinite(L[k, j]) and L[k, j] <= stop:
                    x, xi = stop, j
                    break
            if x is None:
                while xi > i + 1 and not np.isfinite(C[k, xi]):
                    xi -= 1
                x = C[k, xi]
            ret = x / e - 1 - 0.0014
            trades.append((int(T[i + 1]), int(T[xi]), syms[k], ret))
            last_entry[k] = i
            busy_until.append(xi)
    return trades


def k_daily(days, trades):
    """K account (all-in, 5 × 20 %): realised at the exit day; also a flag 'K in a position' per day."""
    eq = np.empty(len(days))
    active = np.zeros(len(days), bool)
    e = 1.0
    pnl = {}
    for t0, t1, _s, r in trades:
        pnl.setdefault((t1 // DAY) * DAY, []).append(r)
        for d in range((t0 // DAY) * DAY, (t1 // DAY) * DAY + DAY, DAY):
            pnl.setdefault(("a", d), True)
    for i, t in enumerate(days):
        for r in pnl.get(t, []):
            e *= 1 + 0.2 * r
        eq[i] = e
        active[i] = ("a", t) in pnl
    return eq, active


def stats(eq, days):
    r = eq[1:] / eq[:-1] - 1
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    yrs = {}
    for y in sorted({dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year for t in days}):
        ix = [i for i, t in enumerate(days) if dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year == y]
        yrs[y] = round((eq[ix[-1]] / eq[max(ix[0] - 1, 0)] - 1) * 100, 1)
    h = len(eq) // 2
    sh = float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0
    return {"total_pct": round((eq[-1] / eq[0] - 1) * 100, 1), "dd_pct": round(dd * 100, 1), "sharpe": round(sh, 2),
            "half1_pct": round((eq[h] / eq[0] - 1) * 100, 1), "half2_pct": round((eq[-1] / eq[h] - 1) * 100, 1), "years": yrs}


def main():
    path = os.environ.get("CNQ_LAB_WIDE", "alt100.npz")
    t0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    t1 = int(dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    days = np.arange(t0, t1, DAY)
    tr = [x for x in k_trades(path) if x[0] >= t0]
    rk = np.array([x[3] for x in tr])
    print(f"K trades {len(tr)}, avg {rk.mean() * 100:+.3f}%, win {(rk > 0).mean() * 100:.0f}%", flush=True)
    for y in (2023, 2024, 2025, 2026):
        a = np.array([x[3] for x in tr if dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).year == y])
        if len(a):
            print(f"  {y}: n {len(a)} avg {a.mean() * 100:+.3f}%")
    B = bal_daily(days)
    K, act = k_daily(days, tr)
    rb = np.r_[0, B[1:] / B[:-1] - 1]
    rkd = np.r_[0, K[1:] / K[:-1] - 1]
    print(f"days {len(days)}, K in a position on {act.mean() * 100:.1f}% of days")
    now = 0.5 * B / B[0] + 0.5 * K / K[0]
    share = np.cumprod(1 + np.where(act, 0.5 * rb + 0.5 * rkd, rb))
    stack = np.cumprod(1 + rb + np.where(act, 0.5 * rkd, 0))
    out = {"k_trades": len(tr), "k_avg_pct": round(float(rk.mean() * 100), 3), "k_active_days_pct": round(float(act.mean() * 100), 1),
           "bal": stats(B / B[0], days), "K": stats(K / K[0], days), "now": stats(now, days), "share": stats(share, days), "stack": stats(stack, days)}
    for k in ("bal", "K", "now", "share", "stack"):
        print(k, json.dumps(out[k]))
    json.dump(out, open(os.environ.get("CNQ_OUT", "fut_sleeve.json"), "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
