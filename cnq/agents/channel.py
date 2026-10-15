"""Channel editor — writes the Telegram channel posts for @crypto_nova_news from the JSON the other departments
already produce (web/*.json). Three slots a day in Kyiv time: morning (situation + scenario), midday (causes →
consequences from the news desk + whales), evening (what happened vs the morning scenario, demo desks, tomorrow).

Rules: every number comes from a JSON file; no links; a post is sent at most once per slot per day (table
channel_posts in data/agents.sqlite); an optional LLM may polish the wording, but the result is accepted only if
every number of the draft survives verbatim — otherwise the draft is sent as is.
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

SLOTS = {"morning": (8, 10), "midday": (14, 16), "evening": (20, 22)}
SIGN = "\n\n<i>CRYPTO NOVA · система читает рынок по данным: зоны, стакан, потоки, киты. Не финансовый совет.</i>"

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

    # ------------------------------------------------------------------ posts
    def post(self, slot: str, now: dt.datetime | None = None) -> str:
        now = now or kyiv_now()
        x = self.ctx()
        day = now.strftime("%d.%m")
        term = x["terminal"]
        btc = self.coin(term, "BTC")
        if slot == "intro":
            return ("<b>CRYPTO NOVA · ЗАПУСК</b>\n\n"
                    "Этот канал ведёт торговая система CRYPTO NOVA. Три раза в день она публикует то, что видит сама — по данным, а не по слухам:\n\n"
                    "<b>Утро</b> — обстановка, режим рынка, сценарии A/B на день и зоны, где цена может развернуться.\n"
                    "<b>День</b> — причины и следствия: какие новости двигают рынок и через какой механизм (кто вынужден покупать или продавать).\n"
                    "<b>Вечер</b> — что сбылось, что нет, результат демо-столов и план на завтра.\n\n"
                    "Каждую пятницу — недельный разбор с честной статистикой: сколько прогнозов сбылось, сколько нет.\n\n"
                    "Система не торгует «вдогонку»: сделки только от сильных зон, стоп всегда известен заранее, в панике — не покупаем."
                    + SIGN)
        if slot == "morning":
            blocks = [f"<b>CRYPTO NOVA · УТРО</b> <i>{day}</i>", self.market_block(x), self.regime_block(x)]
            if btc:
                blocks.append("<b>Сценарий дня по BTC.</b> " + self.scenario(btc, x["calibration"]))
            blocks.append(self.watch_block(x))
            wb = self.whales_block(x)
            if wb:
                blocks.append(wb)
            blocks.append("<b>Что делаем.</b> Сделки только от зон, стоп за уровнем, цель от 1.5R. Если рынок падает весь сразу — не ловим дно и не шортим вдогонку: ждём откупа на объёме у сильной поддержки.")
        elif slot == "midday":
            blocks = [f"<b>CRYPTO NOVA · ДЕНЬ · причины и следствия</b> <i>{day}</i>", self.news_block(x)]
            if btc:
                sup, res = self.zones(btc)
                mb = next((t for t in x["markets"].get("top", []) if t.get("symbol") == "BTC"), None)
                blocks.append(f"Биткоин сейчас {_fmt(btc['price'])} ({_pct((mb or btc).get('chg24'))} за сутки). " +
                              (f"Ближайшая поддержка {_fmt(sup['lo'])}–{_fmt(sup['hi'])}, " if sup else "") +
                              (f"сопротивление {_fmt(res['lo'])}–{_fmt(res['hi'])}." if res else ""))
            wb = self.whales_block(x)
            if wb:
                blocks.append(wb)
        elif slot == "evening":
            blocks = [f"<b>CRYPTO NOVA · ВЕЧЕР · итог дня</b> <i>{day}</i>", self.market_block(x), self.movers_block(x)]
            if btc:
                blocks.append("<b>Сценарий сработал?</b> " + self.evening_verdict(btc, x))
            db = self.desks_block(x)
            if db:
                blocks.append(db)
            blocks.append(self.calibration_block(x))
            blocks.append("<b>Завтра.</b> " + self.watch_block(x, 2).replace("Где смотрим:\n", ""))
        else:
            raise ValueError(slot)
        text = "\n\n".join(b for b in blocks if b)
        return text + SIGN

    def evening_verdict(self, btc: dict, x: dict) -> str:
        price = btc.get("price")
        row = self.conn.execute("SELECT text FROM channel_posts WHERE day=? AND slot='morning'", (kyiv_now().strftime("%Y-%m-%d"),)).fetchone()
        if not row:
            return f"Утреннего сценария сегодня не было; закрываем день на {_fmt(price)}."
        m = re.search(r"Сценарий A \((\d+)%\): к (сопротивлению|поддержке) ([\d\s.,]+)\.", row[0])
        if not m or price is None:
            return f"Закрываем день на {_fmt(price)}."
        tgt = float(m.group(3).replace(" ", "").replace(",", "."))
        up = m.group(2) == "сопротивлению"
        hit = (price >= tgt) if up else (price <= tgt)
        return (f"Утром ставили A ({m.group(1)}%) — {'к сопротивлению' if up else 'к поддержке'} {_fmt(tgt)}. Закрываем день на {_fmt(price)}: "
                f"{'цель достигнута — сценарий отработал' if hit else 'цели нет, сценарий ещё в работе или снят'}. Фиксируем честно — вечером проверяем то, что обещали утром.")

    # ------------------------------------------------------------------ polish + send
    def polish(self, draft: str, llm) -> str:
        if llm is None:
            return draft
        try:
            out = llm.complete(
                "Ты редактор канала CRYPTO NOVA. Перепиши пост короче и живее, уверенным тоном эксперта, для людей с общим пониманием рынка. "
                "Сохрани ВСЕ числа, тикеры, HTML-теги <b>, <i> и структуру абзацев. Не добавляй фактов и чисел, которых нет в тексте. Без эмодзи, без ссылок, без приветствий.",
                draft)
        except Exception:  # noqa: BLE001
            return draft
        if not out or len(out) < len(draft) * 0.4:
            return draft
        nums = set(re.findall(r"\d[\d\s.,]*\d|\d", draft))
        if any(n not in out for n in nums if len(n.replace(" ", "")) >= 2):
            return draft
        return out.strip()

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
        if not slot:
            return {"slot": None, "sent": False, "why": "outside posting hours"}
        if not force_slot and self.already(day, slot):
            return {"slot": slot, "sent": False, "why": "already posted"}
        first = not self.conn.execute("SELECT 1 FROM channel_posts LIMIT 1").fetchone()
        texts = []
        if first and slot != "intro":
            texts.append(("intro", self.post("intro", now)))
        texts.append((slot, self.polish(self.post(slot, now), llm)))
        sent = 0
        for s, t in texts:
            if photo_fn and s in ("morning", "evening"):
                png = self.chart(self.ctx(), s)
                if png:
                    try:
                        photo_fn(png, t.split("\n\n")[0])
                    except Exception:  # noqa: BLE001
                        pass
            if send_fn(t):
                self.record(day if s != "intro" else "intro", s, t)
                sent += 1
        return {"slot": slot, "sent": bool(sent), "n": sent, "text": texts[-1][1]}
