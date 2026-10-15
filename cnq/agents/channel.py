"""Channel editor — writes the Telegram channel posts for @crypto_nova_news from the JSON the other departments
already produce (web/*.json). Four slots a day in Kyiv time, in the format Dmytro ran by hand before:
  08–10  Утренний обзор рынка   — цены, аналитика одним-двумя абзацами, ключевые факторы, вывод CRYPTO NOVA (+ картинка)
  11–13  Новость                — одна главная новость: своя шапка, что случилось, почему важно, вывод, ссылка на источник
  15–17  Новость                — вторая новость дня (если есть сильная)
  20–22  Итоги дня              — главные события, как вёл себя рынок, прогноз кратко/средне/долгосрочно, вывод (+ картинка)
The LLM writes the text from a compact facts JSON; a post is accepted only if every number in it exists in the facts
(no invented figures). Without an LLM a plain template is sent. One post per slot per day (table channel_posts).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=3))

SLOTS = {"morning": (8, 10), "news1": (11, 13), "news2": (15, 17), "evening": (20, 22)}
SIGN = ""

CAT_MECH = {
    "regulation": {"bear": "Регуляторный негатив — часть крупных денег берёт паузу, спрос на покупку редеет, а продавцы остаются.",
                   "bull": "Регуляторная ясность — институциональным деньгам проще заходить, спрос расширяется."},
    "etf_flows": {"bull": "Притоки в ETF — фонды обязаны покупать под новые деньги, это механический спрос на споте.",
                  "bear": "Оттоки из ETF — фонды вынуждены продавать под выводы, это механическое давление на спот."},
    "hack": {"bear": "Взлом — вынужденные продажи украденного и бегство из протокола; давление на монету и на доверие к сектору."},
    "macro": {"bull": "Мягкая макро-картина — дешевле деньги, выше аппетит к риску, крипта получает приток.",
              "bear": "Жёсткая макро-картина — дорогие деньги, риск сокращают первым делом в самых волатильных активах.",
              "neutral": "Макро-событие — волатильность вокруг публикации, направление решит реакция, а не заголовок."},
    "listing": {"bull": "Листинг — новая аудитория и ликвидность, обычно краткий импульс спроса."},
    "unlock": {"bear": "Разлок токенов — ранние инвесторы получают монеты по низкой цене, часть неизбежно продаёт."},
    "tech": {"bull": "Технологическое обновление — улучшает фундаментал, но цена отыгрывает заранее.",
             "bear": "Сбой в сети — доверие и активность падают, спрос откладывается."},
    "adoption": {"bull": "Принятие — реальный спрос на использование, медленный, но устойчивый фактор."},
}


def _fmt(p) -> str:
    if p is None:
        return "—"
    p = float(p)
    if abs(p) >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if abs(p) >= 1:
        return f"{p:.2f}"
    return f"{p:.4f}"


def _pct(x, digits=1) -> str:
    if x is None:
        return "—"
    return f"{x:+.{digits}f}%".replace("+-", "-")


def _load(out: str, name: str):
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def kyiv_now(now_ms: int | None = None) -> dt.datetime:
    t = (now_ms or int(time.time() * 1000)) / 1000
    return dt.datetime.fromtimestamp(t, KYIV)


def slot_for(now: dt.datetime) -> str | None:
    for k, (a, b) in SLOTS.items():
        if a <= now.hour < b:
            return k
    return None


class Channel:
    def __init__(self, conn: sqlite3.Connection, out: str = "web"):
        self.conn = conn
        self.out = out
        conn.execute("CREATE TABLE IF NOT EXISTS channel_posts(day TEXT, slot TEXT, sent_at INTEGER, text TEXT, PRIMARY KEY(day, slot))")
        conn.commit()

    # ------------------------------------------------------------------ data
    def ctx(self) -> dict:
        o = self.out
        return {"terminal": _load(o, "terminal.json") or {}, "decision": _load(o, "decision.json") or {},
                "markets": _load(o, "markets.json") or {}, "whales": _load(o, "whales.json") or {},
                "calibration": _load(o, "calibration.json") or {}, "paper": _load(o, "paper.json") or {},
                "research": _load(o, "research.json") or {}}

    @staticmethod
    def coin(term: dict, base: str) -> dict | None:
        for c in term.get("coins", []):
            if c.get("symbol", "").split("-")[0] == base:
                return c
        return None

    @staticmethod
    def zones(c: dict) -> tuple[dict | None, dict | None]:
        price = c.get("price") or 0
        lv = [l for l in c.get("levels", []) if l.get("status") in (None, "ACTIVE")]
        sup = sorted([l for l in lv if l["hi"] <= price], key=lambda l: -l["center"])
        res = sorted([l for l in lv if l["lo"] >= price], key=lambda l: l["center"])
        return (sup[0] if sup else None), (res[0] if res else None)

    @staticmethod
    def inside(c: dict) -> dict | None:
        price = c.get("price") or 0
        for l in c.get("levels", []):
            if l.get("status") in (None, "ACTIVE") and l["lo"] <= price <= l["hi"]:
                return l
        return None

    def scenario(self, c: dict, cal: dict) -> str:
        base = c["symbol"].split("-")[0]
        op = (cal.get("open") or {}).get(base)
        sup, res = self.zones(c)
        if op and op.get("p_up") is not None:
            p = op["p_up"]
            a_up = p >= 0.5
            pa = round((p if a_up else 1 - p) * 100)
            tgt_a = op.get("up") if a_up else op.get("dn")
            tgt_b = op.get("dn") if a_up else op.get("up")
            return (f"Сценарий A ({pa}%): {'к сопротивлению' if a_up else 'к поддержке'} {_fmt(tgt_a)}. "
                    f"Сценарий B ({100 - pa}%): {'к поддержке' if a_up else 'к сопротивлению'} {_fmt(tgt_b)}. "
                    f"Подтверждение A — закрепление {'выше' if a_up else 'ниже'} {_fmt(c['price'])} на объёме; "
                    f"если цена уходит в другую сторону, сценарий A снимается, работаем B.")
        parts = []
        if sup:
            parts.append(f"поддержка {_fmt(sup['lo'])}–{_fmt(sup['hi'])} ({sup.get('touches', 1)} касаний)")
        if res:
            parts.append(f"сопротивление {_fmt(res['lo'])}–{_fmt(res['hi'])} ({res.get('touches', 1)} касаний)")
        return "Ориентиры: " + "; ".join(parts) + "." if parts else ""

    # ------------------------------------------------------------------ pieces
    def market_block(self, x: dict) -> str:
        m = x["markets"]; g = m.get("global") or {}; f = m.get("fng") or {}
        lines = []
        if f.get("value") is not None:
            lines.append(f"Страх и жадность <b>{f['value']}</b> ({f.get('label', '')}; вчера {f.get('yesterday', '—')}, неделю назад {f.get('week', '—')}).")
        if g.get("total_mcap"):
            lines.append(f"Капитализация рынка <b>${g['total_mcap'] / 1e12:.2f} трлн</b>, {_pct(g.get('mcap_chg24'))} за сутки. Доминация BTC {g.get('btc_dom', 0):.1f}%.")
        btc = next((t for t in m.get("top", []) if t.get("symbol") == "BTC"), None)
        if btc:
            lines.append(f"Биткоин <b>${_fmt(btc['price'])}</b> ({_pct(btc.get('chg24'))} за сутки, {_pct(btc.get('chg7'))} за неделю, {btc.get('ath_chg', 0):.0f}% от максимума).")
        return " ".join(lines)

    def regime_block(self, x: dict) -> str:
        term = x["terminal"]
        c = self.coin(term, "BTC") or (term.get("coins") or [None])[0]
        if not c:
            return ""
        r = c.get("regime") or {}
        long_ = "долгосрок бычий — цена выше 200-дневной" if r.get("long") == "BULL" else "долгосрок медвежий — цена ниже 200-дневной"
        mid = {"LONG": "среднесрок лонг", "SHORT": "среднесрок шорт", "UP_WAIT": "среднесрок ждёт подтверждения вверх", "DOWN_WAIT": "среднесрок ждёт подтверждения вниз"}.get(r.get("mid"), "среднесрок без режима")
        short = {"UP": "импульс вверх", "DOWN": "импульс вниз", "FLAT": "боковик"}.get(r.get("short"), "")
        stress = (x["paper"].get("stress") or {})
        s = ""
        if isinstance(stress, dict) and stress.get("level"):
            s = f" Стресс рынка {stress['level']} из 3 — {'покупки запрещены, ждём откупа у сильной поддержки' if stress['level'] >= 2 else 'осторожно'}."
        return f"Режим по {c['symbol'].split('-')[0]}: {long_}; {mid}; {short} (ATR {r.get('atr_pct', 0):.1f}% в день).{s}"

    def watch_block(self, x: dict, n: int = 3) -> str:
        term = x["terminal"]
        rows = []
        for c in term.get("coins", []):
            base = c['symbol'].split('-')[0]
            z = self.inside(c)
            if z and z.get("touches", 1) >= 3:
                rows.append((0.0, f"{base} {_fmt(c['price'])} — цена внутри зоны {_fmt(z['lo'])}–{_fmt(z['hi'])} (касаний: {z['touches']}) — ждём реакции: отбой или пробой на объёме"))
                continue
            sup, res = self.zones(c)
            for z, kind in ((sup, "поддержка"), (res, "сопротивление")):
                if z and z.get("dist_atr") is not None and z["dist_atr"] <= 1.0 and z.get("touches", 1) >= 3:
                    rows.append((z["dist_atr"], f"{base} {_fmt(c['price'])} — {kind} {_fmt(z['lo'])}–{_fmt(z['hi'])} (касаний: {z['touches']}), {z['dist_atr']:.1f} ATR до зоны"))
        rows.sort()
        if not rows:
            return "Цена далеко от сильных зон — сделок от уровня сейчас нет, ждём подхода."
        return "Где смотрим:\n" + "\n".join("• " + r[1] for r in rows[:n])

    def whales_block(self, x: dict) -> str:
        w = x["whales"]
        if not w.get("available"):
            return ""
        s = w.get("summary") or {}
        if not s.get("n_whales"):
            return ""
        out = [f"Киты Hyperliquid ({s['n_whales']} крупнейших): лонг ${s.get('total_long_usd', 0) / 1e6:.1f}M против шорт ${s.get('total_short_usd', 0) / 1e6:.1f}M — перевес {'в лонг' if s.get('bias') == 'LONG' else 'в шорт' if s.get('bias') == 'SHORT' else 'ничей'}."]
        leans = [c for c in w.get("coins", []) if c.get("lean") in ("LONG", "SHORT")][:3]
        if leans:
            out.append("По монетам: " + ", ".join(f"{c['coin']} {'лонг' if c['lean'] == 'LONG' else 'шорт'} (${(c['long_usd'] if c['lean'] == 'LONG' else c['short_usd']) / 1e6:.1f}M)" for c in leans) + ".")
        ch = [c for c in w.get("changes", []) if c.get("kind") in ("OPEN", "FLIP")][:2]
        if ch:
            out.append("Свежие движения: " + "; ".join(f"{c.get('kind') == 'FLIP' and 'разворот' or 'открыл'} {c.get('side', '').lower()} {c.get('coin')} ${(c.get('usd') or 0) / 1e6:.1f}M" for c in ch) + ".")
        out.append("Помним: стены и позиции китов — контекст, не сигнал; крупные игроки умеют ставить ловушки.")
        return " ".join(out)

    def news_block(self, x: dict, n: int = 3) -> str:
        d = x["decision"].get("news") or {}
        seen = set(); imps = []
        for i in sorted(d.get("impacts", []), key=lambda i: (-(i.get("magnitude") or 0), -(i.get("confidence") or 0))):
            t = (i.get("title") or "").strip().lower()
            if (i.get("magnitude") or 0) < 3 or i.get("category") in (None, "other") or t in seen:
                continue
            if (i.get("reasoning") or "").startswith("mock"):   # keyword stand-in, not an analysis — never publish it
                continue
            seen.add(t); imps.append(i)
        if not imps:
            return "Сильных новостных драйверов за сутки аналитик не выделил — рынок двигают потоки и уровни, а не заголовки."
        out = []
        for i in imps[:n]:
            reason = i.get("reasoning") or ""
            if not reason or reason.startswith("mock"):
                reason = (CAT_MECH.get(i.get("category"), {}) or {}).get(i.get("direction"), "")
            arrow = "▲" if i.get("direction") == "bull" else "▼" if i.get("direction") == "bear" else "●"
            asset = i.get("asset") or "MARKET"
            out.append(f"{arrow} <b>{asset if asset != 'MARKET' else 'Рынок'}</b> — {i.get('title', '').strip()}\n{reason} "
                       f"Горизонт: {({'hours': 'часы', 'days': 'дни', 'weeks': 'недели'}).get(i.get('horizon'), 'дни')}, сила {i.get('magnitude')}/5."
                       + (f" Подтвердит: {i['confirm']}." if i.get("confirm") and not i['confirm'].startswith('цена закрепится за ближайшим') else ""))
        return "\n\n".join(out)

    def desks_block(self, x: dict) -> str:
        p = x["paper"]
        prof = p.get("profiles") or {}
        if not prof:
            return ""
        names = {"cons": "консервативная", "bal": "сбалансированная", "aggr": "агрессивная"}
        parts = []
        for k in ("cons", "bal", "aggr"):
            q = prof.get(k)
            if not q:
                continue
            parts.append(f"{names[k]} {_pct(q.get('pnl_pct'), 2)} ({len(q.get('open') or [])} в работе)")
        day_closed = [t for t in (p.get("closed") or []) if (t.get("exit_time") or 0) > time.time() * 1000 - 86400_000]
        s = f"Демо-столы ($10 000 каждый): " + "; ".join(parts) + "."
        if day_closed:
            r = sum(t.get("r", 0) for t in day_closed)
            s += f" За сутки закрыто {len(day_closed)} сделок, итог {r:+.2f}R."
        return s

    def calibration_block(self, x: dict) -> str:
        st = (x["calibration"].get("stats") or {})
        if not st.get("n"):
            return "Точность прогнозов: первые проверки ещё идут — цифры покажем, когда наберётся выборка."
        return (f"Честно про прогнозы: проверено {st['n']}, сценарий A сбылся в {round(st['hit_rate'] * 100)}% случаев "
                f"(Brier {st['brier']:.2f}; монетка — 0.25). {'Выборка пока мала, выводы рано.' if st['n'] < 30 else ''}").strip()

    def movers_block(self, x: dict) -> str:
        top = [t for t in x["markets"].get("top", []) if not t.get("stable") and not t.get("wrapped") and t.get("chg24") is not None
               and (t.get("rank") or 999) <= 100 and (t.get("volume") or 0) >= 5e7 and not re.search(r"USD|EUR|GOLD|XAU|PAX", t.get("symbol", ""))]
        if not top:
            return ""
        up = sorted(top, key=lambda t: -t["chg24"])[:3]
        dn = sorted(top, key=lambda t: t["chg24"])[:3]
        return ("Лидеры суток: " + ", ".join(f"{t['symbol']} {_pct(t['chg24'])}" for t in up) +
                ". Аутсайдеры: " + ", ".join(f"{t['symbol']} {_pct(t['chg24'])}" for t in dn) + ".")

    # ------------------------------------------------------------------ facts (compact, pre-rounded: the LLM copies them)
    MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    def date_ru(self, now: dt.datetime) -> str:
        return f"{now.day} {self.MONTHS[now.month - 1]} {now.year}"

    def facts(self, x: dict, now: dt.datetime) -> dict:
        m = x["markets"]; g = m.get("global") or {}; f = m.get("fng") or {}
        top = {t["symbol"]: t for t in m.get("top", []) if t.get("symbol") in ("BTC", "ETH", "SOL")}
        term = x["terminal"]; btc = self.coin(term, "BTC")
        r = (btc or {}).get("regime") or {}
        stress = (x["paper"].get("stress") or {})
        lvl = stress.get("level") if isinstance(stress, dict) else None
        out = {"date": self.date_ru(now),
               "prices": {k: {"usd": _fmt(v["price"]), "chg24": f"{v.get('chg24', 0):+.1f}%", "chg7": f"{v.get('chg7', 0):+.1f}%"} for k, v in top.items()},
               "market_cap_trln": f"{g['total_mcap'] / 1e12:.2f}" if g.get("total_mcap") else None,
               "market_cap_chg24": f"{g.get('mcap_chg24', 0):+.1f}%" if g.get("mcap_chg24") is not None else None,
               "btc_dominance": f"{g.get('btc_dom', 0):.1f}%" if g.get("btc_dom") else None,
               "fear_greed": {"value": f.get("value"), "label": f.get("label"), "yesterday": f.get("yesterday")} if f.get("value") is not None else None,
               "btc_regime": {"long_term": "бычий: цена выше 200-дневной средней" if r.get("long") == "BULL" else "медвежий: цена ниже 200-дневной средней",
                              "mid_term": {"LONG": "тренд вверх", "SHORT": "тренд вниз", "UP_WAIT": "готовится к росту, ждём пробоя", "DOWN_WAIT": "готовится к снижению, ждём пробоя"}.get(r.get("mid"), "без выраженного тренда"),
                              "impulse": {"UP": "импульс вверх", "DOWN": "импульс вниз", "FLAT": "боковик"}.get(r.get("short"), "")} if r else None,
               "market_stress": {0: "спокойный", 1: "нервный", 2: "распродажа — большинство монет падает", 3: "паника — падает почти всё"}.get(lvl, "спокойный") if lvl is not None else None}
        if btc:
            sup, res = self.zones(btc)
            out["btc_zones"] = {"support": f"{_fmt(sup['lo'])}–{_fmt(sup['hi'])}" if sup else None, "resistance": f"{_fmt(res['lo'])}–{_fmt(res['hi'])}" if res else None}
            op = (x["calibration"].get("open") or {}).get("BTC")
            if op and op.get("p_up") is not None:
                pr = op["p_up"]; a_up = pr >= 0.5
                out["btc_scenario"] = {"A": f"{round((pr if a_up else 1 - pr) * 100)}% — {'к сопротивлению' if a_up else 'к поддержке'} {_fmt(op['up'] if a_up else op['dn'])}",
                                       "B": f"{round((1 - pr if a_up else pr) * 100)}% — {'к поддержке' if a_up else 'к сопротивлению'} {_fmt(op['dn'] if a_up else op['up'])}"}
        wl = []
        for c in term.get("coins", []):
            z = self.inside(c)
            if z and z.get("touches", 1) >= 3:
                wl.append(f"{c['symbol'].split('-')[0]} {_fmt(c['price'])}: цена внутри зоны {_fmt(z['lo'])}–{_fmt(z['hi'])}, ждём отбой или пробой")
        out["coins_at_zones"] = wl[:3]
        w = x["whales"]; sw = (w.get("summary") or {}) if w.get("available") else {}
        if sw.get("n_whales"):
            out["hyperliquid_whales"] = f"{sw['n_whales']} крупнейших счетов: лонги ${sw.get('total_long_usd', 0) / 1e6:.1f}M против шортов ${sw.get('total_short_usd', 0) / 1e6:.1f}M, перевес {'в лонг' if sw.get('bias') == 'LONG' else 'в шорт' if sw.get('bias') == 'SHORT' else 'ничей'}"
        out["news"] = self.impacts(x, 4)
        prof = x["paper"].get("profiles") or {}
        if prof:
            names = {"cons": "консервативная", "bal": "сбалансированная", "aggr": "агрессивная"}
            out["demo_desks"] = {names[k]: f"{(prof[k].get('pnl_pct') or 0):+.2f}%, позиций в работе {len(prof[k].get('open') or [])}" for k in ("cons", "bal", "aggr") if prof.get(k)}
        st = x["calibration"].get("stats") or {}
        if st.get("n"):
            out["forecast_track_record"] = f"проверено {st['n']} прогнозов, сценарий A сбылся в {round(st['hit_rate'] * 100)}%"
        return out

    def impacts(self, x: dict, n: int) -> list[dict]:
        d = x["decision"].get("news") or {}
        seen = set(); out = []
        for i in sorted(d.get("impacts", []), key=lambda i: (-(i.get("magnitude") or 0), -(i.get("created") or 0))):
            t = (i.get("title") or "").strip()
            if (i.get("magnitude") or 0) < 3 or i.get("category") in (None, "other") or t.lower() in seen or (i.get("reasoning") or "").startswith("mock"):
                continue
            if time.time() * 1000 - (i.get("created") or 0) > 30 * 3600_000:
                continue
            seen.add(t.lower())
            out.append({"id": i.get("news_id"), "title": t, "source": i.get("source"), "link": i.get("link"), "asset": i.get("asset"),
                        "direction": {"bull": "позитив", "bear": "негатив", "neutral": "нейтрально"}.get(i.get("direction"), ""),
                        "strength": f"{i.get('magnitude')}/5", "mechanism": i.get("reasoning"), "confirms_if": i.get("confirm"), "created": i.get("created")})
        return out[:n]

    # ------------------------------------------------------------------ prompts
    VOICE = ("Ты — редактор Telegram-канала CRYPTO NOVA (AI-система оценки и анализа крипторынка). Пиши по-русски живо, дружелюбно и просто — "
             "так, чтобы понял подросток, но без упрощения смысла: называй причину и следствие (кто вынужден покупать или продавать и почему). "
             "Используй ТОЛЬКО факты и числа из JSON, копируй числа как они даны, ничего не выдумывай и не добавляй событий, которых нет в данных. "
             "Если какого-то факта нет — просто не упоминай его. Разметка: только <b>…</b> для заголовков разделов. Без эмодзи, без хэштегов, без ссылок, без подписи в конце.")

    PROMPTS = {
        "morning": "Напиши утренний пост. Структура строго:\n<b>Доброе утро, дорогие подписчики!</b>\n<b>Утренний обзор рынка · {date}</b>\n\n<b>Рынок:</b> одной-двумя строками цены BTC, ETH, SOL с изменением за сутки, капитализация, индекс страха и жадности.\n\n<b>Аналитика:</b> один-два абзаца: что сейчас двигает рынок (новости из news с их механизмом, если они есть; если нет — режим и потоки), как ведёт себя биткоин относительно альтов, что говорит индекс страха.\n\n<b>Ключевые факторы:</b> 3 коротких пункта, каждый с новой строки через «—».\n\n<b>Вывод CRYPTO NOVA:</b> 2–3 предложения: защитный или рабочий режим, базовый сценарий A с вероятностью и уровнем, где ждём разворота (зоны), что делает система (сделки только от зон; в распродажу не покупаем вдогонку, спот — частями). Длина всего поста до 1900 символов.",
        "news": "Напиши пост об одной новости. Структура строго:\n<b>{headline}</b> — придумай свою короткую сильную шапку (до 8 слов) по сути новости.\n\n<b>Что случилось:</b> 2–3 предложения по summary.\n\n<b>Почему это важно для рынка:</b> 2–4 предложения — механизм: кто вынужден покупать или продавать, на какие монеты влияет, что это меняет в настроении; упомяни текущую цену биткоина из данных.\n\n<b>Вывод CRYPTO NOVA:</b> 2 предложения: главный риск или шанс и что делает система (не торгует вдогонку, ждёт реакцию цены у зоны / подтверждение). Длина до 1200 символов. Ссылку НЕ вставляй — её добавят после.",
        "evening": "Напиши вечерний пост. Структура строго:\n<b>Итоги дня · {date}</b>\n\n<b>Главные события:</b> 2–3 пункта с новой строки «1.», «2.», «3.» — из news (с механизмом одной фразой); если новостей нет — главные движения рынка.\n\n<b>Как вёл себя рынок:</b> 2–3 предложения с числами: BTC, ETH, SOL за сутки, капитализация, страх и жадность, кто лидировал и кто падал.\n\n<b>Сценарий дня:</b> одно предложение — сработал ли утренний сценарий (из scenario_verdict).\n\n<b>Прогноз:</b> три строки: «Краткосрочно:» (по стрессу рынка и импульсу), «Среднесрочно:» (по тренду и зонам), «Долгосрочно:» (по режиму 200-дневной) — без новых чисел, только из данных.\n\n<b>Вывод CRYPTO NOVA:</b> 2–3 предложения, честно: что сбылось, что нет, что система делает завтра (сделки только от зон, стоп заранее, в панике не покупаем). Длина до 1900 символов."
    }

    # ------------------------------------------------------------------ posts
    @staticmethod
    def _nums(txt: str) -> set:
        return {re.sub(r"[\s,]", "", n).replace(".", ",").rstrip(",") for n in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", txt)}

    def guard(self, out: str, facts: dict) -> bool:
        """Every number in the post must exist in the facts (digits-only comparison; '81 004' == '81004', '2.9' == '2,9')."""
        if not out or len(out) < 200:
            return False
        pool = self._nums(json.dumps(facts, ensure_ascii=False))
        pool |= {str(n) for n in range(0, 32)} | {str(dt.datetime.now().year)}
        bad = [n for n in self._nums(out) if n and n not in pool and n.split(",")[0] not in pool]
        return not bad

    def write(self, slot: str, facts: dict, llm, headline_hint: str = "") -> str | None:
        if llm is None:
            return None
        prompt = self.PROMPTS["news" if slot.startswith("news") else slot].replace("{date}", facts.get("date", "")).replace("{headline}", headline_hint or "Шапка")
        for attempt in range(2):
            try:
                out = llm.complete(self.VOICE, prompt + "\n\nДанные (JSON):\n" + json.dumps(facts, ensure_ascii=False), temperature=0.4 if attempt == 0 else 0.2)
            except Exception:  # noqa: BLE001
                return None
            out = (out or "").strip()
            if self.guard(out, facts):
                return out
        return None

    def template(self, slot: str, facts: dict) -> str:
        """Plain fallback when the LLM is unavailable: same structure, data only."""
        p = facts.get("prices", {})
        pl = ", ".join(f"{k} ${v['usd']} ({v['chg24']})" for k, v in p.items())
        fg = facts.get("fear_greed")
        if slot == "morning":
            body = [f"<b>Доброе утро, дорогие подписчики!</b>\n<b>Утренний обзор рынка · {facts['date']}</b>", f"<b>Рынок:</b> {pl}." + (f" Капитализация ${facts['market_cap_trln']} трлн ({facts['market_cap_chg24']})." if facts.get("market_cap_trln") else "") + (f" Индекс страха и жадности {fg['value']} — {fg['label']}." if fg else "")]
            if facts.get("btc_regime"):
                body.append(f"<b>Аналитика:</b> Биткоин: {facts['btc_regime']['long_term']}; {facts['btc_regime']['mid_term']}; {facts['btc_regime']['impulse']}. Рынок {facts.get('market_stress') or 'спокойный'}." + (" " + " ".join(n["mechanism"] for n in facts.get("news", [])[:2]) if facts.get("news") else ""))
            kf = [n["title"] for n in facts.get("news", [])[:3]] or [f"Режим биткоина: {facts['btc_regime']['long_term']}" if facts.get("btc_regime") else "Новых драйверов нет — рынок ведут уровни"]
            body.append("<b>Ключевые факторы:</b>\n" + "\n".join("— " + k for k in kf))
            sc = facts.get("btc_scenario"); z = facts.get("btc_zones") or {}
            body.append("<b>Вывод CRYPTO NOVA:</b> " + (f"Базовый сценарий A: {sc['A']}; альтернатива B: {sc['B']}. " if sc else "") + (f"Поддержка {z.get('support')}, сопротивление {z.get('resistance')}. " if z.get('support') or z.get('resistance') else "") + "Сделки только от зон, стоп заранее; в распродажу не покупаем вдогонку.")
            return "\n\n".join(body)
        if slot.startswith("news"):
            n = facts["item"]
            return f"<b>{n['title']}</b>\n\n<b>Что случилось:</b> {n.get('summary') or n['title']}\n\n<b>Почему это важно для рынка:</b> {n.get('mechanism') or ''}\n\n<b>Вывод CRYPTO NOVA:</b> не торгуем вдогонку — ждём реакцию цены у ближайшей зоны." + (f" Подтвердит: {n['confirms_if']}." if n.get("confirms_if") else "")
        body = [f"<b>Итоги дня · {facts['date']}</b>"]
        ev = [n["title"] for n in facts.get("news", [])[:3]]
        body.append("<b>Главные события:</b>\n" + ("\n".join(f"{i + 1}. {t}" for i, t in enumerate(ev)) if ev else "1. Сильных новостных драйверов не было — рынок вели уровни и потоки."))
        body.append(f"<b>Как вёл себя рынок:</b> {pl}." + (f" {facts['movers']}" if facts.get("movers") else "") + (f" Индекс страха и жадности {fg['value']} — {fg['label']}." if fg else ""))
        if facts.get("scenario_verdict"):
            body.append(f"<b>Сценарий дня:</b> {facts['scenario_verdict']}")
        r = facts.get("btc_regime") or {}
        body.append(f"<b>Прогноз:</b>\nКраткосрочно: рынок {facts.get('market_stress') or 'спокойный'}, {r.get('impulse', '')}.\nСреднесрочно: {r.get('mid_term', '')}.\nДолгосрочно: {r.get('long_term', '')}.")
        body.append("<b>Вывод CRYPTO NOVA:</b> " + (facts.get("forecast_track_record", "") + ". " if facts.get("forecast_track_record") else "") + "Завтра работаем так же: сделки только от зон, стоп известен заранее, в панике не покупаем.")
        return "\n\n".join(body)

    def pick_news(self, x: dict, exclude: set) -> dict | None:
        for n in self.impacts(x, 8):
            if n["id"] in exclude or not n.get("link"):
                continue
            if time.time() * 1000 - (n.get("created") or 0) > 10 * 3600_000:
                continue
            it = next((i for i in (x["decision"].get("news") or {}).get("items", []) if i.get("id") == n["id"]), {})
            n["summary"] = (it.get("summary") or "")[:500]
            return n
        return None

    def post(self, slot: str, now: dt.datetime | None = None, llm=None) -> str | None:
        now = now or kyiv_now()
        x = self.ctx()
        if slot == "intro":
            return self.intro()
        facts = self.facts(x, now)
        if slot.startswith("news"):
            used = {r[0].split(":", 1)[1] for r in self.conn.execute("SELECT slot FROM channel_posts WHERE slot LIKE 'news:%'").fetchall()}
            n = self.pick_news(x, used)
            if not n:
                return None
            f2 = {"date": facts["date"], "item": n, "btc": facts["prices"].get("BTC"), "market_stress": facts.get("market_stress"), "btc_zones": facts.get("btc_zones")}
            txt = self.write(slot, f2, llm) or self.template(slot, f2)
            self._last_news_id = n["id"]
            src = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt"}.get((n.get("source") or "").lower(), n.get("source") or "источник")
            return txt + f"\n\nИсточник: <a href=\"{n['link']}\">{src}</a>"
        if slot == "evening":
            btc = self.coin(x["terminal"], "BTC")
            facts["movers"] = self.movers_block(x)
            if btc:
                facts["scenario_verdict"] = self.evening_verdict(btc, x)
        txt = self.write(slot, facts, llm) or self.template(slot, facts)
        return txt

    def intro(self) -> str:
        return ("<b>CRYPTO NOVA — запуск AI-платформы для крипторынка</b>\n\n"
                "Мы запускаем систему, которая смотрит на рынок круглые сутки и делает то, на что у трейдера обычно нет ни времени, ни нервов:\n\n"
                "— читает <b>зоны</b>, где цена разворачивалась снова и снова, и ждёт её там, а не бежит вдогонку;\n"
                "— видит <b>стакан</b>: стены заявок, стакан под шкалой цен, живую битву покупателей и продавцов;\n"
                "— следит за <b>китами</b> Hyperliquid — кто и во что заходит крупно;\n"
                "— строит <b>сценарии A/B</b> с вероятностями и каждый вечер честно сверяет их с рынком;\n"
                "— ведёт три <b>демо-стола</b> с разным риском и три спот-портфеля — от консервативного до высокорискового, с планом докупок и фиксаций;\n"
                "— умеет выставлять сделки на биржу <b>автоматически</b> — ключи живут только у владельца, доступ закрыт биометрией;\n"
                "— разговаривает: голосовой ассистент встречает докладом, отвечает на вопросы и ведёт этот канал сам.\n\n"
                "Здесь будут: утренний обзор рынка, разбор главных новостей — что случилось и кто теперь вынужден покупать или продавать, — и итоги дня с проверкой прогноза. Каждую пятницу — недельный разбор со статистикой: сколько сценариев сбылось, сколько нет.\n\n"
                "Платформа сейчас в закрытом тестировании. Скоро откроем доступ — следите за каналом. Вопросы: @cryptonova_core_ai_bot")

    def evening_verdict(self, btc: dict, x: dict) -> str:
        price = btc.get("price")
        row = self.conn.execute("SELECT text FROM channel_posts WHERE day=? AND slot='morning'", (kyiv_now().strftime("%Y-%m-%d"),)).fetchone()
        if not row:
            return f"утреннего сценария сегодня не было; закрываем день на {_fmt(price)}"
        m = re.search(r"A[^\d]{0,40}(\d+)%[^\d]{0,40}к (сопротивлению|поддержке) ([\d\s]+(?:[.,]\d+)?)", row[0])
        if not m or price is None:
            return f"закрываем день на {_fmt(price)}"
        tgt = float(m.group(3).replace(" ", "").replace(",", "."))
        up = m.group(2) == "сопротивлению"
        hit = (price >= tgt) if up else (price <= tgt)
        return (f"утром ставили сценарий A ({m.group(1)}%) — {'к сопротивлению' if up else 'к поддержке'} {_fmt(tgt)}; закрываем день на {_fmt(price)}: "
                f"{'цель достигнута, сценарий отработал' if hit else 'цель не достигнута, сценарий не сработал или ещё в работе'}")

    def already(self, day: str, slot: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM channel_posts WHERE day=? AND slot=?", (day, slot)).fetchone())

    def record(self, day: str, slot: str, text: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO channel_posts VALUES(?,?,?,?)", (day, slot, int(time.time() * 1000), text))
        self.conn.commit()

    def chart(self, x: dict, slot: str) -> str | None:
        """PNG for the post (morning/evening): BTC wave, zones, A/B. None if Pillow is missing or data is thin."""
        try:
            from . import postchart
            btc = self.coin(x["terminal"], "BTC")
            if not btc:
                return None
            mb = next((t for t in x["markets"].get("top", []) if t.get("symbol") == "BTC"), None)
            title = f"BTC/USDT · 1 час · {'утро' if slot == 'morning' else 'вечер' if slot == 'evening' else 'день'} {kyiv_now():%d.%m}"
            return postchart.render(btc, (x["calibration"].get("open") or {}).get("BTC"), x["markets"].get("fng"),
                                    os.path.join(self.out, "post_chart.png"), title=title, chg24=mb.get("chg24") if mb else None)
        except Exception:  # noqa: BLE001
            return None

    def run(self, send_fn, llm=None, now: dt.datetime | None = None, force_slot: str | None = None, photo_fn=None) -> dict:
        now = now or kyiv_now()
        day = now.strftime("%Y-%m-%d")
        slot = force_slot or slot_for(now)
        sent = 0; texts = []
        # one-off posts controlled from the repo (config/channel_once.json): {"intro": "v2"} sends the intro once per version
        try:
            once = json.load(open(os.path.join(os.path.dirname(self.out) or ".", "config", "channel_once.json")))
        except Exception:  # noqa: BLE001
            once = {}
        for k, ver in once.items():
            if k == "intro" and not self.already("once", f"intro:{ver}"):
                t = self.intro()
                if send_fn(t):
                    self.record("once", f"intro:{ver}", t); sent += 1; texts.append(("intro", t))
        if not slot:
            return {"slot": None, "sent": bool(sent), "n": sent, "why": "outside posting hours"}
        if not force_slot and self.already(day, slot):
            return {"slot": slot, "sent": bool(sent), "n": sent, "why": "already posted"}
        self._last_news_id = None
        t = self.post(slot, now, llm)
        if not t:
            return {"slot": slot, "sent": bool(sent), "n": sent, "why": "nothing worth posting"}
        if photo_fn and slot in ("morning", "evening"):
            png = self.chart(self.ctx(), slot)
            if png:
                try:
                    photo_fn(png, t.split("\n\n")[0])
                except Exception:  # noqa: BLE001
                    pass
        if send_fn(t):
            self.record(day, slot, t); sent += 1; texts.append((slot, t))
            if self._last_news_id:
                self.record(day, f"news:{self._last_news_id}", t)
        return {"slot": slot, "sent": bool(sent), "n": sent, "text": t}
