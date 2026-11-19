"""Score the «Направление рынка» forecast log (19.11). Reads data/market_view/*.jsonl (forecasts written BEFORE the outcome) and resolves
them on BTC hourly closes from the newest snapshot that carries btc_1h (data/market_view/snap/*.json, OKX BTC-USDT 1H).
A forecast is resolved only when the hour close at time+H exists; nothing is re-graded later. Writes docs/research/market_view_score.md.
Rules (fixed 19.11 before any outcome): long/short hit = sign of BTC move matches; neutral hit = |move| < 1.5% (24h) / < 3% (72h).
"""
import glob, json, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BAND = {"24h": 1.5, "72h": 3.0}
HRS = {"24h": 24, "72h": 72}


def closes():
    px = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "data/market_view/snap/*.json"))):
        try:
            for row in json.load(open(p)).get("btc_1h") or []:
                px[int(row[0]) // 3600000] = row[4]  # open-time hour -> close
        except (OSError, ValueError):
            pass
    return px


def main(out):
    px = closes()
    recs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data/market_view/*.jsonl"))):
        recs += [json.loads(l) for l in open(p) if l.strip()]
    rows, stats = [], {h: {"n": 0, "hit": 0, "dir_n": 0, "dir_move": 0.0, "hc_n": 0, "hc_hit": 0} for h in HRS}
    for r in recs:
        p0 = r["prices"]["BTC"]
        for h, hh in HRS.items():
            v = r["view"][h]
            k = (r["ts"] // 3600000) + hh - 1  # close of the hour that ends at ts+H (rounded down)
            if k not in px:
                rows.append((r["time"], h, v["lean"], v["confidence"], None, None)); continue
            mv = (px[k] / p0 - 1) * 100
            hit = abs(mv) < BAND[h] if v["lean"] == "neutral" else (mv > 0) == (v["lean"] == "long")
            s = stats[h]; s["n"] += 1; s["hit"] += hit
            if v["lean"] != "neutral":
                s["dir_n"] += 1; s["dir_move"] += mv if v["lean"] == "long" else -mv
            if v["confidence"] >= 70:
                s["hc_n"] += 1; s["hc_hit"] += hit
            rows.append((r["time"], h, v["lean"], v["confidence"], round(mv, 2), hit))
    L = ["# Счёт попаданий «Направление рынка»", "",
         "Журнал: data/market_view/*.jsonl (оценка пишется до результата). Цена исхода — часовые закрытия BTC-USDT OKX.",
         "Правила заданы 19.11 до первого исхода: лонг/шорт — верно, если знак хода совпал; нейтрально — |ход| < 1.5% за 24 ч, < 3% за 72 ч.",
         f"Обновлено: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Всего оценок: {len(recs)}.", "",
         "| Срок | Разрешено | Верно | Доля | Ср. ход по направлению (лонг/шорт) | Уверенность ≥ 70: верно/всего |", "|---|---|---|---|---|---|"]
    for h, s in stats.items():
        share = f"{s['hit']/s['n']*100:.0f}%" if s["n"] else "—"
        dm = f"{s['dir_move']/s['dir_n']:+.2f}% ({s['dir_n']})" if s["dir_n"] else "—"
        L.append(f"| {h} | {s['n']} | {s['hit']} | {share} | {dm} | {s['hc_hit']}/{s['hc_n']} |")
    L += ["", "Записи соседних запусков (каждые 4 ч) перекрываются по времени и не независимы: до ~30 разрешённых дней выводов нет.", "",
          "| Время оценки | Срок | Склон | Уверенность | Ход BTC, % | Итог |", "|---|---|---|---|---|---|"]
    for t, h, l, c, mv, hit in rows[-60:]:
        L.append(f"| {t} | {h} | {l} | {c} | {'ждёт' if mv is None else mv} | {'ждёт' if hit is None else ('✓' if hit else '✗')} |")
    open(out, "w").write("\n".join(L) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs/research/market_view_score.md"))
