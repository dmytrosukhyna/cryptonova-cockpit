"""Desk improver (owner 27.11.2026 09:38: «каждую стратегию не только тестировать, но и улучшать логикой»). PAPER / RESEARCH ONLY.
A daily job (cnq/api/improver.py). For every paper desk with ≥ 10 forward closes:
1. DIAGNOSIS from its own forward trades + OKX hourly candles: stop-then-target share (stopped, then the target within 72 h),
   MFE / MAE percentiles in R, give-back (losers that were ≥ +1R first), flat trades (|result| < 0.3 % after ≥ 24 h), time in trade,
   losses by coin / side / UTC 6-hour block / BTC 4h regime (EMA20 vs EMA50), entry slippage vs its level (when known).
2. SYMPTOM → FIX (logic, not curve fitting):
     stop-then-target ≥ 30 % of stops  → a wider stop (1.5 / 2 / 3 ATR, beyond the swing) and an entry after the sweep (limit beyond the level)
     give-back ≥ 30 % of losers        → breakeven at +1R, ATR trail, half at +1R
     flat ≥ 30 % or median hold ≥ 48 h → a time stop (24 / 48 h)
     ≥ 50 % of the losses in one coin / regime (≥ 5 trades there) while the rest is positive → a filter (skip that coin / regime)
3. TEST on history — levels-family desks (whose rules are the lab grammar, scripts/research/levels_lab_study.py): the parent and each
   fix on the same OKX hourly history (~250 days, the desk's coins), split in two halves by time. Every fix tested that day counts in
   the Bonferroni correction (z = Φ⁻¹(1 − 0.025 / m)). PASS = the fix's mean > 0 and > the parent's in BOTH halves, ≥ 30 trades a half,
   2nd-half daily t ≥ z. Without a pass, the best fix that beats its parent in both halves is spawned anyway, labelled unproven.
   Whale-stream desks (exp_desks / exp_factory) have no history on the server (the trader history lives in the GitHub study): their
   fixes are spawned as factory specs labelled «не проверено на истории» — the factory's forward test is the test.
4. CHILDREN: ≤ 2 new a day, ≤ 10 alive, each with lineage {parent, fix, born, test}. A child with ≥ 20 forward closes whose mean
   per trade is below its parent's over the same period is killed (stops trading, the record stays).
Stdlib + numpy (for the lab code)."""
from __future__ import annotations

import json
import math
import os
import statistics as st
import time
from collections import defaultdict

H, DAY = 3_600_000, 86_400_000
MIN_FWD, KILL_N, NEW_A_DAY, MAX_ALIVE = 10, 20, 2, 10


# ── 1. the desks and their forward trades, normalised ─────────────────────────────────────────────────────────────────────
def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _side(s):
    return "L" if str(s).upper() in ("L", "LONG") else "S"


def desks(root: str) -> list:
    """[{key, kind, variant?, coins?, trades: [{coin, side, t, t_close, entry, stop, tgt, pct, why}]}] from the server data dir"""
    out = []
    lv = _load(os.path.join(root, "levels", "state.json")) or {}
    if lv.get("closed"):
        out.append({"key": "levels", "kind": "lab", "variant": "desk|L|E_close|a0.5|opp|none", "approx": True,
                    "trades": [_n(c, c.get("opened_t"), c.get("stop0")) for c in lv["closed"]]})
    for m, var in (("a1.5", "desk|L|E_close|a1.5|opp|none"), ("a2", "desk|L|E_close|a2|opp|none"), ("a3", "desk|L|E_close|a3|opp|none"),
                   ("swing", "desk|L|E_close|swing|opp|none"), ("swing24", "desk|L|E_close|swing|opp|t24")):
        w = _load(os.path.join(root, "levels", f"wide_{m}.json")) or {}
        if w.get("closed"):
            out.append({"key": f"levels_wide_{m}", "kind": "lab", "variant": var, "approx": True,
                        "trades": [_n(c, c.get("opened_t"), c.get("stop0")) for c in w["closed"]]})
    qm = _load(os.path.join(root, "qm", "state.json")) or {}
    q = [c for c in qm.get("closed") or [] if not c.get("replay") and int(c.get("opened_t") or 0) + H > int(qm.get("started") or 0)]
    if q:
        out.append({"key": "qm", "kind": "other", "trades": [_n(c, c.get("opened_t"), c.get("stop0")) for c in q]})
    d = os.path.join(root, "levels_lab")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.startswith("desk_") and fn.endswith(".json"):
                s_ = _load(os.path.join(d, fn)) or {}
                cl = list((s_.get("closed") or {}).values())
                spec = s_.get("spec") or {}
                if cl:
                    out.append({"key": "lab_" + fn[5:-5], "kind": "lab", "variant": spec.get("variant") or s_.get("variant"),
                                "coins": spec.get("coins"), "trades": [_n(c, c.get("fill_t") or c.get("t"), c.get("stop"), c.get("exit_t")) for c in cl]})
    ex = _load(os.path.join(root, "exp_desks", "state.json")) or {}
    by = defaultdict(list)
    for c in ex.get("closed") or []:
        by[c.get("v")].append(c)
    for v, cl in by.items():
        out.append({"key": f"exp_{v}", "kind": "whale", "variant": v, "trades": [_n(c, c.get("opened_t"), None) for c in cl]})
    fs = _load(os.path.join(root, "exp_factory", "state.json")) or {}
    byf = defaultdict(list)
    for c in fs.get("closed") or []:
        byf[c.get("spec")].append(c)
    specs = {s_["id"]: s_ for s_ in fs.get("specs") or []}
    for sid_, cl in byf.items():
        if sid_ in specs and specs[sid_].get("alive"):
            out.append({"key": f"fac_{sid_}", "kind": "factory", "spec": specs[sid_], "trades": [_n(c, c.get("t"), None) for c in cl]})
    for o in out:
        o["trades"] = [t for t in o["trades"] if t]
    return [o for o in out if len(o["trades"]) >= MIN_FWD]


def _n(c, t_open, stop, t_close=None):
    try:
        e = float(c.get("entry") or 0) or None
        sp = float(stop) if stop else (e * float(c["stop_pct"]) / 100 if e and c.get("stop_pct") else None)
        if sp and e and stop is None:
            sp = e - sp if _side(c.get("side")) == "L" else e + sp
        return {"coin": str(c.get("coin") or "").upper(), "side": _side(c.get("side")), "t": int(t_open or c.get("t") or 0),
                "t_close": int(t_close or c.get("closed_t") or c.get("exit_t") or 0), "entry": e, "stop": sp,
                "tgt": float(c["tgt"]) if c.get("tgt") else None, "pct": float(c.get("pct") or 0), "why": str(c.get("why") or ""),
                "level": c.get("level")}
    except (TypeError, ValueError, KeyError):
        return None


# ── 2. diagnosis ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q / 100 * len(xs)))], 2)


def regime_at(btc4, t):
    """BTC 4h EMA20 vs EMA50 at the last 4h bar closed before t → 'up' / 'down' / None. btc4 = [(t, close)] ascending"""
    if not btc4:
        return None
    e20 = e50 = None
    lab = None
    for tt, c in btc4:
        if tt + 4 * H > t:
            break
        e20 = c if e20 is None else e20 + (c - e20) * 2 / 21
        e50 = c if e50 is None else e50 + (c - e50) * 2 / 51
        lab = "up" if e20 > e50 else "down"
    return lab


def diagnose(desk: dict, candles, btc4=None) -> dict:
    """candles(coin) → OKX hourly [t, o, h, l, c, v] (recent ~50 days); trades older than the candles get no MFE / MAE"""
    tr = desk["trades"]
    xs = [t["pct"] for t in tr]
    gp, gl = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    mfe, mae, give, stt, stops, flat, holds, slip = [], [], 0, 0, 0, 0, [], []
    for t in tr:
        hold = (t["t_close"] - t["t"]) / H if t["t_close"] else None
        if hold is not None:
            holds.append(hold)
            if abs(t["pct"]) < 0.3 and hold >= 24:
                flat += 1
        R = abs(t["entry"] - t["stop"]) if t.get("entry") and t.get("stop") else None
        if t.get("level") and R and t.get("entry"):
            try:
                slip.append(round((t["entry"] - float(t["level"])) / R * (1 if t["side"] == "L" else -1), 2))
            except (TypeError, ValueError):
                pass
        if "стоп" in t["why"]:
            stops += 1
        if not R or not t["coin"]:
            continue
        try:
            K = candles(t["coin"])
        except Exception:  # noqa: BLE001
            K = []
        seg = [k for k in K if t["t"] - H < k[0] < (t["t_close"] or t["t"]) ]
        if not seg:
            continue
        s = 1 if t["side"] == "L" else -1
        fav = max(s * ((k[2] if s > 0 else k[3]) - t["entry"]) for k in seg) / R
        adv = min(s * ((k[3] if s > 0 else k[2]) - t["entry"]) for k in seg) / R
        mfe.append(fav)
        mae.append(adv)
        if t["pct"] < 0 and fav >= 1:
            give += 1
        if "стоп" in t["why"] and t.get("tgt"):
            after = [k for k in K if t["t_close"] <= k[0] < t["t_close"] + 72 * H]
            if any((k[2] >= t["tgt"]) if s > 0 else (k[3] <= t["tgt"]) for k in after):
                stt += 1
    loss = defaultdict(float)
    cnt = defaultdict(int)
    for t in tr:
        keys = [("coin", t["coin"]), ("side", t["side"]), ("hour", f"{(t['t'] // H % 24) // 6 * 6:02d}-{(t['t'] // H % 24) // 6 * 6 + 6:02d}")]
        rg = regime_at(btc4, t["t"]) if btc4 else None
        if rg:
            keys.append(("regime", rg))
        for k in keys:
            cnt[k] += 1
            loss[k] += t["pct"]
    tot_loss = -sum(x for x in xs if x < 0)
    conc = None
    for k, v in sorted(loss.items(), key=lambda kv: kv[1]):
        if cnt[k] >= 5 and v < 0 and tot_loss and -v >= 0.5 * tot_loss and sum(xs) - v > 0:
            conc = {"by": k[0], "value": k[1], "n": cnt[k], "sum_pct": round(v, 2)}
            break
    losers = sum(1 for x in xs if x < 0)
    d = {"n": len(tr), "mean": round(st.mean(xs), 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None,
         "stops": stops, "stop_then_target": stt, "stt_share": round(stt / stops, 2) if stops else None,
         "mfe_r": {"p50": _pct(mfe, 50), "p75": _pct(mfe, 75), "p90": _pct(mfe, 90)}, "mae_r": {"p10": _pct(mae, 10), "p25": _pct(mae, 25), "p50": _pct(mae, 50)},
         "giveback": give, "giveback_share": round(give / losers, 2) if losers else None, "flat_share": round(flat / len(tr), 2),
         "hold_med_h": round(st.median(holds), 1) if holds else None, "slip_r_med": st.median(slip) if slip else None,
         "by": {f"{k[0]}:{k[1]}": [cnt[k], round(v, 2)] for k, v in sorted(loss.items(), key=lambda kv: kv[1])[:6]}, "concentration": conc}
    d["symptoms"] = symptoms(d)
    return d


def symptoms(d: dict) -> list:
    out = []
    if d["stops"] >= 5 and (d["stt_share"] or 0) >= 0.3:
        out.append("stop_then_target")
    if (d["giveback_share"] or 0) >= 0.3:
        out.append("giveback")
    if d["flat_share"] >= 0.3 or (d["hold_med_h"] or 0) >= 48:
        out.append("flat")
    if d["concentration"]:
        out.append("concentration")
    return out


# ── 3. fixes ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
WIDER = ("a0.5", "a1", "a1.5", "a2", "a3")


def lab_fixes(variant: str, sy: list, conc: dict | None) -> list:
    """[(fix name, child variant, extra spec)] for a lab-grammar variant"""
    try:
        stream, side, ent, sn, tn, mg = variant.split("|")
    except ValueError:
        return []
    out = []
    if "stop_then_target" in sy:
        for w in WIDER[WIDER.index(sn) + 1:] if sn in WIDER else ():
            out.append((f"стоп шире: {w.replace('a', '')} ATR", f"{stream}|{side}|{ent}|{w}|{tn}|{mg}", {}))
        if sn != "swing":
            out.append(("стоп за свингом", f"{stream}|{side}|{ent}|swing|{tn}|{mg}", {}))
        if stream in ("desk", "touch") and ent != "E_limit":
            out.append(("вход после выноса (лимит за уровнем)", f"touch|{side}|E_limit|{sn}|{tn}|{mg}", {}))
    if "giveback" in sy:
        for m, nm in (("be", "безубыток на +1R"), ("trail", "трейл 1,5 ATR после +1R"), ("part", "половина на +1R")):
            if m != mg:
                out.append((nm, f"{stream}|{side}|{ent}|{sn}|{tn}|{m}", {}))
    if "flat" in sy:
        for m in ("t24", "t48"):
            if m != mg:
                out.append((f"выход по времени {m[1:]} ч", f"{stream}|{side}|{ent}|{sn}|{tn}|{m}", {}))
    if "concentration" in sy and conc and conc["by"] in ("coin", "regime"):
        key = "skip_coin" if conc["by"] == "coin" else "skip_regime"
        out.append((f"фильтр: без {conc['value']}", variant, {key: conc["value"]}))
    return out


def factory_fixes(spec: dict, sy: list, conc: dict | None) -> list:
    """[(fix name, child spec)] for a whale-stream factory spec (exp_factory grammar)"""
    base = {k: spec.get(k) for k in ("src", "side", "filt", "entry", "stop", "tp", "hold", "size", "sub", "family") if spec.get(k) is not None}
    out = []
    st_ = spec.get("stop")
    if "stop_then_target" in sy and isinstance(st_, (int, float)) and st_ < 30:
        out.append((f"стоп шире: {st_ * 1.5:g} %", {**base, "stop": round(st_ * 1.5, 1)}))
    if "giveback" in sy and not spec.get("tp"):
        out.append(("цель 2R", {**base, "tp": 2}))
    if "flat" in sy and not spec.get("hold"):
        out.append(("выход по времени 24 ч", {**base, "hold": 24}))
    if "concentration" in sy and conc and conc["by"] == "regime" and "reg4h20" not in (spec.get("filt") or []):
        out.append(("фильтр режима BTC 4ч", {**base, "filt": sorted(set(spec.get("filt") or []) | {"reg4h20"})}))
    return out


def z_for(m: int) -> float:
    from statistics import NormalDist
    return round(NormalDist().inv_cdf(1 - 0.025 / max(1, m)), 2)


def _half_stats(xs):
    """xs = [(t, ret, exit_t)] → (mean, n, daily t)"""
    if not xs:
        return None, 0, None
    r = [x[1] for x in xs]
    days = defaultdict(list)
    for x in xs:
        days[x[2] // DAY].append(x[1])
    dm = [st.mean(v) for v in days.values()]
    t_ = (st.mean(dm) / (st.stdev(dm) / math.sqrt(len(dm)))) if len(dm) > 2 and st.stdev(dm) else None
    return round(st.mean(r), 3), len(r), (round(t_, 2) if t_ is not None else None)


def history_trades(Ps: dict, variant: str, extra: dict, regime_of=None) -> list:
    """the lab variant on prepared history {coin: P} → [(t, ret, exit_t)]"""
    from . import levels_lab_desk as LL
    import numpy as np
    out = []
    empty = (np.zeros(0, dtype=np.int64),) + tuple(np.zeros(0) for _ in range(4))
    for coin, P in Ps.items():
        if extra.get("skip_coin") and coin == extra["skip_coin"]:
            continue
        for tr in LL.trades(P, variant, empty, 0):
            if tr["open"]:
                continue
            if extra.get("skip_regime") and regime_of and regime_of(tr["t"]) == extra["skip_regime"]:
                continue
            out.append((tr["t"], tr["pct"], tr["exit_t"]))
    return sorted(out)


def test_fixes(parent_variant: str, fixes: list, Ps: dict, m_total: int, regime_of=None) -> list:
    """→ [{fix, variant, extra, h1, h2, parent_h1, parent_h2, passed, beats}]"""
    par = history_trades(Ps, parent_variant, {}, regime_of)
    if len(par) < 20:
        return []
    mid = par[len(par) // 2][0]
    p1, p2 = _half_stats([x for x in par if x[0] < mid]), _half_stats([x for x in par if x[0] >= mid])
    z = z_for(m_total)
    res = []
    for name, var, extra in fixes:
        xs = history_trades(Ps, var, extra, regime_of)
        a, b = _half_stats([x for x in xs if x[0] < mid]), _half_stats([x for x in xs if x[0] >= mid])
        beats = bool(a[0] is not None and b[0] is not None and p1[0] is not None and p2[0] is not None and a[0] > p1[0] and b[0] > p2[0])
        passed = bool(beats and a[0] > 0 and b[0] > 0 and a[1] >= 30 and b[1] >= 30 and (b[2] or 0) >= z)
        res.append({"fix": name, "variant": var, "extra": extra, "h1": a, "h2": b, "parent_h1": p1, "parent_h2": p2, "beats": beats,
                    "passed": passed, "z": z, "score": round(min(a[0] - p1[0], b[0] - p2[0]), 3) if beats else None})
    return res


# ── 4. children ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def kill_check(children: list, perf_of) -> list:
    """perf_of(child) → (child closes since born [%], parent closes since the child's birth [%]); returns the children killed now"""
    killed = []
    for c in children:
        if not c.get("alive", True):
            continue
        mine, par = perf_of(c)
        if len(mine) >= KILL_N and par and st.mean(mine) < st.mean(par):
            c["alive"] = False
            c["killed_ru"] = f"после {len(mine)} сделок {st.mean(mine):+.2f}% на сделку хуже родителя ({st.mean(par):+.2f}%)"
            killed.append(c)
    return killed


def log_md(day: str, diag: dict, tests: dict, spawned: list, killed: list, old: str = "") -> str:
    """the daily section (newest first) of docs/research/improver_log.md"""
    L = [f"## {day}"]
    if not diag:
        L.append(f"- Столов с ≥ {MIN_FWD} закрытыми сделками вперёд пока нет — диагностировать нечего.")
    for k, d in diag.items():
        sy = ", ".join(d["symptoms"]) or "нет"
        L.append(f"- **{k}**: {d['n']} сделок, средн. {d['mean']:+.2f}%, PF {d['pf']}, стоп→цель {d['stop_then_target']}/{d['stops']}, "
                 f"отдали прибыль {d['giveback']}, ровные {d['flat_share'] * 100:.0f}%, держим {d['hold_med_h']} ч; симптомы: {sy}")
        if k in tests and not tests[k]:
            L.append("  - проверка на истории: мало сделок родителя на истории (< 20) — исправления не проверены, детей нет")
        for r in tests.get(k) or []:
            h1, h2 = r["h1"], r["h2"]
            L.append(f"  - {r['fix']}: история {h1[1]}+{h2[1]} сделок, {h1[0]:+.2f}% / {h2[0]:+.2f}% против родителя "
                     f"{r['parent_h1'][0]:+.2f}% / {r['parent_h2'][0]:+.2f}%, t {h2[2]} (порог {r['z']}) → "
                     + ("прошёл" if r["passed"] else ("лучше родителя, не доказано" if r["beats"] else "не лучше")) if h1[0] is not None and h2[0] is not None
                     else f"  - {r['fix']}: мало сделок на истории")
    for c in spawned:
        L.append(f"- Новый дочерний стол **{c['key']}** ← {c['parent']}: {c['fix']} ({c['test_ru']})")
    for c in killed:
        L.append(f"- Закрыт дочерний стол **{c['key']}**: {c['killed_ru']}")
    head = "# Журнал улучшателя столов (cnq/agents/desk_improver.py)\n\nБумага и исследование; реальный счёт не меняется. Новые дни сверху.\n\n"
    body = old.split("\n", 4)[-1] if old.startswith("# Журнал") else old
    return head + "\n".join(L) + "\n\n" + body.lstrip()
