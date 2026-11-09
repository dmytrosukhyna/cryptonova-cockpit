"""Channel editor — writes the Telegram channel posts for @crypto_nova_news from the JSON the other departments
already produce (web/*.json). Four slots a day in Kyiv time, in the format Dmytro ran by hand before:
  08–10  Утренний обзор рынка   — цены, аналитика одним-двумя абзацами, ключевые факторы, вывод CRYPTO NOVA AI (+ картинка)
  11–13  Новость                — одна главная новость: своя шапка, что случилось, почему важно, вывод, ссылка на источник
  15–17  Новость                — вторая новость дня (если есть сильная)
  20–22  Итоги дня              — главные события, как вёл себя рынок, прогноз кратко/средне/долгосрочно, вывод (+ картинка)
  Сделки реального счёта — только в вечерних «Итогах дня» (отдельный пост на сделку выключен владельцем 04.11).
  05.11: утренний и вечерний посты несут короткий блок «Система CRYPTO NOVA AI» (позиции реального счёта, итог, одна строка о рынке —
  cnq/agents/whale_digest.py). Владелец 05.11 09:52: людям — только «система CRYPTO NOVA AI» и результат, никогда метод (киты, копии,
  имена трейдеров). Тихие часы: 23:00–08:00 по Киеву ничего не уходит.
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



def _long_text(r: dict) -> str:
    from ..terminal.report import long_text_ru
    return long_text_ru(r)

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
                "calibration": _load(o, "calibration.json") or {}, "paper": _load(o, "paper.json") or {}, "real": _load(o, "public_real.json") or {},
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
        from ..terminal.report import long_text_ru
        long_ = ("долгосрок бычий — " if r.get("long") == "BULL" else "долгосрок не бычий — ") + long_text_ru(r)
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
        """«Вывод CRYPTO NOVA AI» for people: what happened on the market, what the system does, what we expect.
        05.11 (owner 09:52: «везде должна быть система CRYPTO NOVA AI … людям нужен только результат»): futures are run by the
        system CRYPTO NOVA AI — never the method (whales, copying, traders); the spot part is the core BTC / ETH / SOL. No rule names,
        no retired desks («откуп падений», «автопилот»), no promises."""
        ap = facts.get("autopilot") or {}
        share = facts.get("stress_share")
        lt = ((facts.get("btc_regime") or {}).get("long_term") or "")
        n_wh = int(facts.get("whales_following") or 0)
        n = int(ap.get("n") or 0)
        if share is not None and share >= 0.4:
            s = f"Рынок под давлением: {round(share * 100)}% монет за сутки потеряли больше 3%."
        elif share is not None and share >= 0.2:
            s = "Рынок нервный, но без паники."
        else:
            s = "Рынок спокойный, без сильных движений."
        if n and n_wh:                         # the «Система CRYPTO NOVA AI» section right above lists the open positions
            s += " Фьючерсы ведёт система CRYPTO NOVA AI, у каждой сделки стоп на бирже."
        elif n:
            s += f" Фьючерсы: система CRYPTO NOVA AI ведёт {Channel._deals(n)}, у каждой стоп на бирже."
        else:
            s += " Фьючерсы: открытых сделок сейчас нет — система ждёт свой следующий сигнал."
        if lt and (facts.get("btc_regime") or {}).get("long") != "BULL":
            # 10.11 (owner): the exact indicator of the spot rule (terminal regime «long»: the average + 90 days), never «долгосрочная средняя»
            s += f" Спот — ядро BTC, ETH и SOL; у биткоина {lt.split(': ', 1)[-1]}, а по правилу спота в таком режиме новые монеты не берём — только ядро."
        else:
            s += " Спот — ядро BTC, ETH и SOL держим в долгую."
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
               "btc_regime": {"long_term": ("бычий: " if r.get("long") == "BULL" else "медвежий: " if r.get("long") == "BEAR" else "смешанный: ") + _long_text(r),
                              "long": r.get("long"),
                              "mid_term": {"LONG": "тренд вверх", "SHORT": "тренд вниз", "UP_WAIT": "готовится к росту, ждём пробоя", "DOWN_WAIT": "готовится к снижению, ждём пробоя"}.get(r.get("mid"), "без выраженного тренда"),
                              "impulse": {"UP": "импульс вверх", "DOWN": "импульс вниз", "FLAT": "боковик"}.get(r.get("short"), "")} if r else None,
               "market_stress": {0: "спокойный", 1: "нервный", 2: "распродажа — большинство монет падает", 3: "паника — падает почти всё"}.get(lvl, "спокойный") if lvl is not None else None}
        out["stress_share"] = share
        # 31.10: the channel speaks only about the REAL CRYPTO NOVA account on OKX (public_real.json), never the demo desks
        real = x.get("real") or {}
        opn = [o for o in real.get("futures") or [] if isinstance(o, dict)]
        out["autopilot"] = {"n": len(opn), "coins": [str(o.get("inst") or "").replace("-USDT-SWAP", "") for o in opn]} if opn else None
        out["other_open"] = 0
        try:   # 05.11: is the «Система CRYPTO NOVA AI» block there (it lists the positions)? — no names ever reach the writer
            from . import whale_digest
            out["whales_following"] = 1 if whale_digest.block(self.out, slot="morning") else 0
        except Exception:  # noqa: BLE001
            out["whales_following"] = 0
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
        # 05.11: the Hyperliquid leaderboard summary is no longer in the posts — «Киты» now means the traders we copy (whale_digest)
        out["news"] = self.impacts(x, 4)
        try:
            out["day_result"] = self.day_result(x)
        except Exception:  # noqa: BLE001
            out["day_result"] = None
        st = x["calibration"].get("stats") or {}
        if st.get("n"):
            out["forecast_track_record"] = f"проверено {st['n']} прогнозов, сценарий A сбылся в {round(st['hit_rate'] * 100)}%"
        return out

    @staticmethod
    def _usd(v: float) -> str:
        """$ with a sign; cents under $10 so a real −$0.65 is never shown as «−$0» (31.10)"""
        a = abs(v)
        body = f"{a:,.2f}" if a < 10 else f"{a:,.0f}"
        return ("+" if v >= 0 else "−") + "$" + body.replace(",", " ")

    @staticmethod
    def _usd2(v: float) -> str:
        """a trade's result with cents: «+$10.87», «−$8.87»"""
        return ("+" if v >= 0 else "−") + "$" + f"{abs(v):,.2f}".replace(",", " ")

    @staticmethod
    def day_start_ms(now_ms: float | None = None) -> float:
        """00:00 Kyiv of the day `now_ms` falls in"""
        d = kyiv_now(int(now_ms if now_ms is not None else time.time() * 1000))
        return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000

    def day_closed(self, x: dict, now_ms: float | None = None) -> list[dict]:
        """09.11 (owner): the real account's futures trades closed TODAY (Kyiv calendar day; partial closes are part of their trade) —
        the one source for every count and sum of the evening post"""
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        d0 = self.day_start_ms(now_ms)
        out = []
        for t in (x.get("real") or {}).get("closed") or []:
            if isinstance(t, dict) and not t.get("partial") and d0 <= (t.get("closed") or 0) <= now_ms:
                out.append({"coin": re.sub(r"^1000", "", str(t.get("inst") or "").replace("-USDT-SWAP", "").replace("-USDT", "")),
                            "side": "лонг" if str(t.get("side", "long")).lower() == "long" else "шорт",
                            "pnl": float(t.get("pnl_usd") or 0.0), "closed": t.get("closed")})
        return out

    def day_result(self, x: dict) -> dict | None:
        """31.10 (owner: «в канал ушли страшные минуса по демо-столу — не соответствует действительности»): the day's result
        is the REAL CRYPTO NOVA AI account on OKX (public_real.json — the same numbers as the app's public card), never a demo desk.
        Futures and spot parts over the last 24 h come from the account's curves; nothing is estimated."""
        r = x.get("real") or {}
        if not r or r.get("stale") or r.get("equity_usd") is None:
            return None
        now = time.time() * 1000
        d0 = self.day_start_ms(now)              # 09.11 (owner): ONE period for the whole evening post — the Kyiv calendar day
        def change(curve):
            if not curve:
                return None
            base = None
            for t, v in curve:
                if t <= d0:
                    base = v
            if base is None:
                base = curve[0][1]
            return curve[-1][1] - base
        tot = change(r.get("curve"))
        fut = change(r.get("fut_curve"))
        if tot is None:
            tot = r.get("today_usd")
        if tot is None:
            return None
        spot = tot - fut if fut is not None else None
        closed = self.day_closed(x, now)
        cl_usd = round(sum(c["pnl"] for c in closed), 2)
        line = None
        if fut is not None:                      # 09.11: «Фьючерсы: +$18» next to «закрыто … −$8.87» read as a contradiction
            line = f"<b>{self._usd(fut)}</b>" + (f" (закрытые сделки {self._usd2(cl_usd)}, открытые позиции {self._usd2(fut - cl_usd)})" if closed else
                                                 " (открытые позиции, закрытых сделок сегодня нет)")
        return {"futures": self._usd(fut) if fut is not None else None, "futures_line": line, "futures_closed": cl_usd, "spot": self._usd(spot) if spot is not None else None, "total": self._usd(tot),
                "futures_trades_closed": len(closed), "total_sign": "заработали" if tot >= 0 else "потеряли",
                "equity": self._usd(r["equity_usd"]).lstrip("+"), "since_pnl": self._usd(r.get("pnl_usd") or 0), "since_pct": r.get("pnl_pct")}

    # ------------------------------------------------------------------ trade results (owner 21.10)
    # «в новости наши, в канал, нужно ещё добавлять результаты сделок … выделять жирным дату … скриншот»: every closed trade of
    # the autopilot («сбалансированная», the same account the evening «Итог дня» counts) gets its own short post with a
    # picture card, and the evening post lists the day's closed trades. Losses go out exactly like wins.
    WHY_RU = {"A": "после гэпа цена вернулась к уровню — вошли на возврат",
              "B": "после каскада ликвидаций покупатели выкупили падение — вошли на отскок",
              "D": "фьючерс стоил дешевле спота — вошли на сближение цен",
              "E": "после резкой распродажи покупатели начали выкупать падение — вошли на отскок",
              "F": "после массового закрытия позиций рынок разгрузился — вошли на отскок"}
    EXIT_RU = {"TIME": "по времени", "STOP": "по стопу", "BREAKEVEN": "в ноль по стопу", "TARGET": "по цели", "TRAIL": "подтянутым стопом"}
    TRADE_HOURS = (8, 23)          # Kyiv: a trade closed at night is posted in the morning (it stays eligible for TRADE_WINDOW)
    TRADE_WINDOW = 14 * 3600_000   # only trades closed in the last 14 h — no flood of old trades on the first run
    # owner 04.11 22:51: «в канал не надо сбрасывать отчёты об открытых или закрытых сделках отдельными новостями … люди спят …
    # сделки по $1–2 неинтересны» — no separate post per trade; the day's trades stay in the evening «Итог дня» only.
    TRADE_POSTS = False

    def trade_view(self, t: dict) -> dict:
        entry = t.get("entry"); qty = t.get("qty") or 0; pnl = t.get("pnl_usd")
        long_ = str(t.get("side", "LONG")).upper() == "LONG"
        exit_ = None; pct = None
        if entry and qty and pnl is not None:
            exit_ = entry + (pnl / qty if long_ else -pnl / qty)
            pct = pnl / (qty * entry) * 100
        net = t.get("net_usd") if t.get("net_usd") is not None else pnl
        closed = t.get("closed")
        if closed and t.get("exit_kind") == "TIME":
            closed += 900_000                    # a time exit fills at the close of the 15-minute bar stamped by its open
        cl = kyiv_now(closed) if closed else None
        return {"id": t.get("id"), "symbol": t.get("symbol"), "side": t.get("side"), "entry": entry, "exit": exit_, "pct": pct,
                "net_usd": net or 0.0, "opened": t.get("opened"), "closed": closed,
                "exit_ru": self.EXIT_RU.get(t.get("exit_kind"), "по правилу"), "why_ru": self.WHY_RU.get(t.get("grade")),
                "date": cl.strftime("%d.%m.%Y") if cl else "", "date_ru": self.date_ru(cl) if cl else ""}

    def real_trade_view(self, t: dict) -> dict:
        """a closed futures trade of the real OKX account (public_real.json closed[]) in the card's shape"""
        closed = t.get("closed"); cl = kyiv_now(closed) if closed else None
        sym = str(t.get("inst") or "").replace("-USDT-SWAP", "").replace("-USDT", "")
        side = "LONG" if str(t.get("side", "long")).lower() == "long" else "SHORT"
        pnl = t.get("pnl_usd") or 0.0
        return {"id": f"{sym}:{closed}", "symbol": sym, "side": side, "entry": t.get("open_px"), "exit": t.get("close_px"),
                "pct": t.get("move_pct"), "net_usd": pnl, "opened": t.get("opened"), "closed": closed,
                "exit_ru": "частично" if t.get("partial") else "по правилу", "why_ru": None,
                "date": cl.strftime("%d.%m.%Y") if cl else "", "date_ru": self.date_ru(cl) if cl else ""}

    def closed_trades(self, x: dict, since_ms: float) -> list[dict]:
        r = x.get("real") or {}
        return [self.real_trade_view(t) for t in sorted(r.get("closed") or [], key=lambda t: t.get("closed") or 0)
                if isinstance(t, dict) and (t.get("closed") or 0) > since_ms and not t.get("partial")]

    @staticmethod
    def _deals_n(n: int) -> str:
        return f"{n} " + ("сделка" if n % 10 == 1 and n % 100 != 11 else "сделки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "сделок")

    def trades_block(self, x: dict, open_lines: bool = True, now_ms: float | None = None, budget: int = 330) -> str:
        """«Сделки за день» for the evening post. 09.11 (owner: «2 сделки в списке, а в системе — 14»): ALL of today's closed trades
        (Kyiv day, the same list day_result counts), compact, the wins then the losses, each sorted by size; what does not fit the
        caption becomes «и ещё N сделок (…)» so the parts always add up to the total line. Open positions only when the
        «Система CRYPTO NOVA AI» section is absent."""
        r = x.get("real") or {}
        cl = self.day_closed(x, now_ms)
        lines = []
        if cl:
            tot = sum(c["pnl"] for c in cl)
            lines.append(f"— Закрыто {self._deals_n(len(cl))}, итог <b>{self._usd2(tot)}</b>.")
            win = sorted([c for c in cl if c["pnl"] >= 0], key=lambda c: -c["pnl"])
            loss = sorted([c for c in cl if c["pnl"] < 0], key=lambda c: c["pnl"])
            item = lambda c: f"{c['coin']} {c['side']} {self._usd2(c['pnl'])}"
            keep = len(cl)
            while True:                          # drop the smallest results first until the list fits its budget
                rr = [c for pair in zip(win + [None] * len(loss), loss + [None] * len(win)) for c in pair if c is not None]
                ids = {id(c) for c in rr[:keep]}  # the best win, the worst loss, the next best, the next worst, …
                w = [item(c) for c in win if id(c) in ids]
                l_ = [item(c) for c in loss if id(c) in ids]
                body = ([f"— В плюсе: {', '.join(w)}."] if w else []) + ([f"— В минусе: {', '.join(l_)}."] if l_ else [])
                rest = [c for c in cl if id(c) not in ids]
                if rest:
                    body.append(f"— И ещё {self._deals_n(len(rest))} на {self._usd2(sum(c['pnl'] for c in rest))}.")
                if len(" ".join(body)) <= budget or keep <= 2:
                    break
                keep -= 1
            lines += body
        if open_lines:
            for o in [o for o in r.get("futures") or [] if isinstance(o, dict)][:5]:
                u = o.get("upnl_usd")
                sym = str(o.get("inst") or "").replace("-USDT-SWAP", "")
                side = "long" if str(o.get("side", "long")).lower() == "long" else "short"
                lines.append(f"— {sym} {'лонг' if side == 'long' else 'шорт'}: в работе, стоп на бирже"
                             + (f", сейчас {self._usd(u)}" if u is not None else ""))
        return ("<b>Сделки за день (реальный счёт OKX):</b>\n" + "\n".join(lines)) if lines else ""

    SRC = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt"}

    def sourced_events(self, txt: str, news: list[dict]) -> str:
        """09.11 (owner): «Главное за день» lists ONLY news-feed items (decision.json impacts) with their source — whatever the
        writer put in that section is replaced by the feed's own titles"""
        items = [n for n in news if n.get("title") and n.get("link") and n.get("source")][:2]
        sec = ("<b>Главное за день</b>\n" + "\n".join(f"— {n['title']} ({self.SRC.get(str(n['source']).lower(), n['source'])})" for n in items)
               if items else "<b>Главное за день</b>\n— Сильных новостей не было — рынок двигался от уровня к уровню.")
        parts = txt.split("\n\n")
        for i, p_ in enumerate(parts):
            if re.match(r"<b>Главн(ые события|ое за день)", p_):
                parts[i] = sec
                return "\n\n".join(parts)
        return "\n\n".join(parts[:1] + [sec] + parts[1:])

    @staticmethod
    def futures_line(txt: str, dr: dict | None) -> str:
        """the «— Фьючерсы: …» line of the day's result always says what is closed and what is still open (owner 09.11)"""
        if not dr or not dr.get("futures_line"):
            return txt
        return re.sub(r"(?m)^— ?Фьючерсы:.*$", lambda m: "— Фьючерсы: " + dr["futures_line"], txt)

    @staticmethod
    def shrink(text: str, limit: int = 1024, deep: bool = True) -> str:
        """Fit a morning/evening post into Telegram's photo caption: shorten section by section (calendar lines, extra news,
        extra trades, the long-term line) until the visible text is within the limit; nothing is reworded."""
        def visible(t: str) -> int:
            return len(re.sub(r"<[^>]+>", "", t))
        secs = text.split("\n\n")
        # 05.11: + «Система CRYPTO NOVA AI» (to 3 lines, then 2), the trades list a second time to 2 lines
        # 09.11: the trades section is never cut (its list already fits with «и ещё N сделок»; a cut list contradicted the total)
        caps = [("Главное за день", 1), ("Что дальше", 2), ("Календарь", 1), ("Что важно сегодня", 2),
                ("Уровни на день", 2), ("Система CRYPTO NOVA AI", 3), ("Система CRYPTO NOVA AI", 2)]
        if deep:                                  # only when a shorter trades list could not make the caption fit
            caps += [("Система CRYPTO NOVA AI", 1), ("Что дальше", 1)]
        for title, cap in caps:
            if visible("\n\n".join(secs)) <= limit:
                break
            for i, sec in enumerate(secs):
                if sec.startswith(f"<b>{title}"):
                    lines = sec.split("\n")
                    secs[i] = "\n".join(lines[: 1 + cap])
        out = "\n\n".join(secs)
        if visible(out) > limit:                      # last resort: drop the calendar / levels sections entirely
            secs = [x for x in secs if not x.startswith(("<b>Календарь", "<b>Уровни на день", "<b>Утренний сценарий"))]
            out = "\n\n".join(secs)
        return out

    @staticmethod
    def insert_block(text: str, block: str) -> str:
        if not block:
            return text
        for mark in ("\n\n<b>Что дальше", "\n\n<b>Вывод CRYPTO NOVA AI"):
            i = text.find(mark)
            if i >= 0:
                return text[:i] + "\n\n" + block + text[i:]
        return text + "\n\n" + block

    def trade_text(self, v: dict, bal: dict) -> str:
        long_ = str(v["side"]).upper() == "LONG"
        pc = f" ({v['pct']:+.1f}%)".replace("-", "−") if v.get("pct") is not None else ""
        op = kyiv_now(v["opened"]) if v.get("opened") else None
        cl = kyiv_now(v["closed"]) if v.get("closed") else None
        parts = [f"<b>Сделка закрыта · {v['date']}</b>",
                 f"<b>{v['symbol']}</b>, {'лонг — ставка на рост' if long_ else 'шорт — ставка на падение'}\n"
                 + (f"— Вход: <b>{_fmt(v['entry'])}</b>" + (f" ({op:%d.%m в %H:%M})" if op else "") + "\n" if v.get("entry") else "")
                 + (f"— Выход: <b>{_fmt(v['exit'])}</b>" + (f" ({cl:%d.%m в %H:%M}), {v['exit_ru']}" if cl else "") + "\n" if v.get("exit") else "")
                 + f"— Результат: <b>{self._usd(v['net_usd'])}{pc}</b> после комиссий"]
        if v.get("why_ru"):
            parts.append(f"<b>Почему вошли:</b> {v['why_ru'][0].upper() + v['why_ru'][1:]}.")
        st = bal.get("stats") or {}
        if st.get("trades"):
            w = round((st.get("winrate") or 0) * st["trades"])
            parts.append(f"<b>С начала счёта:</b> {self._deals(st['trades'])}, в плюсе {w}, итог <b>{self._usd(bal.get('pnl_usd') or 0)}</b>.")
        parts.append("Реальный счёт CRYPTO NOVA AI на OKX с 22.10.2026, время киевское. Прошлые результаты не гарантируют будущих, это не инвестиционный совет.")
        return "\n\n".join(parts)

    def trade_posts(self, send_fn, photo_fn=None, now: dt.datetime | None = None) -> list[tuple[str, str]]:
        """One post per newly closed autopilot trade (text + picture card); recorded as day='trade', slot='trade:<id>'."""
        now = now or kyiv_now()
        if not self.TRADE_POSTS or not (self.TRADE_HOURS[0] <= now.hour < self.TRADE_HOURS[1]):
            return []
        x = self.ctx()
        r = x.get("real") or {}
        if not r or r.get("stale"):
            return []
        closed_all = [t for t in r.get("closed") or [] if isinstance(t, dict) and not t.get("partial")]
        bal = {"stats": {"trades": len(closed_all), "winrate": (sum(1 for t in closed_all if (t.get("pnl_usd") or 0) > 0) / len(closed_all)) if closed_all else 0},
               "pnl_usd": r.get("fut_pnl_usd") or 0}
        out = []
        for v in self.closed_trades(x, now.timestamp() * 1000 - self.TRADE_WINDOW):
            key = f"trade:{v['id'] or (str(v['symbol']) + ':' + str(v['closed']))}"
            if self.already("trade", key):
                continue
            t = self.trade_text(v, bal)
            ok = False
            if photo_fn:
                try:
                    from . import postchart
                    coin = self.coin(x["terminal"], str(v["symbol"]))
                    png = postchart.trade_card(coin, v, os.path.join(self.out, "post_trade.png"), when="реальный счёт CRYPTO NOVA AI на OKX")
                    ok = bool(png) and bool(photo_fn(png, t))
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                ok = bool(send_fn(t))
            if ok:
                self.record("trade", key, t); out.append((key, t))
                self._log(now.strftime("%Y-%m-%d"), key, {"sent": True})
        return out

    def impacts(self, x: dict, n: int) -> list[dict]:
        d = x["decision"].get("news") or {}
        seen = set(); out = []
        for i in sorted(d.get("impacts", []), key=lambda i: (-(i.get("magnitude") or 0), -(i.get("created") or 0))):
            t = (i.get("title") or "").strip()
            if not i.get("source") or not i.get("link"):          # 09.11 (owner): no headline without its source
                continue
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
    VOICE = ("Ты — редактор Telegram-канала CRYPTO NOVA AI (AI-система оценки и анализа крипторынка). Пиши по-русски живо, дружелюбно и просто — "
             "так, чтобы понял подросток, но без упрощения смысла: называй причину и следствие (кто вынужден покупать или продавать и почему). "
             "Используй ТОЛЬКО факты и числа из JSON, копируй числа как они даны, ничего не выдумывай и не добавляй событий, которых нет в данных. Незнакомые читателю названия (монеты, протоколы, компании, люди) поясняй в скобках одной фразой. Никогда не пиши служебных слов вроде «шапка», «заголовок», «раздел». "
             "Оформление — красиво и легко читать: короткие абзацы по 1–2 предложения, между блоками пустая строка; перечисления — с новой строки через «— »; "
             "главные слова и цифры (цена, вывод, уровень) выделяй <b>жирным</b> — только тегом <b>, никаких звёздочек * и markdown. Никакого сплошного текста. "
             "Язык — как другу, который не разбирается в крипте: без жаргона, любой термин (поддержка, ликвидации, открытый интерес, фандинг) поясняй простыми словами. "
             "Если какого-то факта нет — просто не упоминай его. Весь текст — только на русском: английские заголовки и фразы из данных обязательно переводи (имена, компании и тикеры можно латиницей). Разметка: только <b>…</b> для заголовков разделов. Без эмодзи, без хэштегов, без ссылок, без подписи в конце. "
             "Разделы «Система CRYPTO NOVA AI» и «Сделки за день» добавят к посту после — сам их не пиши. Сделки ведёт система "
             "CRYPTO NOVA AI: никогда не пиши про китов, копирование, трейдеров и как устроена система; не упоминай «автопилот», "
             "демо-счета и названия стратегий.")

    PROMPTS = {
        "morning": "Напиши утренний пост. Структура строго:\n<b>Доброе утро, дорогие подписчики!</b>\n<b>Утренний обзор рынка · {date}</b>\n\n<b>Рынок:</b> три строки через «— »: «— <b>Биткоин</b> $… (±…%)», то же для ETH и SOL; без индекса страха и жадности.\n\n<b>Аналитика:</b> один-два абзаца: что сейчас двигает рынок (новости из news с их механизмом, если они есть; если нет — режим и потоки), как ведёт себя биткоин относительно альтов.\n\n<b>Ключевые факторы:</b> 2–3 коротких пункта, каждый с новой строки через «— ».\n\n<b>Уровни на день:</b> две строки из btc_levels: «— Сопротивление: …» и «— Поддержка: …» (числа <b>жирным</b>); без вероятностей, без слов «сценарий», «основной», «запасной»; одной фразой — что будет значить пробой каждого уровня.\n\n<b>Вывод CRYPTO NOVA AI:</b> 2–3 предложения: защитный или рабочий режим, где ждём разворота (зоны), что делает система — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет; спот — частями. Не пиши «входим от сильных уровней» — система так не работает. Если в данных есть events — добавь блок <b>Календарь</b> с этими строками как есть. Пост идёт подписью к картинке, поэтому строго до 800 символов (картинка уже показывает цены, зоны и сценарии с вероятностями — в тексте только уровни, не дублируй всё подряд).",
        "news": "Напиши пост об одной новости. Влияние новости на цену — это возможность, а не факт: пиши «может», «если», а не «фиксирует», «происходит», «массовые продажи». Что сейчас на самом деле с ценой — только из market_now; что ждёт система — только из system_view (сценарии A и B). Если новость негативная, а цена не падает и system_view за рост — так и скажи честно: «на цене это пока не видно». Если новость мелкая для рынка (арест, частное мошенничество, небольшая сумма) — прямо напиши, что на биткоин она почти не влияет. Структура строго:\nПервая строка — твой заголовок: короткий и сильный, до 8 слов, по сути новости, в <b>…</b>; никаких слов «шапка», «заголовок» и т.п. Если в новости есть малоизвестная монета, проект, компания или человек — поясни одной фразой в скобках, что это (например, «HEX (токен проекта Ричарда Харта)»), чтобы понял новичок.\n\n<b>Что случилось:</b> 2–3 предложения по summary.\n\n<b>Почему это важно для рынка:</b> 2–4 предложения — механизм: кто вынужден покупать или продавать, на какие монеты влияет, что это меняет в настроении; упомяни текущую цену биткоина из данных.\n\n<b>Вывод CRYPTO NOVA AI:</b> 2 предложения: главный риск или шанс и что делает система — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет. Длина до 1200 символов. Ссылку НЕ вставляй — её добавят после.",
        "evening": "Напиши вечерний пост. Структура строго:\n<b>Итоги дня · {date}</b>\n\n<b>Главные события:</b> 2–3 пункта с новой строки «1.», «2.», «3.» — из news (с механизмом одной фразой); если новостей нет — главные движения рынка.\n\n<b>Как вёл себя рынок:</b> пункты через «— »: биткоин, эфир, солана с ценой и изменением за сутки (цены <b>жирным</b>), одна строка — кто вырос и кто упал сильнее всех.\n\n<b>Итог дня по счетам:</b> три строки из day_result: «— Фьючерсы: …» (строку futures_line целиком: итог, закрытые сделки и открытые позиции), «— Спот: …», «— Всего за день: …» (суммы со знаком, <b>жирным</b>) — это реальный счёт CRYPTO NOVA AI на OKX; если day_result нет — раздел пропусти.\n\n<b>Что дальше:</b> одна строка «— Уровни на завтра: сопротивление …, поддержка …» из btc_levels (числа <b>жирным</b>), без вероятностей и без слов «сценарий». Затем строки через «— »: «— Краткосрочно:» (по стрессу рынка и импульсу), «Среднесрочно:» (по тренду и зонам), «Долгосрочно:» (по режиму 200-дневной) — без новых чисел, только из данных.\n\n<b>Вывод CRYPTO NOVA AI:</b> 2–3 предложения: что произошло на рынке, что система делает завтра (не пиши о несбывшихся прогнозах) — возьми фразу из system_line по смыслу. Пиши как аналитик: что произошло на рынке, что мы сделали, чего ждём. Не упоминай «автопилот», правила и условия входа, не пиши, что новых сделок не будет. Уровни на завтра называй только из btc_levels; зоны из btc_zones — это дальние ориентиры, упоминай их только как «если пробьём …, следующая зона …», не как ожидание на завтра. Пост идёт подписью к картинке, поэтому строго до 800 символов: каждый раздел — 1–2 коротких предложения, «Главные события» — максимум 2 пункта."
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
            body.append("<b>Вывод CRYPTO NOVA AI</b>\n" + self.system_line(facts))
            return "\n\n".join(body)
        if slot.startswith("news"):
            n = facts["item"]
            parts = [f"<b>{n['title']}</b>"]
            if n.get("summary"):
                parts.append(f"<b>Что случилось:</b> {n['summary']}")
            if n.get("mechanism"):
                parts.append(f"<b>Как это может повлиять на рынок:</b> {n['mechanism']}")
            # 05.11: no «ждём реакцию у зоны» — the system does not trade news or zones; futures copy the verified traders
            parts.append("<b>Вывод CRYPTO NOVA AI:</b> на новость не торгуем вдогонку — система CRYPTO NOVA AI ведёт свои сделки, у каждой стоп на бирже."
                         + (f" Подтвердит влияние новости: {n['confirms_if']}." if n.get("confirms_if") else ""))
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
            body.append("<b>Итог дня · реальный счёт CRYPTO NOVA AI на OKX</b>\n" + "\n".join(x for x in (("— Фьючерсы: " + (dr.get("futures_line") or f"<b>{dr['futures']}</b>")) if dr.get("futures") else "", f"— Спот: <b>{dr['spot']}</b>" if dr.get("spot") else "", f"— Всего за день: <b>{dr['total']}</b>") if x))
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
        body.append("<b>Вывод CRYPTO NOVA AI</b>\n" + self.system_line(facts))
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
                  "levels": facts.get("btc_levels"), "btc_regime": facts.get("btc_regime"),
                  "system_line": facts.get("system_line")}       # 05.11: the news prompt asks for it («что делает система»)
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
        try:                                     # 05.11: the short «Система CRYPTO NOVA AI» section (positions, the day's result, a market line)
            from . import whale_digest
            wblock = whale_digest.block(self.out, now.timestamp() * 1000, slot=slot, closed_line=slot != "evening")
        except Exception:  # noqa: BLE001
            wblock = ""
        # with «Система CRYPTO NOVA AI» (it lists the open positions) the trades list keeps only the day's closed trades
        tb = (lambda b: self.trades_block(x, open_lines=not wblock, now_ms=now.timestamp() * 1000, budget=b)) if slot == "evening" else (lambda b: "")
        block = "\n\n".join(b for b in (tb(330), wblock) if b)
        if block:                                # added as is after the text is written: exact numbers, room kept in the caption
            facts["_max_len"] = max(500, 1000 - len(re.sub(r"<[^>]+>", "", block)) - 2)
        txt = self.write(slot, facts, llm) or self.template(slot, facts)
        if slot != "evening":
            return self.insert_block(txt, block)
        txt = self.futures_line(self.sourced_events(txt, facts.get("news") or []), facts.get("day_result"))
        vis = lambda t: len(re.sub(r"<[^>]+>", "", t))
        for b in (330, 250, 180, 120, 60):      # 09.11: the caption fits by naming fewer trades («и ещё N сделок»), never by cutting the list
            out = self.insert_block(txt, "\n\n".join(z for z in (tb(b), wblock) if z))
            if vis(self.shrink(out, deep=False)) <= 1024:
                break
        return out

    APP_STORE = "https://apps.apple.com/gr/app/crypto-nova-ai/id6754893343"

    def intro(self) -> str:
        """05.11 (owner 09:52): the system CRYPTO NOVA AI works with a real account (futures and spot, re-balancing); the iOS app shows
        the market and how the system works; anyone can connect. No method, no Google Play (the Android account is under appeal)."""
        return ("<b>CRYPTO NOVA AI — система, которая работает с реальным счётом</b>\n\n"
                "Система CRYPTO NOVA AI круглые сутки ведёт реальный счёт на OKX: фьючерсы и спот, сама балансирует доли между ними. "
                "У каждой сделки стоп на бирже, результат публикуем честно — и плюсы, и минусы (статистика с 22.10.2026).\n\n"
                "В приложении CRYPTO NOVA AI видно всё: что происходит на рынке, какие позиции открыты и почему, как работает система "
                "и какой у неё результат.\n"
                "Подключиться может каждый: скачайте приложение и следуйте шагам в разделе «Подключить».\n"
                f"Приложение для iPhone и iPad — в App Store: <a href=\"{self.APP_STORE}\">CRYPTO NOVA AI</a>\n\n"
                "В канале: утренний обзор рынка, главные новости дня и вечерние итоги реального счёта. С 23:00 до 08:00 по Киеву постов нет.\n"
                "Прошлые результаты не гарантируют будущих, это не инвестиционный совет. Вопросы: @dmytrosukhyna")

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
            # 21.10 owner: the picture keeps scenarios A/B with probabilities; only the post text shows plain levels
            if op and op.get("p_up") is not None:
                pr = op["p_up"]; a_up = pr >= 0.5; pa = round((pr if a_up else 1 - pr) * 100)
                ta, tb = (op["up"], op["dn"]) if a_up else (op["dn"], op["up"])
                concl.append(f"Сценарий A ({pa}%): {'рост к сопротивлению' if a_up else 'снижение к поддержке'} {_fmt(ta)}.")
                concl.append(f"Сценарий B ({100 - pa}%): {'снижение к поддержке' if a_up else 'рост к сопротивлению'} {_fmt(tb)}.")
                concl.append("Работаем от зон: вход только после реакции цены, стоп заранее.")
            # 21.10 owner: «либо в режиме волна … придумай что и показывай» — morning and evening alternate the plain line
            # and the «Волна» look (smooth glowing price, 20/50-hour averages, volume wave), so the picture changes every post
            mode = "wave" if (now.toordinal() + (slot == "evening")) % 2 == 0 else "line"
            png = postchart.render(btc, op, None, os.path.join(self.out, "post_chart.png"), title=title,
                                   chg24=mb.get("chg24") if mb else None, when=when, conclusion=concl, mode=mode)
            if not png:
                return None
            # 21.10 owner: «более интересный график … разное совмещать» — the BTC chart on top, live tiles under it
            try:
                top = {t.get("symbol"): t for t in x["markets"].get("top", [])}
                rows = []
                if slot == "evening":
                    dr = self.day_result(x) or {}
                    for k, name in (("futures", "Фьючерсы"), ("spot", "Спот"), ("total", "Всего за день")):
                        if dr.get(k):
                            rows.append((name, dr[k], postchart.DOWN if str(dr[k]).startswith(("−", "-")) else postchart.UP))
                    rtitle = "Итог дня · реальный счёт OKX"
                else:
                    skip = {"BTC", "ETH", "SOL", "XAUT", "PAXG", "WBT", "LEO", "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC"}
                    movers = [t for t in x["markets"].get("top", [])[:60] if not t.get("stable") and not t.get("wrapped") and t.get("chg24") is not None
                              and (t.get("volume") or 0) > 5e7 and t.get("symbol") not in skip and str(t.get("symbol", "")).isalnum()]
                    movers.sort(key=lambda t: t["chg24"], reverse=True)
                    for t in movers[:2] + movers[-2:][::-1]:
                        rows.append((t["symbol"], f"{t['chg24']:+.1f}%", postchart.UP if t["chg24"] >= 0 else postchart.DOWN))
                    rtitle = "Лидеры и аутсайдеры суток"
                b = postchart.board(png, os.path.join(self.out, "post_board.png"),
                                    eth=self.coin(x["terminal"], "ETH"), sol=self.coin(x["terminal"], "SOL"),
                                    eth_chg=(top.get("ETH") or {}).get("chg24"), sol_chg=(top.get("SOL") or {}).get("chg24"),
                                    fng=x["markets"].get("fng"), result=rows, result_title=rtitle)
                return b or png
            except Exception:  # noqa: BLE001
                return png
        except Exception:  # noqa: BLE001
            return None

    def extra(self, x: dict, slot: str) -> str | None:
        """Second picture of the album: the market map in the morning, the six engines in the evening — the technology
        behind the post and why it matters to people. None when data is missing (the post then goes with one picture)."""
        try:
            from . import postchart
            now = kyiv_now()
            when = f"{'утренний обзор' if slot == 'morning' else 'итоги дня'} · {now.day} {self.MONTHS[now.month - 1]} {now.year}"
            if slot == "morning":
                return postchart.heatmap(x.get("markets") or {}, os.path.join(self.out, "post_map.png"), when=when)
            if slot == "evening":
                return postchart.engine_card(_load(self.out, "app_home.json") or {}, _load(self.out, "conveyor.json"), _load(self.out, "risk.json"),
                                             _load(self.out, "scorecard.json"), os.path.join(self.out, "post_engines.png"), when=when)
        except Exception:  # noqa: BLE001
            return None
        return None

    def run(self, send_fn, llm=None, now: dt.datetime | None = None, force_slot: str | None = None, photo_fn=None) -> dict:
        now = now or kyiv_now()
        day = now.strftime("%Y-%m-%d")
        slot = force_slot or slot_for(now)
        sent = 0; texts = []
        from . import whale_digest
        if whale_digest.quiet(now) and not force_slot:      # 23:00–08:00 Kyiv: nothing goes out (owner 04.11: «люди спят»)
            return {"slot": None, "sent": False, "n": 0, "why": "quiet hours 23:00–08:00 Kyiv"}
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
        try:
            for k_, t_ in self.trade_posts(send_fn, photo_fn, now):
                sent += 1; texts.append((k_, t_))
        except Exception:  # noqa: BLE001
            pass
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
            cx_ = self.ctx()
            png = self.chart(cx_, slot)
            ex = self.extra(cx_, slot) if png else None
            if png and len(re.sub(r"<[^>]+>", "", t)) > 1024:
                t = self.shrink(t)                   # 31.10 (owner): the picture and the text must be ONE message
            if png and len(re.sub(r"<[^>]+>", "", t)) <= 1024:
                try:
                    if photo_fn([png, ex] if ex else png, t):
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
