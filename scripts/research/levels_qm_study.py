"""Owner 26.11.2026 (live): «стратегия уровней — развивать максимально; паттерн квазимодо (стоп ниже низа); сверять сделки китов с
нашей стратегией, переподтверждать и отсеивать». Research only — nothing here trades or changes the desks.

PRE-REGISTERED 26.11.2026 ~20:00 UTC, before any run (only a load test on the data shape was made).

Data: release lab-data-core18 (alt100.npz, sha256 7f72980b…; Binance USDT-M 15m, 2022-11-20 → 2026-11-04; BTC, ETH, SOL + 15 alts),
the same file as scripts/research/levels_study.py. H1 bars from full 15m quads, H4 from H1 (UTC-aligned), levels from
cnq/agents/levels_desk.py (unchanged). Halves: IS before 2024-11-12, OOS from then (same split as levels_study).

PART A — Quasimodo (QM). Swings: fractals on the TF bars (H1: k = 3, H4: k = 2), a swing is known k TF bars after it; consecutive
swings of one type are collapsed to the more extreme one.
  Bullish QM: the last three known swings are L1 (low), H1 (high), L2 (low) with L2 < L1; at the close of an H1 bar i the close
  breaks above H1 (first such close after L2 is known, before any new low below L2) → a LIMIT BUY at L1 («левое плечо»), valid
  72 H1 bars (H1 TF) / 120 H1 bars (H4 TF); cancelled if a new low below L2 prints first (that bar counts as a stop if it filled).
  Stop = L2 − 0.25 × ATR(H1, 14) at the break («стоп ниже низа»). Risk must be 0.3 %–8 % of the entry, else skipped.
  Bearish QM: the mirror (H1, L1, H2 with H2 > H1, close breaks below L1 → limit sell at H1, stop H2 + 0.25 ATR).
  Targets: «2R», «3R», «struct» = the broken swing (H1 for longs / L1 for shorts) if ≥ 1R away, else 2R. Max hold 120 H1 bars.
  The fill bar checks only the stop (levels_desk.step_trade fill_bar rule). Costs as levels_desk (0.10 % + 0.01 % / 8 h).
  One open trade per coin per variant. 12 variants = TF {1h, 4h} × side {L, S} × target {2R, 3R, struct}.
  PASS (12 variants, Bonferroni 0.05 / 12 two-sided → z ≥ 2.87): IS mean > 0 AND OOS mean > 0 AND OOS daily t ≥ 2.87 AND OOS n ≥ 100
  AND the OOS mean above all 100 random-entry means (same coin, side, % stop / target distances and max hold, random OOS bars).

PART B — whale trades re-confirmed by levels. Trades: data/agents.sqlite ws_trips (OKX lead traders, 2026-07-20 → 2026-10-20) on the
18 coins. Decisions: one trader + coin + side, fills opened within 4 h of the first → one decision (first open time, its close).
Copy: entry = close of the 15m bar holding open_t + 2 min; exit = close of the 15m bar holding close_t + 2 min, or the −16 % stop
on 15m highs/lows on the way; cost 0.11 % a round trip. Context at the last H1 bar closed before entry (levels_at, ATR):
  F1 «у опоры»: long with price − S ≤ 1 ATR / short with R − price ≤ 1 ATR (trading from the level) — KEEP these;
  F2 «в стену» (veto): long with R − price ≤ 0.5 ATR / short with price − S ≤ 0.5 ATR (straight into the next level) — DROP these;
  F3 «QM за»: a same-side QM (H1 TF, part A rules) broke in the last 72 H1 bars on the coin — KEEP these.
  A filter WORKS if: kept n ≥ 100, the kept mean > 0 and > the all-decisions mean in BOTH halves (split at 2026-09-04), and Welch t of
  kept vs dropped ≥ 2.0. Three filters, reported all; no tuning after the run.
Usage: levels_qm_study.py alt100.npz agents.sqlite OUT.json"""
import json, math, os, random, sqlite3, statistics as st, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from cnq.agents import levels_desk as LD

H, DAY = LD.H, LD.DAY
MID = 1_731_369_600_000          # 2024-11-12 00:00 UTC
MID_B = 1_788_480_000_000        # 2026-09-04 00:00 UTC
Z = 2.87
RNG = random.Random(20261126)
MAXB = 120


def load(path):
    d = np.load(path)
    T = d["times"].astype(np.int64)
    out, raw = {}, {}
    for k, s in enumerate(d["syms"]):
        o, h, l, c, v = (d[x][k].astype(float) for x in ("open", "high", "low", "close", "volume_quote"))
        ok = np.isfinite(c) & (c > 0)
        raw[str(s)] = (T[ok], o[ok], h[ok], l[ok], c[ok])
        hb = T - T % H
        rows = {}
        for i in np.flatnonzero(ok):
            b = int(hb[i])
            if b not in rows:
                rows[b] = [b, o[i], h[i], l[i], c[i], v[i], 1]
            else:
                r = rows[b]; r[2] = max(r[2], h[i]); r[3] = min(r[3], l[i]); r[4] = c[i]; r[5] += v[i]; r[6] += 1
        ks = sorted(b for b, r in rows.items() if r[6] == 4)
        out[str(s)] = [rows[b][:6] for b in ks]
    return out, raw


def tstat(xs):
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None


def welch(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb else None


def summ(tr):
    if not tr:
        return {"n": 0}
    xs = [x["ret"] for x in tr]
    days = defaultdict(list)
    for x in tr:
        days[x["exit_t"] // DAY].append(x["ret"])
    dm = [st.mean(v) for v in days.values()]
    gp, gl = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(st.mean(xs), 3), "median": round(st.median(xs), 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3),
            "pf": round(gp / gl, 2) if gl else None, "worst": round(min(xs), 2),
            "stopped": round(sum(1 for x in tr if x["why"] == "стоп") / len(tr), 3), "t": tstat(xs), "t_daily": tstat(dm), "days": len(dm)}


# ── swings & QM ──────────────────────────────────────────────────────────────────────────────────────────────────────
def swings(P, tf):
    """[(known_at H1 index, extreme H1-or-TF index, price, 'H'|'L')] collapsed to alternating"""
    t, o, h, l, c, v = P["t"], P["o"], P["h"], P["l"], P["c"], P["v"]
    if tf == "1h":
        hi, lo = LD.fractals(h, l, 3)
        raw = [(j + 3, p, "H") for j, p in hi] + [(j + 3, p, "L") for j, p in lo]
    else:
        g = LD.group(t, o, h, l, c, v, 4 * H)
        gh, gl = LD.fractals(g[2], g[3], 2)
        last = g[6]
        raw = [(last[j + 2], p, "H") for j, p in gh if j + 2 < len(last)] + [(last[j + 2], p, "L") for j, p in gl if j + 2 < len(last)]
    raw.sort()
    out = []
    for k, p, ty in raw:
        if out and out[-1][2] == ty:
            if (ty == "H" and p > out[-1][1]) or (ty == "L" and p < out[-1][1]):
                out[-1] = (k, p, ty)          # known later, more extreme
            continue
        out.append((k, p, ty))
    return out


def qm_setups(P, tf):
    """[(break H1 index, side, entry level, stop, struct target)] per part A"""
    sw = swings(P, tf)
    c, h, l, a = P["c"], P["h"], P["l"], P["atr"]
    n = len(c)
    res = []
    for q in range(2, len(sw)):
        s1, s2, s3 = sw[q - 2], sw[q - 1], sw[q]
        k0 = s3[0]
        k1 = sw[q + 1][0] if q + 1 < len(sw) else n
        if s1[2] == "L" and s2[2] == "H" and s3[2] == "L" and s3[1] < s1[1]:
            side, lvl, head, brk = "L", s1[1], s3[1], s2[1]
        elif s1[2] == "H" and s2[2] == "L" and s3[2] == "H" and s3[1] > s1[1]:
            side, lvl, head, brk = "S", s1[1], s3[1], s2[1]
        else:
            continue
        for i in range(k0, min(k1, n)):
            if side == "L" and l[i] < head:
                break
            if side == "S" and h[i] > head:
                break
            if (side == "L" and c[i] > brk) or (side == "S" and c[i] < brk):
                if a[i]:
                    stop = head - 0.25 * a[i] if side == "L" else head + 0.25 * a[i]
                    res.append((i, side, lvl, stop, brk))
                break
    return res


def run_qm(PP, tf, side, tg):
    trades = []
    wait = 72 if tf == "1h" else 120
    for coin, P in PP.items():
        o, h, l, c, t = P["o"], P["h"], P["l"], P["c"], P["t"]
        n = len(c)
        busy = -1
        for (i, sd, lvl, stop, brk) in P["_qm"][tf]:
            if sd != side or i <= busy or i < 300:
                continue
            risk = abs(lvl - stop)
            if not (0.003 <= risk / lvl <= 0.08):
                continue
            if tg == "2R":
                tgt = lvl + 2 * risk if side == "L" else lvl - 2 * risk
            elif tg == "3R":
                tgt = lvl + 3 * risk if side == "L" else lvl - 3 * risk
            else:
                tgt = brk if abs(brk - lvl) >= risk else (lvl + 2 * risk if side == "L" else lvl - 2 * risk)
            fill = None
            for j in range(i + 1, min(i + 1 + wait, n)):
                if (side == "L" and l[j] <= lvl) or (side == "S" and h[j] >= lvl):
                    fill = j
                    break
                if (side == "L" and h[j] >= tgt) or (side == "S" and l[j] <= tgt):
                    break                              # ran to the target without a retest → no trade
            if fill is None:
                continue
            entry = min(o[fill], lvl) if side == "L" else max(o[fill], lvl)
            tr = {"side": side, "entry": entry, "stop": stop, "tgt": tgt, "risk": risk, "bars": 0, "maxb": MAXB}
            res = LD.step_trade(tr, (entry, h[fill], l[fill], c[fill]), P["atr"][fill], fill_bar=True)
            k = fill
            while res is None and k + 1 < n:
                k += 1
                res = LD.step_trade(tr, (o[k], h[k], l[k], c[k]), P["atr"][k])
            if res is None:
                break
            px, why = res
            busy = k
            trades.append({"coin": coin, "t": t[fill], "exit_t": t[k] + H, "ret": LD.ret_pct(side, entry, px, tr["bars"]), "why": why,
                           "bars": tr["bars"], "stop_pct": abs(stop / entry - 1), "tgt_pct": abs(tgt / entry - 1)})
    return trades


def null_means(PP, oos, reps=100):
    by = defaultdict(list)
    for x in oos:
        by[x["coin"]].append(x)
    out = []
    for _ in range(reps):
        rs = []
        for coin, xs in by.items():
            P = PP[coin]
            idx = [i for i in range(len(P["t"])) if P["t"][i] >= MID] if "_oosidx" not in P else P["_oosidx"]
            P["_oosidx"] = idx
            for x in xs:
                i = RNG.choice(idx[:-MAXB - 2])
                e = P["o"][i]
                side = x["side"]
                stop = e * (1 - x["stop_pct"]) if side == "L" else e * (1 + x["stop_pct"])
                tgt = e * (1 + x["tgt_pct"]) if side == "L" else e * (1 - x["tgt_pct"])
                tr = {"side": side, "entry": e, "stop": stop, "tgt": tgt, "risk": abs(e - stop), "bars": 0, "maxb": MAXB}
                k = i - 1
                r = None
                while r is None:
                    k += 1
                    r = LD.step_trade(tr, (P["o"][k], P["h"][k], P["l"][k], P["c"][k]), P["atr"][k])
                rs.append(LD.ret_pct(side, e, r[0], tr["bars"]))
        out.append(st.mean(rs))
    return out


# ── part B ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def decisions(db, coins):
    c = sqlite3.connect(db)
    q = ",".join("?" * len(coins))
    rows = c.execute(f"select trader, coin, side, open_t, close_t from ws_trips where coin in ({q}) order by trader, coin, side, open_t", coins).fetchall()
    out, cur = [], None
    for tr, coin, side, ot, ct in rows:
        if cur and cur[0] == tr and cur[1] == coin and cur[2] == side and ot - cur[3] <= 4 * H:
            continue
        cur = (tr, coin, side, ot, ct)
        out.append(cur)
    return out


def copy_ret(raw, side, ot, ct):
    T, o, h, l, c = raw
    i = int(np.searchsorted(T, ot + 120_000, "right")) - 1
    k = int(np.searchsorted(T, ct + 120_000, "right")) - 1
    if i < 0 or k >= len(T) or k <= i:
        return None
    e = c[i]
    stop = e * 0.84 if side == "LONG" else e * 1.16
    px, why = c[k], "кит"
    for j in range(i + 1, k + 1):
        if side == "LONG" and l[j] <= stop:
            px, why = stop, "стоп"; break
        if side == "SHORT" and h[j] >= stop:
            px, why = stop, "стоп"; break
    s = 1 if side == "LONG" else -1
    return (s * (px / e - 1) - 0.0011) * 100, why, T[i]


def part_b(PP, raw, db):
    D = decisions(db, list(PP))
    recs = []
    for tr, coin, side, ot, ct in D:
        r = copy_ret(raw[coin], side, ot, ct)
        if r is None:
            continue
        ret, why, te = r
        P = PP[coin]
        i = int(np.searchsorted(np.array(P["t"]), te - te % H, "left")) - 1   # last H1 bar closed before entry
        if i < 300 or not P["atr"][i]:
            continue
        px, a = P["c"][i], P["atr"][i]
        S, R, _, _ = LD.nearest(LD.levels_at(P, i), px)
        L = side == "LONG"
        f1 = (S is not None and px - S <= a) if L else (R is not None and R - px <= a)
        f2 = (R is not None and R - px <= 0.5 * a) if L else (S is not None and px - S <= 0.5 * a)
        sd = "L" if L else "S"
        f3 = any(b <= i < b + 72 and s_ == sd for (b, s_, *_x) in P["_qm"]["1h"])
        recs.append({"t": te, "exit_t": ct, "ret": ret, "why": why, "f1": f1, "f2": f2, "f3": f3, "coin": coin, "trader": tr})
    out = {"decisions": len(recs), "traders": len({x["trader"] for x in recs})}
    halves = (lambda x: x["t"] < MID_B, lambda x: x["t"] >= MID_B)
    for name, keep in (("all", lambda x: True), ("F1_у_опоры", lambda x: x["f1"]), ("F2_вето_в_стену", lambda x: not x["f2"]), ("F3_QM_за", lambda x: x["f3"])):
        kept = [x for x in recs if keep(x)]
        drop = [x for x in recs if not keep(x)]
        row = {"kept": summ(kept), "dropped": summ(drop), "welch_t": welch([x["ret"] for x in kept], [x["ret"] for x in drop])}
        for hn, hf in zip(("h1", "h2"), halves):
            row[hn] = {"kept": summ([x for x in kept if hf(x)]), "all": summ([x for x in recs if hf(x)])}
        if name != "all":
            row["works"] = bool(row["kept"]["n"] >= 100 and all(row[hn]["kept"].get("mean", -1) > 0 and row[hn]["kept"]["mean"] > row[hn]["all"].get("mean", 0) for hn in ("h1", "h2"))
                                and (row["welch_t"] or 0) >= 2.0)
        out[name] = row
    return out


def main():
    npz, db, outp = sys.argv[1:4]
    D, raw = load(npz)
    PP = {}
    for coin, K in D.items():
        t, o, h, l, c, v = ([r[j] for r in K] for j in range(6))
        P = LD.prep(t, o, h, l, c, v)
        P["_qm"] = {tf: qm_setups(P, tf) for tf in ("1h", "4h")}
        PP[coin] = P
        print(coin, len(c), {tf: len(P["_qm"][tf]) for tf in P["_qm"]}, flush=True)
    res = {"part_a": {}, "part_b": None}
    for tf in ("1h", "4h"):
        for side in ("L", "S"):
            for tg in ("2R", "3R", "struct"):
                v = f"qm|{tf}|{side}|{tg}"
                tr = run_qm(PP, tf, side, tg)
                for x in tr:
                    x["side"] = side
                is_ = [x for x in tr if x["t"] < MID]
                oos = [x for x in tr if x["t"] >= MID]
                row = {"is": summ(is_), "oos": summ(oos), "all": summ(tr)}
                pre = (row["is"].get("mean", -1) > 0 and row["oos"].get("mean", -1) > 0 and (row["oos"].get("t_daily") or 0) >= Z and row["oos"]["n"] >= 100)
                if pre:
                    nm = null_means(PP, oos)
                    row["null"] = {"max": round(max(nm), 3), "mean": round(st.mean(nm), 3)}
                    row["passed"] = row["oos"]["mean"] > max(nm)
                else:
                    row["passed"] = False
                res["part_a"][v] = row
                print(v, json.dumps({k: row[k] for k in ("is", "oos")}, ensure_ascii=False), row["passed"], flush=True)
    res["part_b"] = part_b(PP, raw, db)
    print(json.dumps(res["part_b"], ensure_ascii=False)[:3000])
    json.dump(res, open(outp, "w"), ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
