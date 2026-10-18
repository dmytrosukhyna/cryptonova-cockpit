"""Merge scripts/lab/results/*.json into web/lab.json for the panel («Тесты стратегий» → «Лаборатория»)."""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
FAM_RU = {"fade": "Против движения и разворот", "trend": "Тренд и пробои", "deriv": "Деривативы: фандинг, ОИ, ликвидации",
          "scalp": "Скальпинг и сессии", "manip": "Манипуляции: сбор стопов и ловушки"}


def fam_ru(fam):
    base, _, wide = fam.partition("_wide")
    return FAM_RU.get(base, base) + (f" · топ-{wide} монет" if wide else " · 18 монет")


def slim(st):
    keep = ("all", "train", "test", "years", "pos_years", "years_n", "max_dd_sum_pct", "top_coin_share", "per_day", "median_hold_h", "data",
            "test_day_avg", "test_days", "test_ex3_avg", "pf5")
    return {k: st.get(k) for k in keep if k in st}


def main():
    fams, items, tried = [], [], 0
    for p in sorted(glob.glob(os.path.join(HERE, "results", "*.json"))):
        if os.path.basename(p).startswith("_"):
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        fam = d.get("family") or os.path.basename(p)[:-5]
        fams.append({"key": fam, "name_ru": fam_ru(fam), "universe": d.get("universe", "core18"), "tried": d.get("tried"), "notes_ru": d.get("notes_ru")})
        tried += d.get("tried") or 0
        for r in d.get("results") or []:
            st = r.get("stats") or {}
            from harness import passes as _passes        # the current pass rule (a family may have run with an older one)
            r["passes"] = _passes(st)
            items.append({"family": fam, "family_ru": fam_ru(fam), "universe": d.get("universe", "core18"), "id": r.get("id"), "name_ru": r.get("name_ru"),
                          "rule_ru": r.get("rule_ru"), "exit": r.get("exit"), "params": r.get("params"), "passes": bool(r.get("passes")),
                          "stats": slim(st)})
    items.sort(key=lambda x: (not x["passes"], -((x["stats"].get("test") or {}).get("avg_pct") or -9)))
    out = {"asof": int(time.time() * 1000), "families": fams, "tried": tried, "n": len(items), "passed": sum(1 for x in items if x["passes"]),
           "items": items,
           "method_ru": ["Каждая идея проверена одинаково: 15-минутные свечи (данные Binance), вход на открытии следующей свечи, "
                         "комиссии 0.14% за сделку, если стоп и цель в одной свече — считаем стоп.",
                         "Два набора монет. «18 монет» — крупные монеты, история 2021–2026 с ликвидациями и фандингом. «Топ-50» — все фьючерсы "
                         "Binance с 2023 года, включая удалённые; монета торгуется, только пока она в топ-50 по объёму за 30 дней на тот момент "
                         "(без подглядывания в будущее и без «выживших»).",
                         "Параметры подбирались только на годах до 2025; оценка — на 2025–2026, которые при подборе не использовались.",
                         "Стратегия «прошла», если на 2025–2026 в среднем больше +0.05% на сделку после комиссий, сделок не меньше 60, "
                         "плюсовых лет большинство (4 из 6 или 3 из 4), ни одна монета не дала больше половины прибыли, "
                         "Главное — последние два года: счёт на 5 позиций одновременно должен быть в плюсе и в 2025, и в 2026 году отдельно, "
                         "и оставаться в плюсе на 2025–2026 без своих 3 лучших дней (чтобы прибыль не держалась на паре удачных дней).",
                         "Чем больше вариантов перепробовано, тем выше шанс случайной удачи — поэтому число попыток указано."]}
    path = os.path.join(HERE, "..", "..", "web", "lab.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, default=float)
    print(f"web/lab.json: {len(items)} strategies, passed {out['passed']}, tried {tried}")


if __name__ == "__main__":
    main()
