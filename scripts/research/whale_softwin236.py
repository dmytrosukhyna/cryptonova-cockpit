"""П. 236 — мягкая планка доли прибыльных (только исследование, правила не меняются).

Те же данные и копия, что в п. 235 (scripts/research/whale_30d235.py: решения, копия = ход − 0,11%, стоп −16%).
Точки пересмотра каждые 3 дня, 15.10–17.11.2026. В точке T по 60 дням (закрытые решения):
  A  — действующая планка: n ≥ 20, в плюсе ≥ 70%, PF ≥ 2, сумма > 0;
  S  — мягкая группа (задана заранее, один вариант): n ≥ 20, в плюсе 65–70%, PF ≥ 8, сумма > 0.
Вперёд: решения, открытые в (T, T+3д]. Вердикт заранее: S проходит, только если средняя копия
на решение S не хуже A в обеих половинах (15.10–31.10, 01.11–17.11) и S даёт ≥ 30 решений вперёд в каждой.
"""
import json
import math
import statistics as st
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from whale_30d235 import load, stats, DAY  # noqa: E402

H2 = 1793491200000  # 2026-11-01 UTC


def main():
    data = load()
    t, t_end, pts = 1792022400000, 1795132800000, []
    while t + 3 * DAY <= t_end:
        pts.append(t)
        t += 3 * DAY
    rows = []
    for T in pts:
        g = {"A": [], "S": []}
        names = {"A": [], "S": []}
        for tr, decs in data.items():
            n, w, pf, s = stats([d for d in decs if T - 60 * DAY < d["close_t"] <= T])
            if n < 20 or s <= 0:
                continue
            if w >= 0.70 and pf >= 2:
                k = "A"
            elif 0.65 <= w < 0.70 and pf >= 8:
                k = "S"
            else:
                continue
            names[k].append(tr)
            f = [d["copy"] for d in decs if T < d["open_t"] <= T + 3 * DAY]
            if f:
                g[k].append((tr, f))
        r = {"T": T}
        for k in g:
            allc = [c for _, f in g[k] for c in f]
            r[k] = {"traders": len(names[k]), "active": len(g[k]), "dec": len(allc),
                    "mean": st.mean(allc) if allc else None,
                    "win": (sum(c > 0 for c in allc) / len(allc)) if allc else None,
                    "copies": allc, "names": names[k] if k == "S" else None}
        rows.append(r)
    summ = {}
    for h, rr in (("h1", [r for r in rows if r["T"] < H2]), ("h2", [r for r in rows if r["T"] >= H2])):
        s = {}
        for k in ("A", "S"):
            c = [x for r in rr for x in r[k]["copies"]]
            s[k] = {"dec": len(c), "mean": st.mean(c) if c else None,
                    "se": (st.stdev(c) / math.sqrt(len(c))) if len(c) > 2 else None,
                    "win": (sum(x > 0 for x in c) / len(c)) if c else None,
                    "worst": min(c) if c else None}
        if s["A"]["se"] and s["S"]["se"]:
            s["diff"] = s["S"]["mean"] - s["A"]["mean"]
            s["t_welch"] = s["diff"] / math.sqrt(s["A"]["se"] ** 2 + s["S"]["se"] ** 2)
        diffs = [r["S"]["mean"] - r["A"]["mean"] for r in rr if r["S"]["mean"] is not None and r["A"]["mean"] is not None]
        s["points_S_better"] = f"{sum(d >= 0 for d in diffs)}/{len(diffs)}"
        summ[h] = s
    for r in rows:
        for k in ("A", "S"):
            r[k].pop("copies")
    json.dump({"points": rows, "summary": summ}, sys.stdout, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
