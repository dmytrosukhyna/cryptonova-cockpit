"""П. 235 — 30-дневная проверка в дополнение к 60-дневной планке (только исследование).

Данные: data/whale_trades/*.jsonl (закрытые сделки трейдеров Binance/OKX/HL, история с бэкфиллом).
Решение = сделки одного трейдера по одной монете и стороне, закрытые в пределах 60 с.
Копия решения = направление × (выход/вход − 1) − 0,11% за круг, убыток не глубже стопа −16% (−16,11%).
Задержка не моделируется (по п. 228 задержка 2–6 мин копию в среднем не меняет).

Точки пересмотра: каждые 3 дня с 15.10 по 17.11.2026 (как замок). В точке T:
  60 дн.: решения, закрытые в (T−60д, T]: n ≥ 20, доля в плюсе ≥ 70%, PF ≥ 2, сумма > 0;
  30 дн. (правило п. 235, задано заранее): n ≥ 8 и PF ≥ 2 за (T−30д, T].
Вперёд: решения, открытые в (T, T+3д] и уже закрытые. Сравнение «60 да / 30 да» против «60 да / 30 нет».
Вердикт заранее: правило проходит, только если средняя копия «30 да» выше «30 нет» в обеих половинах
(точки 15.10–31.10 и 01.11–17.11).
"""
import glob
import json
import math
import statistics as st
import sys
from collections import defaultdict

DAY = 86400_000
COST = 0.0011
STOP = -0.16


def load(root="data/whale_trades"):
    seen = {}
    for f in sorted(glob.glob(f"{root}/*.jsonl")):
        for line in open(f):
            j = json.loads(line)
            if j.get("kind") != "closed":
                continue
            k = (j["trader"], j["id"])
            if k not in seen and j.get("entry") and j.get("exit") and j.get("notional"):
                seen[k] = j
    by = defaultdict(list)
    for j in seen.values():
        by[j["trader"]].append(j)
    out = {}
    for tr, rows in by.items():
        rows.sort(key=lambda r: (r["coin"], r["side"], r["close_t"]))
        dec = []
        for r in rows:
            d = dec[-1] if dec else None
            if d and d["coin"] == r["coin"] and d["side"] == r["side"] and r["close_t"] - d["close_t"] <= 60_000:
                d["rows"].append(r)
                d["close_t"] = max(d["close_t"], r["close_t"])
                d["open_t"] = min(d["open_t"], r["open_t"])
            else:
                dec.append({"coin": r["coin"], "side": r["side"], "open_t": r["open_t"],
                            "close_t": r["close_t"], "rows": [r]})
        for d in dec:
            n = sum(r["notional"] for r in d["rows"])
            sgn = 1 if d["side"] == "LONG" else -1
            ret = sum(sgn * (r["exit"] / r["entry"] - 1) * r["notional"] for r in d["rows"]) / n
            d["pnl"] = sum(r["pnl"] for r in d["rows"])
            d["copy"] = max(ret, STOP) - COST
            del d["rows"]
        out[tr] = dec
    return out


def stats(decs):
    n = len(decs)
    if not n:
        return n, 0, 0, 0
    win = sum(d["pnl"] > 0 for d in decs) / n
    gp = sum(d["pnl"] for d in decs if d["pnl"] > 0)
    gl = -sum(d["pnl"] for d in decs if d["pnl"] < 0)
    pf = gp / gl if gl > 0 else (math.inf if gp > 0 else 0)
    return n, win, pf, gp - gl


def main():
    data = load()
    t0 = 1792022400000  # 2026-10-15 00:00 UTC
    t_end = 1795132800000  # 2026-11-20 00:00 UTC
    pts = []
    t = t0
    while t + 3 * DAY <= t_end:
        pts.append(t)
        t += 3 * DAY
    res = []
    for T in pts:
        g = {"yes": [], "no": []}
        trs = {"yes": 0, "no": 0}
        for tr, decs in data.items():
            n60, w60, pf60, s60 = stats([d for d in decs if T - 60 * DAY < d["close_t"] <= T])
            if not (n60 >= 20 and w60 >= 0.70 and pf60 >= 2 and s60 > 0):
                continue
            n30, _, pf30, _ = stats([d for d in decs if T - 30 * DAY < d["close_t"] <= T])
            key = "yes" if (n30 >= 8 and pf30 >= 2) else "no"
            fwd = [d["copy"] for d in decs if T < d["open_t"] <= T + 3 * DAY]
            trs[key] += 1
            if fwd:
                g[key].append((tr, fwd))
        row = {"T": T, "traders": trs}
        for k in g:
            allc = [c for _, f in g[k] for c in f]
            per_tr = [st.mean(f) for _, f in g[k]]
            row[k] = {"dec": len(allc), "mean_dec": st.mean(allc) if allc else None,
                      "mean_trader": st.mean(per_tr) if per_tr else None, "tr_active": len(per_tr)}
        res.append(row)
    half = {"h1": [r for r in res if r["T"] < 1793491200000], "h2": [r for r in res if r["T"] >= 1793491200000]}
    summ = {}
    for h, rows in half.items():
        s = {}
        for k in ("yes", "no"):
            vals = [(r[k]["mean_trader"], r[k]["tr_active"]) for r in rows if r[k]["mean_trader"] is not None]
            decs = sum(r[k]["dec"] for r in rows)
            s[k] = {"points": len(vals), "dec": decs,
                    "mean_trader_avg": st.mean(v for v, _ in vals) if vals else None}
        diffs = [r["yes"]["mean_trader"] - r["no"]["mean_trader"] for r in rows
                 if r["yes"]["mean_trader"] is not None and r["no"]["mean_trader"] is not None]
        s["diff_mean"] = st.mean(diffs) if diffs else None
        s["diff_t"] = (st.mean(diffs) / (st.stdev(diffs) / math.sqrt(len(diffs)))) if len(diffs) > 2 and st.stdev(diffs) > 0 else None
        s["diff_pos"] = f"{sum(d > 0 for d in diffs)}/{len(diffs)}"
        summ[h] = s
    out = {"points": res, "summary": summ}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
