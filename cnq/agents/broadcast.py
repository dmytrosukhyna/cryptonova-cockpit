"""Broadcast department — the Friday live show («эфир», 10–15 minutes) prepared from the panel's own JSON.

Every Friday (Kyiv time) the deck is rebuilt on each server run, so it is fresh at airtime; it is also rebuilt when
web/broadcast.json is missing or older than 7 days. On other days only the small `live` block (current prices, update
time) is refreshed, so the deck is never silently stale between Fridays.

web/broadcast.json = {asof, generated, week_of, period_ru, generated_reason, duration_min_est, slides:[...], live:{...}}
slide = {id, title, show, bullets[], say_ru, facts{}, tip_ru}; `show` is the id of the panel element the panel puts
on the slide (SHOW_IDS). bullets = short factual lines with numbers, say_ru = what the presenter says (plain spoken
Russian, numbers as digits), tip_ru = what to point at on screen. Texts are templates over the facts — no LLM, no numbers
that are not in the data, no promises. A slide whose data is missing is skipped.
Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import time

from .outlook import DAY_MS, KYIV, fmt_price, kyiv, kyiv_str, pct, plural

SHOW_IDS = ("chartCard", "kpi", "globeCard", "coreCard", "macroCard", "calCard", "fcCard", "stratsCard", "fdeskBox", "apRadar",
            "ideasCard", "memesCard", "earnBar", "newsCard")
FILES = ("terminal", "markets", "decision", "macro", "calendar", "paper", "portfolio", "ideas", "memes", "arb", "tri", "funding",
         "calibration", "outlook")
CORE = ("BTC", "ETH", "SOL")
GEN = {"BTC": "биткоина", "ETH": "эфира", "SOL": "соланы"}
WEEK_MS = 7 * DAY_MS
WORDS_PER_MIN = 130          # calm live speech
SHOW_MIN_PER_SLIDE = 0.3     # time to switch and point at the card
DISCLAIMER = "Не финансовая рекомендация. Все счета — демо, деньги виртуальные."


# ------------------------------------------------------------------ small helpers
def _load(d: str, name: str):
    try:
        return json.load(open(os.path.join(d, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def say_pct(x: float | None, d: int = 1) -> str:
    """Spoken percent: «плюс 1.8%», «минус 0.4%», «0%»."""
    if x is None:
        return "—"
    v = round(x * 100, d)
    if v == 0:
        return "0%"
    return ("плюс " if v > 0 else "минус ") + f"{abs(v):.{d}f}%"


def _money(v: float) -> str:
    """|v| as text: cents below $20 (2.35, 12.50), whole dollars above (113, 10 000)."""
    a = abs(v)
    return f"{a:.2f}" if 0.005 <= a < 20 and abs(a - round(a)) >= 0.005 else f"{math.floor(a + 0.5):,}".replace(",", " ")


def usd(v: float | None, sign: bool = True) -> str:
    if v is None:
        return "—"
    m = _money(v)
    if m in ("0", "0.00"):
        return "$0"
    if not sign:
        return ("−" if v < 0 else "") + "$" + m
    return ("+" if v > 0 else "−") + "$" + m


def say_usd(v: float | None, sign: bool = True) -> str:
    if v is None:
        return "—"
    m = _money(v)
    if m in ("0", "0.00"):
        return "0 долларов"
    w = "доллара" if "." in m else plural(int(m.replace(" ", "")), "доллар", "доллара", "долларов")
    return (("плюс " if v > 0 else "минус ") if sign else "") + m + " " + w


WD_SAY = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]


def say_when(ms: int) -> str:
    """«в среду 21.10 в 00:00» (Kyiv time) — for the spoken text."""
    t = kyiv(ms)
    return f"{WD_SAY[t.weekday()]} {t.strftime('%d.%m')} в {t.strftime('%H:%M')}"


def speak_name(name: str | None) -> str:
    """Panel names like «Эксперимент · свинг» read badly aloud — keep the last part."""
    n = str(name or "")
    return n.split(" · ")[-1].capitalize() if " · " in n and n.startswith(("Эксперимент", "Автопилот")) else n.split(" · ")[0]


def big_usd(v: float | None) -> tuple[str, str]:
    """(«2.75 трлн $», «2.75 триллиона долларов»)."""
    if not v:
        return "—", "—"
    if v >= 1e12:
        return f"{v / 1e12:.2f} трлн $", f"{v / 1e12:.2f} триллиона долларов"
    if v >= 1e9:
        return f"{v / 1e9:.1f} млрд $", f"{v / 1e9:.1f} миллиарда долларов"
    return f"{v / 1e6:.1f} млн $", f"{v / 1e6:.1f} миллиона долларов"


def _sent(xs: list[str], cap: int = 6) -> str:
    return " ".join(x.strip() for x in [x for x in xs if x and x.strip()][:cap])


def first_sentence(s: str | None) -> str:
    if not s:
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", s.strip())
    return (m.group(1) if m else s.strip())


def latin_heavy(s: str | None) -> bool:
    try:
        from .channel import latin_heavy as lh
        return lh(s)
    except Exception:  # noqa: BLE001
        lat = sum(len(w) for w in re.findall(r"\b[A-Za-z][a-z]{2,}\b", s or ""))
        cyr = len(re.findall(r"[А-Яа-яЁё]", s or ""))
        return lat > 12 and lat > 0.25 * (lat + cyr)


def is_ru(s: str | None) -> bool:
    return bool(s) and bool(re.search(r"[А-Яа-яЁё]", s)) and not latin_heavy(s)


def coin(term: dict | None, base: str) -> dict | None:
    return next((c for c in (term or {}).get("coins") or [] if str(c.get("symbol", "")).replace("-USDT", "") == base), None)


def slide(id_: str, title: str, show: str, bullets: list[str], say: list[str], facts: dict, tip: str) -> dict:
    assert show in SHOW_IDS, show
    return {"id": id_, "title": title, "show": show, "bullets": [b for b in bullets if b][:5], "say_ru": _sent(say), "facts": facts, "tip_ru": tip}


# ------------------------------------------------------------------ indicators the panel computes in the browser
def rsi(closes: list[float], n: int = 14) -> list[float]:
    """Port of the panel's rsi() (web/app.html): simple average for the first n changes, then Wilder smoothing."""
    out, g, l = [], 0.0, 0.0
    for i, c in enumerate(closes):
        if i == 0:
            out.append(50.0); continue
        d = c - closes[i - 1]
        up, dn = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            g += up / n; l += dn / n
        else:
            g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n
        out.append(100.0 if l == 0 else 100 - 100 / (1 + g / l))
    return out


def resample_closes(candles: list[list], ms: int) -> list[float]:
    """Close of every `ms` bucket (UTC-aligned), the forming bucket included — like the exchange candles the panel draws."""
    out: dict[int, float] = {}
    for k in candles or []:
        out[k[0] // ms] = k[4]
    return [out[b] for b in sorted(out)]


def radar_rows(scan: dict) -> list[dict]:
    """Port of the panel's «Ждём вход» scoring: how close each coin is to an autopilot entry (1.0 = condition met)."""
    oi_need = abs(scan.get("f_need_oi") or 0.12); px_need = abs(scan.get("f_need_px") or 0.05)
    b_sig = abs(scan.get("b_need_sigma") or 3.5); d_need = abs(scan.get("d_need_basis") or 0.002)
    out = []
    for r in scan.get("rows") or []:
        ret, bs = r.get("ret_last"), r.get("b_sigma")
        need_b = -b_sig * abs(ret / bs) if bs and ret is not None else None
        fb = max(0.0, -ret) / abs(need_b) if need_b else 0.0
        ff = min(max(0.0, -r["oi4"]) / oi_need, max(0.0, -r["px4"]) / px_need) if r.get("oi4") is not None and r.get("px4") is not None else 0.0
        dn = 0.0025 if r.get("b_only") else d_need
        fd = max(0.0, -r["basis"]) / dn if r.get("basis") is not None else 0.0
        parts = [(fb, "пролив за 15 минут", f"{pct(ret, 2)}, нужно {pct(need_b, 2)}" if need_b else ""),
                 (ff, "сброс позиций за 4 часа", f"ставки {pct(r.get('oi4'))} (нужно −{oi_need * 100:.0f}%), цена {pct(r.get('px4'))} (нужно −{px_need * 100:.0f}%)"),
                 (fd, "дисконт фьючерса", f"{-(r.get('basis') or 0) * 100:.2f}%, нужно {dn * 100:.2f}%")]
        best = max(parts, key=lambda x: x[0])
        out.append({"coin": r.get("coin"), "b_only": bool(r.get("b_only")), "score": round(max(fb, ff, fd), 3),
                    "condition_ru": best[1], "detail_ru": best[2]})
    out.sort(key=lambda x: -x["score"])
    return out


# ------------------------------------------------------------------ data the panel files do not hold for a whole week
def week_news(conn: sqlite3.Connection | None, decision: dict | None, now_ms: int, n: int = 3) -> tuple[list[dict], dict | None]:
    """Top-n Russian-titled news of the last 7 days by magnitude (then confidence), one row per story; + measured hit rate."""
    rows = []
    since = now_ms - WEEK_MS
    if conn is not None:
        try:
            q = conn.execute("SELECT i.news_id, i.asset, i.category, i.direction, i.horizon, i.magnitude, i.confidence, i.reasoning, i.created, "
                             "i.title_ru, i.realized_ret, i.scored FROM news_impact i WHERE i.created >= ? AND i.created <= ?", (since, now_ms)).fetchall()
            rows = [dict(zip(("news_id", "asset", "category", "direction", "horizon", "magnitude", "confidence", "reasoning", "created", "title",
                              "realized_ret", "scored"), r)) for r in q]
        except Exception:  # noqa: BLE001
            rows = []
    if not rows and decision:
        rows = [i for i in (decision.get("news") or {}).get("impacts") or [] if (i.get("created") or 0) >= since]
    best: dict[str, dict] = {}
    for i in rows:
        t = (i.get("title") or "").strip()
        if (i.get("magnitude") or 0) < 3 or i.get("category") in (None, "other") or not is_ru(t) or str(i.get("reasoning") or "").startswith("mock"):
            continue
        key = str(i.get("news_id") or t.lower())
        cur = best.get(key)
        rank = (i.get("magnitude") or 0, 1 if i.get("asset") in ("MARKET", "BTC") else 0, i.get("confidence") or 0)
        if cur is None or rank > cur["_rank"]:
            best[key] = dict(i, _rank=rank)
    seen_t = set(); out = []
    for i in sorted(best.values(), key=lambda i: (-(i.get("magnitude") or 0), -(i.get("confidence") or 0), -(i.get("created") or 0))):
        tl = i["title"].strip().lower()
        if tl in seen_t:
            continue
        seen_t.add(tl)
        out.append({k: i.get(k) for k in ("news_id", "asset", "category", "direction", "horizon", "magnitude", "confidence", "created", "title",
                                          "realized_ret", "scored")})
        if len(out) >= n:
            break
    hit = None
    if conn is not None:
        try:
            sc = conn.execute("SELECT direction, realized_ret FROM news_impact WHERE scored=1 AND direction IN ('bull','bear') AND realized_ret IS NOT NULL").fetchall()
            if len(sc) >= 20:
                h = sum(1 for d, r in sc if (r > 0) == (d == "bull"))
                hit = {"n": len(sc), "hit_rate": round(h / len(sc), 3)}
        except Exception:  # noqa: BLE001
            hit = None
    return out, hit


# ------------------------------------------------------------------ slides
def s_week(ctx: dict, now_ms: int) -> dict | None:
    term, mk = ctx.get("terminal"), ctx.get("markets") or {}
    rows = []
    for s in CORE:
        c = coin(term, s)
        if c and c.get("price") and c.get("chg7") is not None:
            rows.append({"symbol": s, "price": c["price"], "chg7": c["chg7"], "chg24": c.get("chg24")})
    g = mk.get("global") or {}
    fng = mk.get("fng") or {}
    if not rows:
        return None
    facts = {"coins": rows, "total_mcap": g.get("total_mcap"), "mcap_chg24_pct": g.get("mcap_chg24"), "btc_dom_pct": g.get("btc_dom"),
             "fng": {k: fng.get(k) for k in ("value", "label", "week")} if fng.get("value") is not None else None}
    B = [f"{r['symbol']} {fmt_price(r['price'])} $ — за неделю {pct(r['chg7'])}" for r in rows]
    S = ["Ну что, начинаем с итогов недели."]
    S.append("За 7 дней " + ", ".join(f"у {GEN[r['symbol']]} {say_pct(r['chg7'])}" for r in rows) + ".")
    btc = next((r for r in rows if r["symbol"] == "BTC"), None)
    if btc:
        S.append(f"Биткоин сейчас стоит {fmt_price(btc['price'])} долларов.")
    if g.get("total_mcap"):
        b, s = big_usd(g["total_mcap"])
        B.append(f"Весь рынок {b} (за сутки {pct((g.get('mcap_chg24') or 0) / 100)}), доля BTC {g.get('btc_dom', 0):.1f}%")
        S.append(f"Весь крипторынок стоит {s}, доля биткоина {g.get('btc_dom', 0):.1f}%.")
    if facts["fng"]:
        wk = f" (неделю назад {fng['week']})" if fng.get("week") is not None else ""
        B.append(f"Страх и жадность: {fng['value']} — {fng.get('label', '')}{wk}")
        S.append(f"Индекс страха и жадности {fng['value']} — это {fng.get('label', '')}" + (f", неделю назад было {fng['week']}." if fng.get("week") is not None else "."))
    return slide("week", "Итоги недели", "kpi", B, S, facts,
                 "Покажи полосу вверху: цены BTC, ETH, SOL, капитализацию рынка и долю BTC.")


def s_news(ctx: dict, now_ms: int) -> dict | None:
    items, hit = week_news(ctx.get("conn"), ctx.get("decision"), now_ms)
    if not items:
        return None
    hz = {"hours": "через 6 часов", "days": "через 2 дня", "weeks": "через 10 дней"}
    B, S = [], ["Что двигало рынок. Наш отдел новостей выбрал главные новости недели по силе влияния."]
    for k, i in enumerate(items):
        arrow = "▲" if i["direction"] == "bull" else "▼" if i["direction"] == "bear" else "•"
        who = "рынок" if i["asset"] == "MARKET" else i["asset"]
        res = ""
        if i.get("scored") and i.get("realized_ret") is not None:
            base = "BTC" if i["asset"] == "MARKET" else i["asset"]
            res = f"; {hz.get(i.get('horizon'), 'позже')} {base} {pct(i['realized_ret'])}"
            i["result_ru"] = res[2:]
        B.append(f"{kyiv(i['created']).strftime('%d.%m')} · {who} {arrow} {i['title']} (сила {i['magnitude']}/5){res}")
        tone = "позитив" if i["direction"] == "bull" else "негатив" if i["direction"] == "bear" else "нейтрально"
        S.append(f"{['Первая', 'Вторая', 'Третья'][k]}: {i['title']} — для {'всего рынка' if who == 'рынок' else who} это {tone}.")
    if hit:
        B.append(f"Направление по новостям сбывалось в {round(hit['hit_rate'] * 100)}% случаев (проверок — {hit['n']})")
        S.append(f"И честно: по направлению такие оценки сбывались в {round(hit['hit_rate'] * 100)}% случаев из {hit['n']}, поэтому новости — это фон, а не сигнал.")
    return slide("news", "Что двигало рынок", "newsCard", B, S, {"news": items, "news_hit": hit},
                 "Открой карточку «Новости» и найди эти новости в списке; покажи стрелку — позитив или негатив.")


def _macro_line(i: dict | None, field: str = "chg_5d") -> str | None:
    if not i or i.get(field) is None:
        return None
    v = i[field]
    return (f"{v:+.1f}%" if i.get("chg_unit") == "%" else f"{v:+.1f} бп").replace("-", "−")


def _macro_say(i: dict | None, field: str = "chg_5d") -> str | None:
    if not i or i.get(field) is None:
        return None
    v = i[field]
    if i.get("chg_unit") == "%":
        return say_pct(v / 100)
    return ("плюс " if v > 0 else "минус " if v < 0 else "") + f"{abs(v):.1f} базисных пункта"


def s_macro(ctx: dict, now_ms: int) -> dict | None:
    m = ctx.get("macro")
    if not m:
        return None
    it = {i["key"]: i for i in m.get("items") or [] if i.get("value") is not None}
    if not it:
        return None
    ndx, spx, vix, dxy, y10, wti, gold = (it.get(k) for k in ("NDX_FUT", "SPX_FUT", "VIX", "DXY", "US10Y", "WTI", "GOLD"))
    asof = max((i.get("asof") or "") for i in it.values())
    when = f" (данные на {asof[8:10]}.{asof[5:7]})" if len(asof) >= 10 else ""
    B, S = [], [f"Теперь макро — что было на внешних рынках за 5 торговых дней{when}."]
    if ndx or spx:
        B.append("Акции США за 5 дней: " + ", ".join(x for x in (f"Nasdaq-100 {_macro_line(ndx)}" if ndx else "", f"S&P 500 {_macro_line(spx)}" if spx else "") if x))
        S.append("Акции США: " + ", ".join(x for x in (f"Nasdaq за 5 дней {_macro_say(ndx)}" if ndx else "", f"S&P 500 {_macro_say(spx)}" if spx else "") if x) + ".")
    if vix:
        B.append(f"VIX {vix['value']} — за 5 дней {_macro_line(vix)}")
        S.append(f"Индекс страха на акциях VIX сейчас {vix['value']}, за 5 дней {_macro_say(vix)}.")
    if dxy or y10:
        B.append("; ".join(x for x in (f"Доллар (DXY) {_macro_line(dxy)}" if dxy else "", f"доходность 10-летних {_macro_line(y10)} ({y10['value']}%)" if y10 else "") if x))
    if wti or gold:
        B.append(", ".join(x for x in (f"Нефть WTI {_macro_line(wti)} ({wti['value']} $)" if wti else "", f"золото {_macro_line(gold)}" if gold else "") if x))
        if wti:
            S.append(f"Нефть WTI за 5 дней {_macro_say(wti)}, сейчас {wti['value']} доллара.")
    notes = m.get("notes") or []
    if notes:
        S.append("Что из этого реально важно для биткоина. " + first_sentence(notes[0]))
        if len(notes) > 2:
            S.append(first_sentence(notes[2]))
    sig = [i for i in it.values() if i.get("signal_ru")]
    facts = {"items": {k: {kk: v.get(kk) for kk in ("name_ru", "value", "chg_1d", "chg_5d", "chg_unit", "asof")} for k, v in it.items()},
             "notes": notes[:3], "signals": [{"key": i["key"], "signal_ru": i["signal_ru"]} for i in sig],
             "asof": max((i.get("asof") or "") for i in it.values())}
    return slide("macro", "Макро: внешние рынки", "macroCard", B, S, facts,
                 "Открой «Макро-панель»: покажи строки Nasdaq-100 и VIX" +
                 (" — у них сейчас подсказка, что такой день значил для биткоина раньше." if sig else " и колонку «за 5 дней»."))


def s_calendar(ctx: dict, now_ms: int) -> dict | None:
    cal = ctx.get("calendar")
    if not cal:
        return None
    evs = sorted([e for e in cal.get("events") or [] if e.get("time") and now_ms < e["time"] <= now_ms + WEEK_MS], key=lambda e: e["time"])
    if len(evs) > 5:
        keep = sorted(evs, key=lambda e: (-(e.get("importance") or 0), e["time"]))[:5]
        evs = sorted(keep, key=lambda e: e["time"])
    nxt = next((e for e in sorted(cal.get("events") or [], key=lambda e: e.get("time") or 0) if (e.get("time") or 0) > now_ms + WEEK_MS
                and (e.get("importance") or 0) >= 2), None)
    B, S = [], ["Календарь на следующую неделю, время по Киеву."]
    for e in evs:
        B.append(f"{kyiv_str(e['time'])} — {e.get('name')} (важность {e.get('importance', 1)}/3)")
        S.append(f"{say_when(e['time']).capitalize()} — {e.get('name')}. {first_sentence(e.get('what'))}")
    if not evs:
        B.append("Плановых событий на 7 дней нет")
        S.append("Плановых событий на ближайшие 7 дней в календаре нет.")
    if nxt and len(B) < 5:
        B.append(f"Дальше: {kyiv_str(nxt['time'])} — {nxt.get('name')}")
        if not any((e.get("importance") or 0) >= 2 for e in evs):
            S.append(f"Ближайшее важное событие — {nxt.get('name')}, {say_when(nxt['time'])}.")
    if cal.get("verdict"):
        S.append("И что мы проверили на истории: " + first_sentence(cal["verdict"]).replace("Проверено на 2023–2026: ", "в 2023–2026 "))
    facts = {"events": [{k: e.get(k) for k in ("time", "type", "name", "importance", "what")} for e in evs],
             "next_important": {k: nxt.get(k) for k in ("time", "name")} if nxt else None, "verdict": cal.get("verdict")}
    return slide("calendar", "Календарь следующей недели", "calCard", B, S[:6], facts,
                 "Открой «Календарь событий»: покажи события ближайших 7 дней и как биткоин реагировал на них раньше.")


def s_breadth(ctx: dict, now_ms: int) -> dict | None:
    term = ctx.get("terminal")
    cs = [c for c in (term or {}).get("coins") or [] if c.get("chg24") is not None and c.get("chg7") is not None]
    if len(cs) < 5:
        return None
    n = len(cs)
    up24 = sum(1 for c in cs if c["chg24"] > 0); up7 = sum(1 for c in cs if c["chg7"] > 0)
    srt = sorted(cs, key=lambda c: -c["chg7"])
    nm = lambda c: str(c["symbol"]).replace("-USDT", "")  # noqa: E731
    best = [{"symbol": nm(c), "chg7": c["chg7"]} for c in srt[:3]]
    worst = [{"symbol": nm(c), "chg7": c["chg7"]} for c in srt[-3:][::-1]]
    scan = (ctx.get("paper") or {}).get("scan") or {}
    B = [f"За сутки растут {up24} из {n}, падают {n - up24}",
         f"За неделю в плюсе {up7} из {n}",
         "Лучшие за неделю: " + ", ".join(f"{b['symbol']} {pct(b['chg7'])}" for b in best),
         "Худшие за неделю: " + ", ".join(f"{w['symbol']} {pct(w['chg7'])}" for w in worst)]
    S = [f"Сфера рынка — это {n} основных монет. За сутки растут {up24}, падают {n - up24}.",
         f"За неделю в плюсе {up7} из {n}.",
         "Лучше всех за неделю " + ", ".join(f"{b['symbol']} — {say_pct(b['chg7'])}" for b in best) + ".",
         "Хуже всех " + ", ".join(f"{w['symbol']} — {say_pct(w['chg7'])}" for w in worst) + "."]
    if scan.get("breadth") is not None:
        B.append(f"Упали больше чем на 3% за сутки: {round(scan['breadth'] * 100)}% монет (паника для автопилота — от {round((scan.get('need_breadth') or 0.4) * 100)}%)")
        S.append(f"Больше чем на 3% за сутки упали {round(scan['breadth'] * 100)}% монет — до паники, на которой покупает автопилот, далеко: нужно {round((scan.get('need_breadth') or 0.4) * 100)}%."
                 if scan["breadth"] < (scan.get("need_breadth") or 0.4) else
                 f"Больше чем на 3% за сутки упали {round(scan['breadth'] * 100)}% монет — это уровень паники, на котором автопилот покупает.")
    facts = {"n": n, "up24": up24, "up7": up7, "best7": best, "worst7": worst, "breadth": scan.get("breadth"), "need_breadth": scan.get("need_breadth")}
    return slide("breadth", "Сфера рынка", "globeCard", B, S, facts,
                 "Покрути сферу: зелёные точки растут, красные падают, размер — объём. Наведи на крупную точку — покажи название.")


def s_rsi(ctx: dict, now_ms: int) -> dict | None:
    term = ctx.get("terminal")
    rows = []
    for c in (term or {}).get("coins") or []:
        c4 = resample_closes(c.get("candles") or [], 4 * 3_600_000)
        if len(c4) < 30:
            continue
        r4 = rsi(c4)[-1]
        d1 = [k[4] for k in c.get("candles_1d") or []]
        r1 = rsi(d1)[-1] if len(d1) >= 30 else None
        rows.append({"symbol": str(c["symbol"]).replace("-USDT", ""), "rsi4h": round(r4, 1), "rsi1d": round(r1, 1) if r1 is not None else None})
    if len(rows) < 5:
        return None
    os_ = sorted([r for r in rows if r["rsi4h"] <= 35], key=lambda r: r["rsi4h"])
    ob = sorted([r for r in rows if r["rsi4h"] >= 65], key=lambda r: -r["rsi4h"])
    os_d = sorted([r for r in rows if r["rsi1d"] is not None and r["rsi1d"] <= 35], key=lambda r: r["rsi1d"])
    ob_d = sorted([r for r in rows if r["rsi1d"] is not None and r["rsi1d"] >= 65], key=lambda r: -r["rsi1d"])
    med = sorted(r["rsi4h"] for r in rows)[len(rows) // 2]
    lst = lambda xs, k: ", ".join(f"{r['symbol']} {round(r[k])}" for r in xs[:6]) or "нет"  # noqa: E731
    B = [f"Перепроданы, RSI 4ч ≤ 35: {lst(os_, 'rsi4h')}", f"Перекуплены, RSI 4ч ≥ 65: {lst(ob, 'rsi4h')}",
         f"Дневной RSI: ≤ 35 — {lst(os_d, 'rsi1d')}; ≥ 65 — {lst(ob_d, 'rsi1d')}", f"Средний по {len(rows)} монетам RSI 4ч: {round(med)}"]
    S = ["Карта перекупленности. RSI показывает, перегрета монета или перепродана: ниже 30 — дёшево, выше 70 — перегрето.",
         f"По 4-часовым свечам у ядра, то есть перепроданы: {lst(os_, 'rsi4h')}." if os_ else "По 4-часовым свечам перепроданных монет у ядра сейчас нет.",
         f"На внешней орбите, перекуплены: {lst(ob, 'rsi4h')}." if ob else "Перекупленных на внешней орбите нет.",
         f"Средний RSI по {len(rows)} монетам — {round(med)}, то есть рынок в середине."
         if 40 <= med <= 60 else f"Средний RSI по {len(rows)} монетам — {round(med)}."]
    facts = {"rows": rows, "oversold4h": [r["symbol"] for r in os_], "overbought4h": [r["symbol"] for r in ob], "median4h": med,
             "note_ru": "RSI 14 по 4-часовым и дневным свечам сервера на момент сборки; в браузере цифры могут немного отличаться (живая свеча)."}
    return slide("rsi", "Карта перекупленности", "coreCard", B, S, facts,
                 "Нажми на карту — разверни на весь экран, покажи монеты у ядра (перепроданы) и на внешней орбите (перекуплены). "
                 "Переключатель «4ч / день / нед» — сверху справа.")


def _levels(c: dict) -> tuple[dict | None, dict | None]:
    p = c["price"]
    lv = [l for l in c.get("levels") or [] if l.get("status") in (None, "ACTIVE")]
    sup = sorted([l for l in lv if l["hi"] <= p * 1.001 or (l["kind"] == "support" and l["center"] < p)], key=lambda l: -l["center"])
    res = sorted([l for l in lv if l["lo"] >= p * 0.999 or (l["kind"] == "resistance" and l["center"] > p)], key=lambda l: l["center"])
    return (sup[0] if sup else None), (res[0] if res else None)


def s_btc(ctx: dict, now_ms: int) -> dict | None:
    c = coin(ctx.get("terminal"), "BTC")
    if not c or not c.get("price"):
        return None
    p = c["price"]; reg = c.get("regime") or {}
    wk = [k for k in c.get("candles") or [] if k[0] >= now_ms - WEEK_MS] or (c.get("candles") or [])[-96 * 7:]
    lo7 = min(k[3] for k in wk) if wk else None; hi7 = max(k[2] for k in wk) if wk else None
    sup, res = _levels(c)
    B = [f"BTC {fmt_price(p)} $ · за 7 дней от {fmt_price(lo7)} до {fmt_price(hi7)}" if wk else f"BTC {fmt_price(p)} $"]
    S = [f"Теперь график биткоина. За неделю цена ходила от {fmt_price(lo7)} до {fmt_price(hi7)}, сейчас {fmt_price(p)}." if wk else
         f"Теперь график биткоина: сейчас {fmt_price(p)}."]
    if sup:
        B.append(f"Поддержка {fmt_price(sup['lo'])}–{fmt_price(sup['hi'])} ({sup['touches']} {plural(sup['touches'], 'касание', 'касания', 'касаний')}, {pct(sup['dist_pct'] / 100)})")
    if res:
        B.append(f"Сопротивление {fmt_price(res['lo'])}–{fmt_price(res['hi'])} ({res['touches']} {plural(res['touches'], 'касание', 'касания', 'касаний')}, {pct(res['dist_pct'] / 100)})")
    if sup and res:
        S.append(f"Ближняя поддержка снизу — {fmt_price(sup['lo'])}–{fmt_price(sup['hi'])}, сопротивление сверху — {fmt_price(res['lo'])}–{fmt_price(res['hi'])}.")
    elif sup or res:
        z = sup or res
        S.append(f"Ближняя зона {'поддержки' if sup else 'сопротивления'} — {fmt_price(z['lo'])}–{fmt_price(z['hi'])}.")
    if reg.get("sma200"):
        d = p / reg["sma200"] - 1
        lab = "бычий" if reg.get("long") == "BULL" else "медвежий" if reg.get("long") == "BEAR" else "смешанный"
        B.append(f"{'Выше' if d > 0 else 'Ниже'} 200-дневной средней {fmt_price(reg['sma200'])} на {pct(abs(d), 1, False)} — режим {lab}")
        S.append(f"Цена {'выше' if d > 0 else 'ниже'} 200-дневной средней на {pct(abs(d), 1, False)} — режим {lab}.")
    if reg.get("mid_note"):
        trig = f"; триггер — закрытие дня выше {fmt_price(reg['hh20'])}" if reg.get("mid") in ("UP_NO_BREAKOUT",) and reg.get("hh20") else ""
        B.append(f"Тренд 28 дней: {reg['mid_note']}{trig}")
        if trig:
            S.append(f"Тренд вверх, но без нового максимума: наблюдение тренда включится, если день закроется выше {fmt_price(reg['hh20'])}.")
    facts = {"price": p, "low7": lo7, "high7": hi7, "support": sup, "resistance": res,
             "regime": {k: reg.get(k) for k in ("long", "mid", "mid_note", "sma200", "hh20", "ll20", "atr_pct")}}
    return slide("btc", "BTC: уровни и режим", "chartCard", B, S, facts,
                 "Открой график BTC на 4ч: покажи зону поддержки снизу и сопротивления сверху; потом переключи на «день», чтобы был виден путь недели.")


def s_forecast(ctx: dict, now_ms: int) -> dict | None:
    o = ctx.get("outlook") or {}
    hz = o.get("horizons") or {}
    if not hz or not any((hz.get(h) or {}).get("range") for h in ("day", "week", "month")):
        return None
    B, S = [], ["Теперь прогноз — и сразу как мы его считаем."]
    names = {"day": "Сутки", "week": "Неделя", "month": "Месяц"}
    say_r = []
    for h in ("day", "week", "month"):
        x = hz.get(h) or {}
        r, b = x.get("range"), x.get("base_rate_up")
        if not r:
            continue
        line = f"{names[h]}: {fmt_price(r['lo'])} – {fmt_price(r['hi'])} $ (±{r['pm_pct']:.1f}%)"
        if b:
            line += f", рост в {round(b['p'] * 100)}% случаев — {b['verdict_ru']}"
        B.append(line)
        say_r.append(f"{'на сутки' if h == 'day' else 'на неделю' if h == 'week' else 'на месяц'} — от {fmt_price(r['lo'])} до {fmt_price(r['hi'])}")
    if say_r:
        S.append("В 2 случаях из 3 биткоин остаётся в коридоре: " + ", ".join(say_r) + ".")
    brs = [(h, (hz.get(h) or {}).get("base_rate_up")) for h in ("day", "week", "month")]
    brs = [(h, b) for h, b in brs if b]
    if brs:
        reg = "в бычьем режиме" if brs[0][1].get("regime") == "BULL" else "в небычьем режиме"
        allcoin = all(b["verdict"] == "coin" for _, b in brs)
        S.append(f"Направление {'— как монетка' if allcoin else 'по истории'}: {reg} рост был в "
                 + ", ".join(f"{round(b['p'] * 100)}% {'дней' if h == 'day' else 'недель' if h == 'week' else 'месяцев'}" for h, b in brs)
                 + f" за {brs[0][1]['period_ru']}.")
    ab = (hz.get("day") or {}).get("forecast_ab")
    if ab and ab.get("n"):
        B.append(f"Прогноз A/B на сутки: {ab.get('scenario_ru', 'нет')}; угадал {round(ab['hit_rate'] * 100)}% из {ab['n']}")
        S.append(f"За {ab['n']} {plural(ab['n'], 'проверку', 'проверки', 'проверок')} наш короткий прогноз A/B угадал {round(ab['hit_rate'] * 100)}% — {ab.get('verdict_ru', '')}.")
    if o.get("params_ru"):
        S.append(o["params_ru"].replace("Что используем:", "Что мы используем:"))
    facts = {"horizons": {h: {"range": (hz.get(h) or {}).get("range"), "base_rate_up": (hz.get(h) or {}).get("base_rate_up")} for h in ("day", "week", "month")},
             "forecast_ab": ab, "params_ru": o.get("params_ru"), "asof": o.get("asof")}
    return slide("forecast", "Прогноз: сутки, неделя, месяц", "fcCard", B, S, facts,
                 "Открой карточку «Прогноз»: там коридор на сутки, неделю и месяц и что на него влияет. Подчеркни: коридор — 2 из 3, не гарантия.")


def strategy_rows(ctx: dict) -> list[dict]:
    """The rows of the panel's «Результат всех стратегий» (renderStrats in web/app.html)."""
    rows = []
    def add(group, name, eq, start, open_n, trades, since):  # noqa: E306
        if eq is None:
            return
        rows.append({"group": group, "name": name, "equity": round(eq, 2), "pnl": round(eq - start, 2), "pct": round((eq - start) / start * 100, 2),
                     "open": open_n, "trades": trades, "since": since})
    pr = (ctx.get("paper") or {}).get("profiles") or {}
    for k, g in (("cons", "Фьючерсы"), ("bal", "Фьючерсы"), ("aggr", "Фьючерсы"), ("exp", "Эксперимент"), ("lab", "Эксперимент")):
        v = pr.get(k)
        if v:
            eh = v.get("equity_history") or []
            add(g, v.get("name"), (v.get("equity") or 0) + (v.get("open_pnl_usd") or 0), v.get("start_equity") or 10000, len(v.get("open") or []),
                (v.get("stats") or {}).get("trades") or 0, (eh[0].get("time") if eh else v.get("since")))
            rows[-1]["key"] = k
    for k, v in ((ctx.get("portfolio") or {}).get("portfolios") or {}).items():
        if k == "meme":
            continue
        eq = v.get("equity_usd") if v.get("equity_usd") is not None else 10000 * (v.get("index") or 100) / 100
        h = v.get("history") or []
        add("Спот · эксперимент" if v.get("experiment") else "Спот", v.get("name"), eq, 10000,
            len([x for x in v.get("positions") or [] if not x.get("status") or x.get("status") == "OPEN"]), len(v.get("actions") or []), h[0].get("time") if h else None)
        rows[-1]["key"] = "spot_" + k
    f = ctx.get("funding")
    if f and f.get("equity") is not None:
        add("Заработок", "Фандинг · без направления", f["equity"], f.get("start_equity") or 10000, len(f.get("positions") or []), None,
            (f.get("history") or [[None]])[0][0])
        rows[-1]["key"] = "funding"
    m = (ctx.get("memes") or {}).get("account")
    if m:
        add("Мемы", "Мем-портфель", m.get("equity"), m.get("start_equity") or 10000, len(m.get("positions") or []), (m.get("stats") or {}).get("trades") or 0, None)
        rows[-1]["key"] = "memes"
    a = (ctx.get("arb") or {}).get("week")
    if a:
        add("Заработок", "Арбитраж (виртуально)", 10000 + (a.get("virtual_pnl_usd") or 0), 10000, None, a.get("windows"), (ctx.get("arb") or {}).get("since"))
        rows[-1]["key"] = "arb"
    return rows


def s_strategies(ctx: dict, now_ms: int) -> dict | None:
    rows = strategy_rows(ctx)
    if not rows:
        return None
    by = {r["key"]: r for r in rows}
    tot = sum(r["pnl"] for r in rows)
    paper = ctx.get("paper") or {}
    B, S = [], ["Наши стратегии. Все — на демо-счетах по 10 000 долларов, видно и плюсы, и минусы."]
    ap = [by[k] for k in ("cons", "bal", "aggr") if k in by]
    reset = paper.get("reset") or {}
    if ap:
        tr = sum(r["trades"] or 0 for r in ap)
        since = kyiv(reset["time"]).strftime("%d.%m") if reset.get("time") else None
        B.append("Автопилот: " + ", ".join(f"{r['name'].replace('Автопилот · ', '')} {usd(r['pnl'])}" for r in ap)
                 + (f" — с {since}, сделок {tr}" if since else f", сделок {tr}"))
        if reset.get("time") and reset.get("archived_trades") is not None:
            n = reset["archived_trades"]
            S.append(f"{since} счета автопилота обнулили по решению владельца: прошлые {n} {plural(n, 'сделка', 'сделки', 'сделок')} — в архиве, "
                     f"итог {say_usd(reset.get('archived_pnl_usd'))}.")
        S.append(f"С тех пор у автопилота {tr} {plural(tr, 'сделка', 'сделки', 'сделок')}" +
                 (": сигналов на покупку после резкого падения не было." if tr == 0 else ": " + ", ".join(f"{speak_name(r['name'])} {say_usd(r['pnl'])}" for r in ap) + "."))
    lab = [by[k] for k in ("lab", "exp") if k in by]
    if lab:
        B.append("Эксперименты: " + ", ".join(f"{r['name']} {usd(r['pnl'])} ({r['trades']} {plural(r['trades'] or 0, 'сделка', 'сделки', 'сделок')})" for r in lab))
        S.append("Эксперименты: " + ", ".join(f"{speak_name(r['name'])} — {say_usd(r['pnl'])}" for r in lab) + ".")
    spot = [r for r in rows if r["key"].startswith("spot_")]
    if spot:
        B.append("Спот: " + ", ".join(f"{r['name'].split(' · ')[0]} {pct(r['pct'] / 100)}" for r in spot))
        resets = [(v.get("name"), (v.get("reset") or {}).get("archived_pnl_usd")) for k, v in ((ctx.get("portfolio") or {}).get("portfolios") or {}).items()
                  if (v.get("reset") or {}).get("archived_pnl_usd") is not None]
        best = max(spot, key=lambda r: r["pct"]); worst = min(spot, key=lambda r: r["pct"])
        S.append(f"Спотовые портфели: лучший — {best['name'].split(' · ')[0]}, {say_pct(best['pct'] / 100)}; слабее всех — {worst['name'].split(' · ')[0]}, {say_pct(worst['pct'] / 100)}.")
        if resets:
            B.append("Архив до пересборки 17.10: " + ", ".join(f"{n} {usd(v)}" for n, v in resets))
    other = [by[k] for k in ("funding", "memes", "arb") if k in by]
    if other:
        B.append(", ".join(f"{r['name']} {usd(r['pnl'])}" for r in other))
    B.append(f"Всего по всем счетам: {usd(tot)}")
    S.append(f"Всего по всем счетам сейчас {say_usd(tot)}.")
    return slide("strategies", "Наши стратегии", "stratsCard", B[:5], S, {"rows": rows, "total_pnl": round(tot, 2), "paper_reset": reset or None},
                 "Разверни карточку «Результат всех стратегий»: пройди сверху вниз — фьючерсы, эксперименты, спот, заработок; покажи итог справа вверху.")


def s_now(ctx: dict, now_ms: int) -> dict | None:
    paper = ctx.get("paper") or {}
    pr = paper.get("profiles") or {}
    scan = paper.get("scan")
    if not pr and not scan:
        return None
    try:
        from .paper_desk import RULE_RU
    except Exception:  # noqa: BLE001
        RULE_RU = {}
    opens = []
    for k, v in pr.items():
        for o in v.get("open") or []:
            opens.append({"desk": k, "desk_name": v.get("name"), "symbol": o.get("symbol"), "side": o.get("side"), "grade": o.get("grade"),
                          "rule_ru": RULE_RU.get(o.get("grade"), o.get("kind")), "entry": o.get("entry"), "price": o.get("price"),
                          "open_pnl_usd": o.get("open_pnl_usd")})
    main = [o for o in opens if o["desk"] in ("cons", "bal", "aggr")]
    rr = radar_rows(scan) if scan else []
    B, S = [], ["Что делаем прямо сейчас."]
    if main:
        B.append("Автопилот: " + "; ".join(f"{o['symbol']} {'лонг' if o['side'] == 'LONG' else 'шорт'} ({o['rule_ru']}) {usd(o['open_pnl_usd'])}" for o in main[:3]))
        S.append("У автопилота открыто: " + ", ".join(f"{o['symbol']} {'лонг' if o['side'] == 'LONG' else 'шорт'} по правилу «{o['rule_ru']}»" for o in main[:3]) + ".")
    else:
        B.append("Автопилот: позиций нет — ждёт резкого падения")
        calm = scan and (scan.get("breadth") or 0) < (scan.get("need_breadth") or 0.4) and (not rr or rr[0]["score"] < 1)
        S.append("Основной автопилот без позиций: он покупает только после резких падений" + (", а сейчас ни одно условие не выполнено." if calm else "."))
    other = [o for o in opens if o["desk"] not in ("cons", "bal", "aggr")]
    if other:
        tot = sum(o.get("open_pnl_usd") or 0 for o in other)
        B.append(f"Эксперименты: открыто {len(other)} ({', '.join(sorted({o['symbol'] for o in other})[:6])}), сейчас {usd(tot)}")
        S.append(f"В экспериментах открыто {len(other)} {plural(len(other), 'позиция', 'позиции', 'позиций')}, по ним сейчас {say_usd(tot)}.")
    if scan and scan.get("breadth") is not None:
        B.append(f"Паника по рынку: {round(scan['breadth'] * 100)}% монет · нужно {round((scan.get('need_breadth') or 0.4) * 100)}%")
    top = rr[:3]
    if top:
        B.append("Ближе всего к сигналу: " + ", ".join(f"{r['coin']} {min(100, round(r['score'] * 100))}%" for r in top))
        r0 = top[0]
        held = sorted({o["desk_name"] for o in opens if str(o.get("symbol") or "").replace("USDT", "").replace("-", "") == r0["coin"]})
        r0["held_by"] = held
        alt = " (альткоин, только для счёта «Тест новых идей»)" if r0["b_only"] else ""
        S.append(f"Ближе всего к сигналу {r0['coin']}{alt} — {min(100, round(r0['score'] * 100))}% пути: {r0['condition_ru']}, {r0['detail_ru']}."
                 + (f" По этой монете уже открыта сделка: {', '.join(held)}." if held else ""))
        if r0["score"] < 1:
            S.append("Полоска дойдёт до конца — будет вход; пока ждём.")
    show = "fdeskBox" if main else "apRadar"
    tip = ("Вкладка «Фьючерсы»: покажи панель сделок автопилота с открытыми позициями, потом панель «Ждём вход»." if main else
           "Открой панель «Ждём вход» (вкладка «Фьючерсы»): покажи верхние 2–3 монеты — полоска дошла до конца, значит будет вход.")
    return slide("now", "Что делаем сейчас", show, B, S, {"open": opens, "radar_top": top, "breadth": (scan or {}).get("breadth"),
                                                          "need_breadth": (scan or {}).get("need_breadth")}, tip)


def s_ideas(ctx: dict, now_ms: int) -> dict | None:
    d = ctx.get("ideas")
    if not d or not d.get("available") or not d.get("rows"):
        return None
    rows = d["rows"]
    grp = {s: [r for r in rows if r.get("status") == s] for s in ("buy", "add", "small", "wait", "avoid")}
    c = d.get("counts") or {s: len(v) for s, v in grp.items()}
    sym = lambda xs: ", ".join(r["symbol"] for r in xs[:8]) or "нет"  # noqa: E731
    B = [f"Можно брать ({c.get('buy', 0)}): {sym(grp['buy'])}"]
    if grp["add"]:
        B.append(f"Можно добрать ({c.get('add', 0)}): {sym(grp['add'])}")
    B.append(f"На небольшую сумму ({c.get('small', 0)}): {sym(grp['small'])}")
    B.append(f"Не брать ({c.get('avoid', 0)}): {sym(grp['avoid'])}")
    B.append(f"Ждём: {c.get('wait', 0)} · {d.get('regime_ru', '')}".rstrip(" ·"))
    S = ["Актуально для набора на спот — это наша таблица отбора."]
    if d.get("regime_ru"):
        S.append(f"Сейчас {d['regime_ru']}.")
    S.append(f"Можно брать: {sym(grp['buy'])}." if grp["buy"] else "Монет со статусом «можно брать» сейчас нет.")
    if grp["buy"] and grp["buy"][0].get("why_ru"):
        S.append(f"Например, {grp['buy'][0]['symbol']}: {grp['buy'][0]['why_ru']}.")
    if grp["small"]:
        S.append(f"На небольшую сумму, которую не жалко: {sym(grp['small'])}.")
    S.append(d.get("disclaimer_ru") or "Это правила отбора, а не прогноз цены.")
    facts = {"counts": c, "buy": [{k: r.get(k) for k in ("symbol", "score", "why_ru", "price", "chg7")} for r in grp["buy"]],
             "small": [r["symbol"] for r in grp["small"]], "avoid": [r["symbol"] for r in grp["avoid"]], "btc_bull": d.get("btc_bull"),
             "summary_ru": d.get("summary_ru")}
    return slide("ideas", "Актуально для набора на спот", "ideasCard", B, S, facts,
                 "Открой «Актуально для набора на спот»: пройди по группам — можно брать, на небольшую сумму, не брать; у каждой строки написана причина.")


def s_memes(ctx: dict, now_ms: int) -> dict | None:
    m = ctx.get("memes")
    if not m or not m.get("account"):
        return None
    try:
        from .memes import DIP
    except Exception:  # noqa: BLE001
        DIP = -0.25
    a = m["account"]
    st = a.get("stats") or {}
    wait = sorted([r for r in m.get("hl_memes") or [] if r.get("signal") == "ждём" and r.get("dip_dd_pct") is not None], key=lambda r: r["dip_dd_pct"])[:3]
    B = [f"Мем-портфель: {usd(a.get('equity'), False)}, результат {usd(a.get('pnl_usd'))}, сделок {st.get('trades', 0)}, позиций {len(a.get('positions') or [])}",
         f"Правило ({a.get('rule_status', 'эксперимент')}): покупка после падения на {abs(DIP) * 100:.0f}% от максимума суток"]
    S = ["Мемы. Это самый рискованный рынок, поэтому здесь у нас только эксперимент.",
         f"Правило одно: покупаем мем-фьючерс после обвала на {abs(DIP) * 100:.0f}% от максимума суток."]
    if wait:
        B.append("Ближе всего к обвалу: " + ", ".join(f"{r['coin']} {r['dip_dd_pct']:.1f}%".replace("-", "−") for r in wait))
        S.append(f"Ближе всего {wait[0]['coin']}: {say_pct(wait[0]['dip_dd_pct'] / 100)} от максимума суток, до сигнала ещё далеко."
                 if wait[0]["dip_dd_pct"] > DIP * 100 else f"{wait[0]['coin']} уже {say_pct(wait[0]['dip_dd_pct'] / 100)} от максимума суток.")
    S.append(f"Счёт сейчас {say_usd(a.get('equity'), sign=False)}, результат {say_usd(a.get('pnl_usd'))}, сделок {st.get('trades', 0)}.")
    cnt = m.get("spot_status_counts")
    if cnt:
        B.append(f"Спот-мемы: на небольшую сумму {cnt.get('small', 0)}, ждём {cnt.get('wait', 0)}, не брать {cnt.get('avoid', 0)}")
    rs = m.get("research_summary") or {}
    if rs.get("verdict"):
        B.append(f"Исследование: {rs['verdict']}")
        S.append(f"Честный вывод нашего исследования: {rs['verdict']}.")
    facts = {"equity": a.get("equity"), "pnl_usd": a.get("pnl_usd"), "trades": st.get("trades"), "positions": [p.get("coin") for p in a.get("positions") or []],
             "dip_rule": DIP, "closest": [{k: r.get(k) for k in ("coin", "dip_dd_pct", "score")} for r in wait], "spot_counts": cnt,
             "research_verdict": rs.get("verdict")}
    return slide("memes", "Мемы", "memesCard", B, S, facts,
                 "Открой «Мем-сектор»: покажи счёт мем-портфеля и монеты со статусом «ждём» — насколько каждая ниже максимума суток.")


def s_earn(ctx: dict, now_ms: int) -> dict | None:
    arb, tri, f = ctx.get("arb"), ctx.get("tri"), ctx.get("funding")
    if not (arb or tri or f):
        return None
    B, S, facts = [], ["Заработок без направления — арбитраж и фандинг. Тут честно: пока это наблюдение, а не деньги."], {}
    if arb and arb.get("week"):
        w = arb["week"]; since = kyiv(arb["since"]).strftime("%d.%m") if arb.get("since") else None
        B.append(f"Арбитраж между биржами{' с ' + since if since else ''}: {w.get('windows', 0)} окон, виртуально {usd(w.get('virtual_pnl_usd'))}")
        if w.get("observations"):
            B.append(f"Застывшие наблюдения: {w.get('stuck_observations', 0)} из {w['observations']} — забрать нельзя")
        S.append(f"Арбитраж между биржами: {w.get('windows', 0)} {plural(w.get('windows', 0), 'окно', 'окна', 'окон')}, виртуально {say_usd(w.get('virtual_pnl_usd'))}.")
        if w.get("observations"):
            so = w.get("stuck_observations", 0)
            S.append(f"Но {so} {plural(so, 'наблюдение', 'наблюдения', 'наблюдений')} из {w['observations']} — застывшие: на бирже закрыт вывод или пустой стакан, такую разницу не забрать.")
        facts["arb"] = {k: w.get(k) for k in ("windows", "virtual_pnl_usd", "observations", "stuck_observations", "best_net", "median_net")} | {"since": arb.get("since")}
    if tri and tri.get("week"):
        w = tri["week"]
        B.append(f"Треугольники внутри биржи: {w.get('windows', 0)} окон, виртуально {usd(w.get('virtual_pnl_usd'))}")
        facts["tri"] = {k: w.get(k) for k in ("windows", "virtual_pnl_usd", "samples")} | {"scanned_total": tri.get("scanned_total")}
    if f and f.get("equity") is not None:
        bd = f.get("breakdown") or {}
        line = f"Фандинг: {usd(f.get('pnl_usd'))}"
        if bd.get("funding_usd") is not None:
            line += f" — фандинг {usd(bd['funding_usd'])}, комиссии {usd(-abs(bd.get('fees_usd') or 0))}"
        B.append(line)
        s = f"Фандинг-счёт сейчас {say_usd(f.get('pnl_usd'))}"
        if bd.get("funding_usd") is not None:
            s += f" — фандинга получили {say_usd(bd['funding_usd'], sign=False)}, а комиссий заплатили {say_usd(bd.get('fees_usd') or 0, sign=False)}"
        S.append(s + ".")
        if bd.get("breakeven_days"):
            S.append(f"Окупится примерно за {round(bd['breakeven_days'])} {plural(round(bd['breakeven_days']), 'день', 'дня', 'дней')}, если ставки не упадут.")
        facts["funding"] = {"equity": f.get("equity"), "pnl_usd": f.get("pnl_usd"), "positions": len(f.get("positions") or []),
                            **{k: bd.get(k) for k in ("funding_usd", "fees_usd", "breakeven_days", "days")}}
    if not B:
        return None
    return slide("earn", "Заработок: арбитраж и фандинг", "earnBar", B, S, facts,
                 "Покажи полосу «Заработок» (LIVE): арбитраж и треугольники за неделю, фандинг с начала — всё виртуальные счета.")


def s_plan(ctx: dict, now_ms: int) -> dict | None:
    c = coin(ctx.get("terminal"), "BTC")
    o = (ctx.get("outlook") or {}).get("horizons") or {}
    if not c and not o:
        return None
    B, S, facts = [], ["Подводим итог."], {}
    if c:
        reg = (c.get("regime") or {}).get("long")
        lab = "бычий" if reg == "BULL" else "медвежий" if reg == "BEAR" else "смешанный"
        B.append(f"BTC за неделю {pct(c.get('chg7'))}, режим {lab}")
        S.append(f"Биткоин за неделю {say_pct(c.get('chg7'))}, режим {lab}.")
        facts["btc"] = {"price": c.get("price"), "chg7": c.get("chg7"), "regime": reg}
    w = o.get("week") or {}
    if w.get("range"):
        r = w["range"]; b = w.get("base_rate_up") or {}
        B.append(f"Коридор на неделю: {fmt_price(r['lo'])} – {fmt_price(r['hi'])} $ (2 из 3)" + (f", направление — {b['verdict_ru']}" if b else ""))
        S.append(f"На неделю ориентир — коридор от {fmt_price(r['lo'])} до {fmt_price(r['hi'])}" + (f"; направление — {b['verdict_ru']}." if b else "."))
        facts["week_range"] = r
    cal = ctx.get("calendar") or {}
    ev = sorted([e for e in cal.get("events") or [] if e.get("time") and now_ms < e["time"] <= now_ms + WEEK_MS], key=lambda e: (-(e.get("importance") or 0), e["time"]))
    if ev:
        main_ev = (ev[0].get("importance") or 0) >= 2
        B.append(f"{'Главное событие' if main_ev else 'Событие недели'}: {ev[0].get('name')} — {kyiv_str(ev[0]['time'])}")
        S.append(f"{'Главное событие недели' if main_ev else 'Из календаря на неделю'} — {ev[0].get('name')}, {say_when(ev[0]['time'])} по Киеву.")
        facts["event"] = {k: ev[0].get(k) for k in ("time", "name", "importance")}
    else:
        B.append("Плановых событий на неделю нет")
    scan = (ctx.get("paper") or {}).get("scan") or {}
    if scan.get("need_breadth") is not None:
        B.append(f"Автопилот ждёт: паника от {round(scan['need_breadth'] * 100)}% монет, пролив, сброс позиций или дисконт фьючерса")
        S.append("Автопилот ждёт резкого падения — там он и покупает; в спокойном рынке он не торгует.")
        facts["need_breadth"] = scan["need_breadth"]
    B.append(DISCLAIMER)
    S.append("Это не финансовая рекомендация, все счета — демо. Спасибо, что были с нами!")
    return slide("plan", "Итог и план на неделю", "chartCard", B[-5:] if len(B) > 5 else B, S, facts,
                 "Вернись на график BTC: покажи коридор недели и ближние зоны; в конце — общий вид панели.")


SLIDES = (s_week, s_news, s_macro, s_calendar, s_breadth, s_rsi, s_btc, s_forecast, s_strategies, s_now, s_ideas, s_memes, s_earn, s_plan)


def build_slides(ctx: dict, now_ms: int) -> list[dict]:
    out = []
    for fn in SLIDES:
        try:
            s = fn(ctx, now_ms)
        except Exception as e:  # noqa: BLE001  — one broken input must not kill the deck
            s = None
            ctx.setdefault("_errors", []).append(f"{fn.__name__}: {e!r}")
        if s:
            out.append(s)
    return out


def duration_min(slides: list[dict]) -> float:
    words = sum(len(s["say_ru"].split()) for s in slides)
    return round(words / WORDS_PER_MIN + SHOW_MIN_PER_SLIDE * len(slides), 1)


# ------------------------------------------------------------------ schedule + live block
def should_regenerate(path: str, now_ms: int) -> str | None:
    """'missing' (no deck / unreadable), 'friday' (any run on a Kyiv Friday), 'stale' (deck older than 7 days), else None."""
    try:
        doc = json.load(open(path, encoding="utf-8"))
        gen = int(doc.get("generated") or 0)
        if not doc.get("slides"):
            return "missing"
    except Exception:  # noqa: BLE001
        return "missing"
    if kyiv(now_ms).weekday() == 4:
        return "friday"
    if now_ms - gen > WEEK_MS:
        return "stale"
    return None


def live(ctx: dict, now_ms: int, generated: int | None) -> dict:
    term = ctx.get("terminal") or {}
    out = {"asof": now_ms, "asof_kyiv": kyiv(now_ms).strftime("%d.%m.%Y %H:%M"), "data_asof": term.get("asof"),
           "data_asof_kyiv": kyiv(term["asof"]).strftime("%d.%m %H:%M") if term.get("asof") else None, "coins": {}}
    for s in CORE:
        c = coin(term, s)
        if c:
            out["coins"][s] = {"price": c.get("price"), "chg24": c.get("chg24"), "chg7": c.get("chg7")}
    if "BTC" in out["coins"]:
        out["btc_price"] = out["coins"]["BTC"]["price"]
    fng = (ctx.get("markets") or {}).get("fng") or {}
    if fng.get("value") is not None:
        out["fng"] = fng.get("value")
    if generated:
        out["deck_age_h"] = round((now_ms - generated) / 3_600_000, 1)
        out["note_ru"] = (f"Слайды собраны {kyiv(generated).strftime('%d.%m %H:%M')} по Киеву; цены в этом блоке — на "
                          f"{out['data_asof_kyiv'] or out['asof_kyiv']}.")
    return out


def load_ctx(in_dir: str, conn: sqlite3.Connection | None = None) -> dict:
    ctx = {n: _load(in_dir, n + ".json") for n in FILES}
    ctx["conn"] = conn
    return ctx


def build(ctx: dict, now_ms: int, reason: str) -> dict:
    slides = build_slides(ctx, now_ms)
    k = kyiv(now_ms)
    monday = (k - dt.timedelta(days=k.weekday())).date()
    return {"asof": now_ms, "generated": now_ms, "generated_kyiv": k.strftime("%d.%m.%Y %H:%M"), "week_of": monday.isoformat(),
            "period_ru": f"{kyiv(now_ms - WEEK_MS).strftime('%d.%m')}–{k.strftime('%d.%m.%Y')}", "generated_reason": reason,
            "duration_min_est": duration_min(slides), "slides": slides, "show_ids": list(SHOW_IDS), "disclaimer_ru": DISCLAIMER,
            "errors": ctx.get("_errors", []),
            "sources": {n: (ctx.get(n) or {}).get("asof") if isinstance(ctx.get(n), dict) else None for n in FILES}}


def run(out_dir: str = "web", conn: sqlite3.Connection | None = None, now_ms: int | None = None, force: bool = False,
        in_dir: str | None = None) -> tuple[dict, str | None]:
    """Rebuild the deck when due (or forced), always refresh `live`; writes out_dir/broadcast.json. Returns (doc, reason|None)."""
    now_ms = now_ms or int(time.time() * 1000)
    path = os.path.join(out_dir, "broadcast.json")
    ctx = load_ctx(in_dir or out_dir, conn)
    reason = "forced" if force else should_regenerate(path, now_ms)
    if reason:
        doc = build(ctx, now_ms, reason)
    else:
        doc = json.load(open(path, encoding="utf-8"))
    doc["asof"] = now_ms
    doc["live"] = live(ctx, now_ms, doc.get("generated"))
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, default=str)
    return doc, reason
