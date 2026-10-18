"""Merge scripts/lab/results/*.json into web/lab.json for the panel («Тесты стратегий» → «Лаборатория»)."""
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAM_RU = {"fade": "Против движения и разворот", "trend": "Тренд и пробои", "deriv": "Деривативы: фандинг, ОИ, ликвидации",
          "scalp": "Скальпинг и сессии"}


def slim(st):
    keep = ("all", "train", "test", "years", "pos_years", "years_n", "max_dd_sum_pct", "top_coin_share", "per_day", "median_hold_h", "data")
    return {k: st.get(k) for k in keep if k in st}


def main():
    fams, items, tried = [], [], 0
    for p in sorted(glob.glob(os.path.join(HERE, "results", "*.json"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        fam = d.get("family") or os.path.basename(p)[:-5]
        fams.append({"key": fam, "name_ru": FAM_RU.get(fam, fam), "tried": d.get("tried"), "notes_ru": d.get("notes_ru")})
        tried += d.get("tried") or 0
        for r in d.get("results") or []:
            st = r.get("stats") or {}
            items.append({"family": fam, "family_ru": FAM_RU.get(fam, fam), "id": r.get("id"), "name_ru": r.get("name_ru"),
                          "rule_ru": r.get("rule_ru"), "exit": r.get("exit"), "params": r.get("params"), "passes": bool(r.get("passes")),
                          "stats": slim(st)})
    items.sort(key=lambda x: (not x["passes"], -((x["stats"].get("test") or {}).get("avg_pct") or -9)))
    out = {"asof": int(time.time() * 1000), "families": fams, "tried": tried, "n": len(items), "passed": sum(1 for x in items if x["passes"]),
           "items": items,
           "method_ru": ["Каждая идея проверена одинаково: 18 монет, 15-минутные свечи 2020–2026 (данные Binance), вход на открытии следующей свечи, "
                         "комиссии 0.14% за сделку (лимитные ордера — 0.04%), если стоп и цель в одной свече — считаем стоп.",
                         "Параметры подбирались только на 2021–2024; оценка — на 2025–2026, которые при подборе не использовались.",
                         "Стратегия «прошла», если на 2025–2026 в среднем больше +0.05% на сделку после комиссий, сделок не меньше 60, "
                         "плюсовых лет не меньше 4 из 6 и ни одна монета не дала больше половины прибыли.",
                         "Чем больше вариантов перепробовано, тем выше шанс случайной удачи — поэтому число попыток указано."]}
    path = os.path.join(HERE, "..", "..", "web", "lab.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, default=float)
    print(f"web/lab.json: {len(items)} strategies, passed {out['passed']}, tried {tried}")


if __name__ == "__main__":
    main()
