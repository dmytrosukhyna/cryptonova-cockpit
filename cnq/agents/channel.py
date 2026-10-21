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

# 20.10 (owner: «итоги дня приходят очень рано, в 8 вечера ещё ничего не понятно … хотя бы в 9:30 по Киеву»): the evening slot
# opens at 21:30 Kyiv; values are (start hour, end hour) and SLOT_START_MIN holds a start minute when it is not :00.
SLOTS = {"morning": (8, 10), "news1": (11, 13), "news2": (15, 17), "evening": (21, 23)}
SLOT_START_MIN = {"evening": 30}
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


def latin_heavy(s: str | None) -> bool:
    """True when a text is mostly English: Latin letters in ordinary words (tickers like BTC, ETF are fine) outweigh Cyrillic."""
    if not s:
        return False
    s = re.sub(r"<a [^>]*>.*?</a>|https?://\S+", "", s)
    lat = sum(len(w) for w in re.findall(r"\b[A-Za-z][a-z]{2,}\b", s))
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    return lat > 12 and lat > 0.25 * (lat + cyr)


def russify(items: list[dict], llm, keys=("title", "mechanism", "confirms_if", "summary")) -> list[dict]:
    """Translate English fields of news items into Russian with one LLM call; items that stay English are dropped."""
    need = [(i, k) for i, it in enumerate(items) for k in keys if latin_heavy(it.get(k))]
    if need and llm is not None:
        src = {f"{i}.{k}": items[i][k] for i, k in need}
        try:
            res = llm.json("Переведи значения на русский язык: просто, понятно, без потери смысла. Имена людей, названия компаний и тикеры оставь как есть. "
                           "Верни JSON-объект с теми же ключами.", json.dumps(src, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            res = None
        if isinstance(res, dict):
            for key, val in res.items():
                try:
                    i, k = key.split(".", 1); i = int(i)
                except ValueError:
                    continue
                if isinstance(val, str) and val.strip() and not latin_heavy(val) and 0 <= i < len(items):
                    items[i][k] = val.strip()
    out = []
    for it in items:
        if latin_heavy(it.get("title")) or latin_heavy(it.get("mechanism")):
            continue
        for k in keys:
            if latin_heavy(it.get(k)):
                it[k] = ""
        out.append(it)
    return out


def kyiv_now(now_ms: int | None = None) -> dt.datetime:
    t = (now_ms or int(time.time() * 1000)) / 1000
    return dt.datetime.fromtimestamp(t, KYIV)


def slot_for(now: dt.datetime) -> str | None:
    for k, (a, b) in SLOTS.items():
        if (a, SLOT_START_MIN.get(k, 0)) <= (now.hour, now.minute) and now.hour < b:
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
                "research": _load(o, "research.json") or {}, "portfolio": _load(o, "portfolio.json") or {}}

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
            pass   # 16.10: no fear & greed in posts
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

    @staticmethod
    def events(now: dt.datetime) -> list[str]:
        """Scheduled macro events in the next 7 days (FOMC / CPI / NFP) as ready post lines."""
        try:
            from cnq.agents import macro_calendar
            return macro_calendar.post_lines(int(now.timestamp() * 1000), 7)
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _deals(n: int) -> str:
        # 21.10: «9 сделкок» went out — the stem was glued to the ending of «сделок»
        return f"{n} " + ("сделку" if n % 10 == 1 and n % 100 != 11 else "сделки" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "сделок")

    @staticmethod
    def system_line(facts: dict) -> str:
        """«Вывод CRYPTO NOVA» for people: what happened on the market, what we did, what we expect.
        18.10: owner — no rule names. 19.10: owner — «вывод должен говорить, что по результатам пролива вошли в сделку, рынок ожидает
        небольшого роста, после пролива произошли откупы … без лишней технической истории»; and never «без пролива сделок не будет»:
        the drop-buyback is one of several strategies, not the main rule."""
        ap = facts.get("autopilot") or {}
        share = facts.get("stress_share")
        lt = ((facts.get("btc_regime") or {}).get("long_term") or "")
        other = int(facts.get("other_open") or 0)
        if ap.get("n"):
            coins = ", ".join(ap.get("coins", [])[:4])
            s = (f"После резкого снижения покупатели начали выкупать падение. Мы вошли в {Channel._deals(ap['n'])} на отскок ({coins}) "
                 "и ждём небольшого роста в ближайшие сутки, риск ограничен.")
        elif share is not None and share >= 0.4:
            s = (f"Рынок под давлением: {round(share * 100)}% монет за сутки потеряли больше 3%. Ждём, когда продавцы выдохнутся — "
                 "после таких распродаж обычно бывает отскок, к нему и готовимся.")
        elif share is not None and share >= 0.2:
            s = "Рынок нервный, но без паники. На падении не спешим покупать — ждём, где цена найдёт опору."
        else:
            s = "Рынок спокойный, без сильных движений. Вдогонку не входим — ждём понятной точки входа."
        if other:
            s += f" В работе ещё {Channel._deals(other)} по другим нашим стратегиям."
        if "медвеж" in lt:
            s += " Биткоин ниже своей долгосрочной средней, поэтому крупных покупок не делаем."
        elif "бычий" in lt:
            s += " Долгосрочный тренд биткоина вверх — основу портфеля держим."
        return s

    def facts(self, x: dict, now: dt.datetime) -> dict:
        m = x["markets"]; g = m.get("global") or {}; f = m.get("fng") or {}
        top = {t["symbol"]: t for t in m.get("top", []) if t.get("symbol") in ("BTC", "ETH", "SOL")}
        term = x["terminal"]; btc = self.coin(term, "BTC")
        r = (btc or {}).get("regime") or {}
        stress = x["paper"].get("stress")
        # paper.json → stress is a float (share of coins down >3 %/24h); the old code expected a dict, so «спокойный» was always shown
        share = float(stress) if isinstance(stress, (int, float)) else None
        lvl = stress.get("level") if isinstance(stress, dict) else (None if share is None else 0 if share < 0.2 else 1 if share < 0.4 else 2 if share < 0.7 else 3)
        out = {"date": self.date_ru(now),
               "prices": {k: {"usd": _fmt(v["price"]), "chg24": f"{v.get('chg24', 0):+.1f}%", "chg7": f"{v.get('chg7', 0):+.1f}%"} for k, v in top.items()},
               "market_cap_trln": f"{g['total_mcap'] / 1e12:.2f}" if g.get("total_mcap") else None,
               "market_cap_chg24": f"{g.get('mcap_chg24', 0):+.1f}%" if g.get("mcap_chg24") is not None else None,
               "btc_dominance": f"{g.get('btc_dom', 0):.1f}%" if g.get("btc_dom") else None,
               "fear_greed": None,   # 16.10: owner asked to drop market-mood indices from posts and panel
               "events": self.events(now),
               "btc_regime": {"long_term": "бычий: цена выше 200-дневной средней" if r.get("long") == "BULL" else "медвежий: цена ниже 200-дневной средней",
                              "mid_term": {"LONG": "тренд вверх", "SHORT": "тренд вниз", "UP_WAIT": "готовится к росту, ждём пробоя", "DOWN_WAIT": "готовится к снижению, ждём пробоя"}.get(r.get("mid"), "без выраженного тренда"),
                              "impulse": {"UP": "импульс вверх", "DOWN": "импульс вниз", "FLAT": "боковик"}.get(r.get("short"), "")} if r else None,
               "market_stress": {0: "спокойный", 1: "нервный", 2: "распродажа — большинство монет падает", 3: "паника — падает почти всё"}.get(lvl, "спокойный") if lvl is not None else None}
        out["stress_share"] = share
        bal = ((x["paper"].get("profiles") or {}).get("bal") or {})
        opn = bal.get("open") or []
        out["autopilot"] = {"n": len(opn), "coins": [o["symbol"] for o in opn if isinstance(o, dict) and o.get("symbol")]} if opn else None
        prof = x["paper"].get("profiles") or {}
        out["other_open"] = sum(len((prof.get(k) or {}).get("open") or []) for k in ("imp", "exp", "lst", "own"))
        out["system_line"] = self.system_line(out)
        if btc:
            sup, res = self.zones(btc)
            out["btc_zones"] = {"support": f"{_fmt(sup['lo'])}–{_fmt(sup['hi'])}" if sup else None, "resistance": f"{_fmt(res['lo'])}–{_fmt(res['hi'])}" if res else None}
            op = (x["calibration"].get("open") or {}).get("BTC")
            # 21.10 owner (option 2): the channel shows the levels for the day, no A/B scenarios and no probabilities — the
            # direction forecast hits 45% (worse than a coin, public forecast table); the levels themselves are facts
            if op and (op.get("up") is not None or op.get("dn") is not None):
                out["btc_levels"] = {"resistance": _fmt(op["up"]) if op.get("up") is not None else None,
                                     "support": _fmt(op["dn"]) if op.get("dn") is not None else None}
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
            try:
                out["day_result"] = self.day_result(x)
            except Exception:  # noqa: BLE001
                out["day_result"] = None
            out["demo_desks"] = {names[k]: f"{(prof[k].get('pnl_pct') or 0):+.2f}%, позиций в работе {len(prof[k].get('open') or [])}" for k in ("cons", "bal", "aggr") if prof.get(k)}
        st = x["calibration"].get("stats") or {}
        if st.get("n"):
            out["forecast_track_record"] = f"проверено {st['n']} прогнозов, сценарий A сбылся в {round(st['hit_rate'] * 100)}%"
        return out

    @staticmethod
    def _usd(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}".replace(",", " ")

    def day_result(self, x: dict) -> dict | None:
        """What the AI strategies earned or lost over the last 24 h: futures (autopilot «норма», incl. open positions) and spot."""
        now = time.time() * 1000
        def change(hist, cur, get):
            if not hist or cur is None:
                return None
            base = None
            for h in hist:
                if h.get("time", 0) <= now - 86400_000:
                    base = get(h)
            base = get(hist[0]) if base is None else base
            return cur - base
        bal = (x["paper"].get("profiles") or {}).get("bal") or {}
        fut_now = (bal.get("equity") or 0) + sum(o.get("open_pnl_usd") or 0 for o in bal.get("open") or []) if bal else None
        fut = change(bal.get("equity_history"), fut_now, lambda h: h.get("equity"))
        pf = x.get("portfolio") or {}
        sp = (pf.get("portfolios") or {}).get("mod") or pf.get("portfolio") or {}
        spot = change(sp.get("history"), 10_000 * (sp.get("index") or 100) / 100, lambda h: 10_000 * (h.get("v") if h.get("v") is not None else h.get("equity", 100)) / 100) if sp else None
        if fut is None and spot is None:
            return None
        tot = (fut or 0) + (spot or 0)
        closed = [t for t in bal.get("closed") or [] if (t.get("closed") or 0) > now - 86400_000]
        return {"futures": self._usd(fut) if fut is not None else None, "spot": self._usd(spot) if spot is not None else None, "total": self._usd(tot),
                "futures_trades_closed": len(closed), "total_sign": "заработали" if tot >= 0 else "потеряли"}

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
             "Используй ТОЛЬКО факты и числа из JSON, копируй числа как они даны, ничего не выдумывай и не добавляй событий, которых нет в данных. Незнакомые читателю названия (монеты, протоколы, компании, люди) поясняй в скобках одной фразой. Никогда не пиши служебных слов вроде «шапка», «заголовок», «раздел». "
             "Оформление — красиво и легко читать: короткие абзацы по 1–2 предложения, между блоками пустая строка; перечисления — с новой строки через «— »; "
             "главные слова и цифры (цена, вывод, уровень) выделяй <b>жирным</b> — только тегом <b>, никаких звёздочек * и markdown. Никакого сплошного текста. "
             "Язык — как другу, который не разбирается в крипте: без жаргона, любой термин (поддержка, ликвидации, открытый интерес, фандинг) поясняй простыми словами. "
             "Если какого-то факта нет — просто не упоминай его. Весь текст — только на русском: английские заголовки и фразы из данных обязательно переводи (имена, компании и тикеры можно латиницей). Разметка: только <b>…</b> для заголовков разделов. Без эмодзи, без хэштегов, без ссылок, без подписи в конце.")

    PROMPTS = {
        "morning": "Напиши утренний пост. Структура строго:\n<b>Доброе утро, дорогие подписчики!</b>\n<b>Утренний обзор рынка · {date}</b>\n\n<b>Рынок:</b> три строки через «— »: «— <b>Биткоин</b> $… (±…%)», то же для ETH и SOL; без индекса страха и жадности.\n\n<b>Аналитика:</b> один-два абзаца: что сейчас двигает рынок (новости из news с их механизмом, если они есть; если нет — режим и потоки), как ведёт себя биткоин относительно альтов.\n\n<b>Ключевые факторы:</b> 2–3 коротких пункта, каждый с новой строки через «— ».\n\n<b>Уровни на день:</b> две строки из btc_levels: «— Сопротивление: …» и «— Поддержка: …» (числа <b>жирным</b>); без вероятностей, без слов «сценарий», «основной», «запасной»; одной фразой — что будет значить пробой каждого уровня.\n\n<b>Вывод CRYPTO NOVA:</b> 2–3 предложения: защитный или рабочий режим, где ждём разворота (зоны), что делает система — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет; спот — частями. Не пиши «входим от сильных уровней» — система так не работает. Если в данных есть events — добавь блок <b>Календарь</b> с этими строками как есть. Пост идёт подписью к картинке, поэтому строго до 800 символов (картинка уже показывает цены, зоны и уровни — не дублируй всё подряд).",
        "news": "Напиши пост об одной новости. Влияние новости на цену — это возможность, а не факт: пиши «может», «если», а не «фиксирует», «происходит», «массовые продажи». Что сейчас на самом деле с ценой — только из market_now; что ждёт система — только из system_view (сценарии A и B). Если новость негативная, а цена не падает и system_view за рост — так и скажи честно: «на цене это пока не видно». Если новость мелкая для рынка (арест, частное мошенничество, небольшая сумма) — прямо напиши, что на биткоин она почти не влияет. Структура строго:\nПервая строка — твой заголовок: короткий и сильный, до 8 слов, по сути новости, в <b>…</b>; никаких слов «шапка», «заголовок» и т.п. Если в новости есть малоизвестная монета, проект, компания или человек — поясни одной фразой в скобках, что это (например, «HEX (токен проекта Ричарда Харта)»), чтобы понял новичок.\n\n<b>Что случилось:</b> 2–3 предложения по summary.\n\n<b>Почему это важно для рынка:</b> 2–4 предложения — механизм: кто вынужден покупать или продавать, на какие монеты влияет, что это меняет в настроении; упомяни текущую цену биткоина из данных.\n\n<b>Вывод CRYPTO NOVA:</b> 2 предложения: главный риск или шанс и что делает система — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет. Длина до 1200 символов. Ссылку НЕ вставляй — её добавят после.",
        "evening": "Напиши вечерний пост. Структура строго:\n<b>Итоги дня · {date}</b>\n\n<b>Главные события:</b> 2–3 пункта с новой строки «1.», «2.», «3.» — из news (с механизмом одной фразой); если новостей нет — главные движения рынка.\n\n<b>Как вёл себя рынок:</b> пункты через «— »: биткоин, эфир, солана с ценой и изменением за сутки (цены <b>жирным</b>), одна строка — кто вырос и кто упал сильнее всех.\n\n<b>Итог дня по счетам:</b> три строки из day_result: «— Фьючерсы (автопилот): …», «— Спот: …», «— Всего за день: …» (суммы со знаком, <b>жирным</b>); если day_result нет — раздел пропусти.\n\n<b>Что дальше:</b> одна строка «— Уровни на завтра: сопротивление …, поддержка …» из btc_levels (числа <b>жирным</b>), без вероятностей и без слов «сценарий». Затем строки через «— »: «— Краткосрочно:» (по стрессу рынка и импульсу), «Среднесрочно:» (по тренду и зонам), «Долгосрочно:» (по режиму 200-дневной) — без новых чисел, только из данных.\n\n<b>Вывод CRYPTO NOVA:</b> 2–3 предложения: что произошло на рынке, что система делает завтра (не пиши о несбывшихся прогнозах) — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет. Уровни на завтра называй только из btc_levels; зоны из btc_zones — это дальние ориентиры, упоминай их только как «если пробьём …, следующая зона …», не как ожидание на завтра. Пост идёт подписью к картинке, поэтому строго до 800 символов: каждый раздел — 1–2 коротких предложения, «Главные события» — максимум 2 пункта."
    }

    # ------------------------------------------------------------------ posts
    @staticmethod
    def _nums(txt: str) -> set:
        return {re.sub(r"[\s,]", "", n).replace(".", ",").rstrip(",") for n in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", txt)}

    def guard(self, out: str, facts: dict) -> bool:
        """Every number in the post must exist in the facts (digits-only comparison; '81 004' == '81004', '2.9' == '2,9')."""
        if not out or len(out) < 200 or any(latin_heavy(line) for line in out.splitlines()):
            return False
        pool = self._nums(json.dumps(facts, ensure_ascii=False))
        pool |= {str(n) for n in range(0, 32)} | {str(dt.datetime.now().year)}
        bad = [n for n in self._nums(out) if n and n not in pool and n.split(",")[0] not in pool]
        return not bad

    REVIEW = ("Ты — выпускающий редактор и фактчекер крипто-канала. Проверь пост перед публикацией строго по данным JSON. Найди ошибки:\n"
              "1) факт, число или уровень, которого нет в данных или который искажён (например, зона, поданная как уровень на завтра, хотя в btc_levels другие числа);\n"
              "2) противоречия внутри поста (например, «сценария не было» и дальше «сценарий A не реализовался»);\n"
              "3) выводы, которые не следуют из данных, обещания и уверенность там, где в данных вероятность;\n"
              "4) английский текст (кроме имён, компаний, тикеров), служебные слова вроде «шапка», пустые или повторяющиеся разделы;\n"
              "5) неверное направление: позитив/негатив новости или сценария перепутаны.\n"
              'Верни JSON: {"ok": true|false, "problems": ["коротко, что не так и как исправить", ...]}. Мелкие стилистические замечания не считаются ошибками.')

    def lint(self, out: str, facts: dict) -> list[str]:
        """Deterministic pre-publish checks (no LLM)."""
        probs = []
        if re.search(r"шапка|заголовок:", out, re.I):
            probs.append("в тексте служебное слово «шапка/заголовок»")
        bad = [ln for ln in out.splitlines() if latin_heavy(ln)]
        if bad:
            probs.append("строки на английском — переведи на русский: " + " | ".join(b[:80] for b in bad[:3]))
        if re.search(r"сценари\w*\s+[AАBВ]\b|\b[AB]\s*\(?\d{1,3}\s*%", out, re.I):
            probs.append("сценарии A/B с вероятностями убраны (21.10) — пиши только уровни: сопротивление и поддержка из btc_levels")
        v = str(facts.get("scenario_verdict") or "")
        if v.startswith("утреннего сценария сегодня не было") and re.search(r"сценари\w* A", out):
            probs.append("утреннего сценария не было — не упоминай «сценарий A» в итогах")
        # only SECTION headers count («<b>Главные события:</b>» or a bold line on its own) — bold VALUES may repeat
        # (17.10: «Фьючерсы: <b>+$0</b>» and «Всего: <b>+$0</b>» blocked the evening post, the template fallback included)
        heads = []
        for m in re.finditer(r"<b>([^<]{3,60})</b>", out):
            ls, le = out.rfind("\n", 0, m.start()) + 1, out.find("\n", m.end())
            line = out[ls:le if le >= 0 else len(out)].strip()
            if m.group(1).strip().endswith(":") or line == m.group(0):
                heads.append(m.group(1).strip())
        if len(heads) != len(set(heads)):
            probs.append("повторяющиеся разделы")
        if re.search(r"\*\*|(^|\s)\*[^*\n]+\*", out):
            probs.append("markdown со звёздочками — для жирного используй только <b>…</b>")
        if any(len(re.sub(r"<[^>]+>", "", ln)) > 320 for ln in out.splitlines()):
            probs.append("слишком длинный абзац — разбей на короткие абзацы или пункты через «— »")
        # the post must not claim a fall the data does not show (18.10: «Система фиксирует падение» at BTC +0.4 % / 24 h)
        try:
            chg = float(str(((facts.get("btc") or (facts.get("prices") or {}).get("BTC") or {}).get("chg24") or "0")).replace("%", "").replace("+", "").replace(",", "."))
        except ValueError:
            chg = 0.0
        if chg > -1.0 and re.search(r"фиксиру\w*\s+(паден|спад|снижен)|массов\w*\s+(продаж|распродаж)|рынок\s+(падает|обвалив)|давлени\w*\s+ощуща", out, re.I):
            probs.append(f"в данных нет падения (биткоин {chg:+.1f}% за сутки) — не пиши, что система фиксирует падение или идут массовые продажи; влияние новости — только «может»")
        if chg < 1.0 and re.search(r"фиксиру\w*\s+(рост|подъ[её]м)|массов\w*\s+покуп", out, re.I):
            probs.append(f"в данных нет роста (биткоин {chg:+.1f}% за сутки) — не пиши, что система фиксирует рост")
        if facts.get("_max_len") and len(re.sub(r"<[^>]+>", "", out)) > facts["_max_len"]:
            probs.append(f"слишком длинно: сократи до {facts['_max_len'] - 70} символов")
        return probs

    def review(self, out: str, facts: dict, llm) -> list[str]:
        try:
            res = llm.json(self.REVIEW, json.dumps({"post": out, "data": facts}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            return []
        if isinstance(res, dict) and res.get("ok") is False:
            return [str(p) for p in (res.get("problems") or [])][:6] or ["рецензент отклонил пост без пояснения"]
        return []

    def write(self, slot: str, facts: dict, llm, headline_hint: str = "") -> str | None:
        """Draft → number guard → deterministic lint → LLM fact-check; problems go back to the writer (up to 3 drafts).
        Nothing is published unless a draft passes all checks — otherwise the caller falls back to the data-only template."""
        if llm is None:
            return None
        prompt = self.PROMPTS["news" if slot.startswith("news") else slot].replace("{date}", facts.get("date", "")).replace("{headline}", headline_hint or "")
        notes = ""
        self.last_review = []
        for attempt in range(3):
            try:
                out = llm.complete(self.VOICE, prompt + notes + "\n\nДанные (JSON):\n" + json.dumps(facts, ensure_ascii=False), temperature=0.4 if attempt == 0 else 0.2)
            except Exception as e:  # noqa: BLE001
                self.last_review.append({"attempt": attempt + 1, "error": str(e)[:200]})
                return None
            out = re.sub(r"<b>\s*Шапка\s*</b>\s*\n?", "", (out or "").strip())
            probs = [] if self.guard(out, facts) else ["в тексте есть числа, которых нет в данных — используй только числа из JSON"]
            probs += self.lint(out, facts)
            if not probs:
                probs = self.review(out, facts, llm)
            self.last_review.append({"attempt": attempt + 1, "problems": probs})
            if not probs:
                return out
            notes = "\n\nПредыдущий черновик отклонён проверкой. Исправь:\n- " + "\n- ".join(probs)
        return None

    def template(self, slot: str, facts: dict) -> str:
        """Plain fallback when the LLM is unavailable: same structure, data only."""
        p = facts.get("prices", {})
        pl = ", ".join(f"{k} ${v['usd']} ({v['chg24']})" for k, v in p.items())
        fg = facts.get("fear_greed")
        if slot == "morning":
            names = {"BTC": "Биткоин", "ETH": "Эфир", "SOL": "Солана"}
            body = [f"<b>Доброе утро, дорогие подписчики!</b>\nУтренний обзор рынка · {facts['date']}"]
            mk = [f"— {names.get(k, k)}: <b>${v['usd']}</b> ({v['chg24']} за сутки)" for k, v in p.items()]
            if fg:
                mk.append(f"— Индекс страха и жадности: <b>{fg['value']}</b> — {fg['label']}")
            body.append("<b>Рынок</b>\n" + "\n".join(mk))
            kf = [n["title"] for n in facts.get("news", [])[:2]]
            if facts.get("btc_regime"):
                kf.append(f"Биткоин: {facts['btc_regime']['mid_term']}; в долгую — {facts['btc_regime']['long_term']}")
            body.append("<b>Что важно сегодня</b>\n" + "\n".join("— " + k for k in kf[:3]))
            lv = facts.get("btc_levels") or {}; z = facts.get("btc_zones") or {}
            plan = []
            if lv.get("resistance"):
                plan.append(f"— Сопротивление: <b>{lv['resistance']}</b>")
            if lv.get("support"):
                plan.append(f"— Поддержка: <b>{lv['support']}</b>")
            if z.get("support") or z.get("resistance"):
                plan.append("— Сильные уровни: " + ", ".join(x for x in (f"поддержка {z['support']}" if z.get("support") else "", f"сопротивление {z['resistance']}" if z.get("resistance") else "") if x))
            if plan:
                body.append("<b>Уровни на день</b>\n" + "\n".join(plan))
            if facts.get("events"):
                body.append("<b>Календарь</b>\n" + "\n".join(facts["events"][:2]))
            body.append("<b>Вывод CRYPTO NOVA</b>\n" + self.system_line(facts))
            return "\n\n".join(body)
        if slot.startswith("news"):
            n = facts["item"]
            parts = [f"<b>{n['title']}</b>"]
            if n.get("summary"):
                parts.append(f"<b>Что случилось:</b> {n['summary']}")
            if n.get("mechanism"):
                parts.append(f"<b>Как это может повлиять на рынок:</b> {n['mechanism']}")
            parts.append("<b>Вывод CRYPTO NOVA:</b> не торгуем вдогонку — ждём реакцию цены у ближайшей зоны." + (f" Подтвердит: {n['confirms_if']}." if n.get("confirms_if") else ""))
            return "\n\n".join(parts)
        names = {"BTC": "Биткоин", "ETH": "Эфир", "SOL": "Солана"}
        body = [f"<b>Итоги дня · {facts['date']}</b>"]
        ev = [n["title"] for n in facts.get("news", [])[:2]]
        body.append("<b>Главное за день</b>\n" + ("\n".join(f"— {t}" for t in ev) if ev else "— Сильных новостей не было — рынок двигался от уровня к уровню."))
        mk = [f"— {names.get(k, k)}: <b>${v['usd']}</b> ({v['chg24']} за сутки)" for k, v in p.items()]
        if fg:
            mk.append(f"— Индекс страха и жадности: <b>{fg['value']}</b> — {fg['label']}")
        body.append("<b>Как вёл себя рынок</b>\n" + "\n".join(mk))
        if facts.get("scenario_verdict") and not str(facts["scenario_verdict"]).startswith("утреннего сценария сегодня не было"):
            body.append(f"<b>Утренний сценарий</b>\n{facts['scenario_verdict'][0].upper() + facts['scenario_verdict'][1:]}.")
        dr = facts.get("day_result")
        if dr:
            body.append("<b>Итог дня по счетам</b>\n" + "\n".join(x for x in (f"— Фьючерсы (автопилот): <b>{dr['futures']}</b>" if dr.get("futures") else "", f"— Спот: <b>{dr['spot']}</b>" if dr.get("spot") else "", f"— Всего за день: <b>{dr['total']}</b>") if x))
        r = facts.get("btc_regime") or {}
        lv = facts.get("btc_levels") or {}
        fc = ["— Рынок сейчас: " + (facts.get("market_stress") or "спокойный") + (f", {r.get('impulse')}" if r.get("impulse") else "") + "."]
        if lv.get("resistance") or lv.get("support"):
            fc.append("— Уровни на завтра: " + ", ".join(x for x in (f"сопротивление <b>{lv['resistance']}</b>" if lv.get("resistance") else "",
                                                                    f"поддержка <b>{lv['support']}</b>" if lv.get("support") else "") if x))
        if r.get("long_term"):
            fc.append(f"— В долгую: {r['long_term']}.")
        fc += (facts.get("events") or [])[:2]
        body.append("<b>Что дальше</b>\n" + "\n".join(fc))
        body.append("<b>Вывод CRYPTO NOVA</b>\n" + self.system_line(facts))
        return "\n\n".join(body)

    @staticmethod
    def minor_enforcement(n: dict, it: dict) -> bool:
        """Arrests, sentences and scams below $500M rarely move bitcoin — not a market post unless rated 4/5+."""
        txt = " ".join(str(v or "") for v in (n.get("title"), it.get("title"), it.get("title_en"), n.get("summary"))).lower()
        if not re.search(r"арест|задерж|приговор|осужд|мошенни|экстрадир|arrest|sentenc|scam|fraud|extradit|charged|laundering", txt):
            return False
        try:
            mag = int(str(n.get("strength") or "0").split("/")[0])
        except ValueError:
            mag = 0
        amounts = [float(a.replace(",", ".")) * (1000 if u.lower().startswith(("b", "млрд")) else 1) for a, u in
                   re.findall(r"\$\s?(\d+(?:[.,]\d+)?)\s?(m|b|млн|млрд)", txt)]
        return mag < 4 and (not amounts or max(amounts) < 500)

    def pick_news(self, x: dict, exclude: set) -> dict | None:
        for n in self.impacts(x, 8):
            if n["id"] in exclude or not n.get("link"):
                continue
            if time.time() * 1000 - (n.get("created") or 0) > 10 * 3600_000:
                continue
            it = next((i for i in (x["decision"].get("news") or {}).get("items", []) if i.get("id") == n["id"]), {})
            n["summary"] = (it.get("summary") or "")[:500]
            if self.minor_enforcement(n, it):
                continue
            return n
        return None

    def post(self, slot: str, now: dt.datetime | None = None, llm=None) -> str | None:
        now = now or kyiv_now()
        x = self.ctx()
        if slot == "intro":
            return self.intro()
        facts = self.facts(x, now)
        facts["news"] = russify(facts.get("news") or [], llm)
        if slot.startswith("news"):
            used = {r[0].split(":", 1)[1] for r in self.conn.execute("SELECT slot FROM channel_posts WHERE slot LIKE 'news:%'").fetchall()}
            n = self.pick_news(x, used)
            if n:
                n = (russify([n], llm) or [None])[0]
            if not n:
                return None
            b_ = facts["prices"].get("BTC") or {}
            f2 = {"date": facts["date"], "item": n, "btc": b_, "market_stress": facts.get("market_stress"), "btc_zones": facts.get("btc_zones"),
                  # 18.10: the post said «система фиксирует падение» while BTC was +0.4 % and our forecast leaned up — the writer
                  # now sees what the market and the system actually show, and must not present the news' possible effect as a fact
                  "market_now": f"биткоин {b_.get('chg24', '?')} за сутки, {b_.get('chg7', '?')} за неделю; рынок: {facts.get('market_stress') or 'спокойный'}",
                  "levels": facts.get("btc_levels"), "btc_regime": facts.get("btc_regime")}
            txt = self.write(slot, f2, llm) or self.template(slot, f2)
            self._last_news_id = n["id"]
            src = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt"}.get((n.get("source") or "").lower(), n.get("source") or "источник")
            return txt + f"\n\nИсточник: <a href=\"{n['link']}\">{src}</a>"
        if slot == "evening":
            btc = self.coin(x["terminal"], "BTC")
            facts["movers"] = self.movers_block(x)
            if btc:
                # 21.10 owner: «про несбывшиеся сценарии писать не нужно» — the verdict goes into the post only when the morning
                # scenario reached its target; a miss stays in the forecast table (scorecard), not in the channel
                v = self.evening_verdict(btc, x, now.strftime("%Y-%m-%d"))
                if "цель достигнута" in v:
                    facts["scenario_verdict"] = v
        facts["_max_len"] = 1000                 # morning / evening go out as a photo caption (Telegram limit 1024)
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
                "Здесь будут: утренний обзор рынка, разбор главных новостей дня и вечерние итоги с проверкой прогноза. Каждую пятницу — недельный разбор со статистикой: сколько сценариев сбылось, сколько нет.\n\nМы анализируем рынок полностью математически, без человеческого фактора: зоны, стакан, потоки, позиции крупных игроков, новости и их последствия — и даём максимально чёткую картину рынка. Так чётко её не даст никто.\n\n"
                "Платформа сейчас в закрытом тестировании. Скоро откроем доступ — следите за каналом. Вопросы: @cryptonova_core_ai_bot")

    def evening_verdict(self, btc: dict, x: dict, day: str | None = None) -> str:
        price = btc.get("price")
        row = self.conn.execute("SELECT text FROM channel_posts WHERE day=? AND slot='morning'", (day or kyiv_now().strftime("%Y-%m-%d"),)).fetchone()
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
            now = kyiv_now()
            title = "Биткоин (BTC/USDT) · часовой график за 4 дня"
            when = f"{'утренний обзор' if slot == 'morning' else 'итоги дня' if slot == 'evening' else 'обзор'} · {now.day} {self.MONTHS[now.month - 1]} {now.year}, {now:%H:%M} по Киеву"
            op = (x["calibration"].get("open") or {}).get("BTC")
            concl = []
            if op and (op.get("up") is not None or op.get("dn") is not None):
                if op.get("up") is not None:
                    concl.append(f"Сопротивление: {_fmt(op['up'])}.")
                if op.get("dn") is not None:
                    concl.append(f"Поддержка: {_fmt(op['dn'])}.")
                concl.append("Работаем от зон: вход только после реакции цены, стоп заранее.")
            return postchart.render(btc, op, None, os.path.join(self.out, "post_chart.png"), title=title,
                                    chg24=mb.get("chg24") if mb else None, when=when, conclusion=concl)
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
            if k.startswith("repost:") and not self.already("once", f"{k}:{ver}"):
                # {"repost:2026-10-15:evening": "1"} — forget that slot once so it is written and checked again
                _, d_, s_ = k.split(":", 2)
                self.conn.execute("DELETE FROM channel_posts WHERE day=? AND slot=?", (d_, s_))
                self.record("once", f"{k}:{ver}", "")
            if k == "intro" and not self.already("once", f"intro:{ver}"):
                t = self.intro()
                if send_fn(t):
                    self.record("once", f"intro:{ver}", t); sent += 1; texts.append(("intro", t))
        if not slot:
            return {"slot": None, "sent": bool(sent), "n": sent, "why": "outside posting hours"}
        if not force_slot and self.already(day, slot):
            return {"slot": slot, "sent": bool(sent), "n": sent, "why": "already posted"}
        self._last_news_id = None
        self.last_review = []
        t = self.post(slot, now, llm)
        if not t:
            return {"slot": slot, "sent": bool(sent), "n": sent, "why": "nothing worth posting"}
        final = self.lint(re.sub(r"Источник: <a [^>]*>.*?</a>", "", t), {})
        if final:
            self._log(day, slot, {"blocked": final, "review": self.last_review})
            return {"slot": slot, "sent": bool(sent), "n": sent, "why": "blocked by pre-publish check", "problems": final, "review": self.last_review}
        # picture + text as ONE message: Telegram allows a photo caption up to 1024 characters, so morning/evening posts
        # are written compact (the chart carries prices and scenarios); a longer text falls back to photo + text
        if photo_fn and slot in ("morning", "evening"):
            png = self.chart(self.ctx(), slot)
            if png and len(re.sub(r"<[^>]+>", "", t)) <= 1024:
                try:
                    if photo_fn(png, t):
                        self.record(day, slot, t); sent += 1; texts.append((slot, t))
                        self._log(day, slot, {"sent": True, "photo": True, "review": self.last_review})
                        return {"slot": slot, "sent": True, "n": sent, "text": t, "photo": True, "review": self.last_review}
                except Exception:  # noqa: BLE001
                    pass
            elif png:
                try:
                    photo_fn(png, t.split("\n\n")[0])
                except Exception:  # noqa: BLE001
                    pass
        if send_fn(t):
            self.record(day, slot, t); sent += 1; texts.append((slot, t))
            if self._last_news_id:
                self.record(day, f"news:{self._last_news_id}", t)
        self._log(day, slot, {"sent": bool(sent), "review": self.last_review})
        return {"slot": slot, "sent": bool(sent), "n": sent, "text": t, "review": self.last_review}

    def _log(self, day: str, slot: str, obj: dict) -> None:
        """Pre-publish check history (committed with agents.sqlite) — shows what the fact-checker rejected and why."""
        self.conn.execute("CREATE TABLE IF NOT EXISTS channel_review(day TEXT, slot TEXT, time INTEGER, log TEXT)")
        self.conn.execute("INSERT INTO channel_review VALUES(?,?,?,?)", (day, slot, int(time.time() * 1000), json.dumps(obj, ensure_ascii=False)))
        self.conn.commit()
