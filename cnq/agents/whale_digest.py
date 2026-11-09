"""The «Система CRYPTO NOVA AI» block of the public texts (internally: the «Киты» desk) (05.11.2026, owner: «вся система должна быть подключена к лучшим трейдерам мира … всё подтянуть,
адаптировать, привести в порядок»).

Since 04.11 the whole real futures budget copies the top-5 verified whales (Binance lead traders chosen by
cnq/agents/whale_study.py → web/whales_top.json «following»); spot is the core BTC/ETH/SOL of the real OKX account. This module is
the ONE place the channel, the weekly post and the voice brief take that story from, so they never drift apart:
  • following()   — who we copy now (names, source, 60-day record);
  • copies()      — the real account's futures (public_real.json) tagged with the whale behind each one (real_why.json);
  • funds_line()  — one calm line from the funds' reports (stock_whales.json: today's ARK trades, else the 13F consensus);
  • onchain_line()— one calm line from the smart wallets (smart_money.json: tokens in «накопление»);
  • block()       — the short «Киты» section for a post (HTML, ≤ ~380 visible characters).
Also the quiet hours of every public Telegram post: nothing goes out 23:00–08:00 Kyiv (quiet()).
Read-only: it reads web/*.json and never changes the whale desk. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=2))

QUIET = (23, 8)                     # no public posts from 23:00 to 08:00 Kyiv (owner 04.11: «люди спят»)
STATS_FROM = "22.10.2026"           # statistics of the real account are counted from here (owner)
RISK_PCT, LEV_CAP = 2, 5            # the whale desk on the real account: 2 % risk per trade, leverage capped at 5×
SRC_RU = {"bn": "Binance", "okx": "OKX", "hl": "Hyperliquid"}
SIDE_RU = {"long": "лонг", "short": "шорт"}


def kyiv(now_ms: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp((now_ms if now_ms is not None else time.time() * 1000) / 1000, KYIV)


def quiet(now: dt.datetime | None = None) -> bool:
    """True from 23:00 to 08:00 Kyiv — no public post goes out then (a post waits for the morning)."""
    h = (now.astimezone(KYIV) if now and now.tzinfo else (now or kyiv())).hour
    return h >= QUIET[0] or h < QUIET[1]


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _usd(v: float) -> str:
    a = abs(v)
    body = f"{a:,.2f}" if a < 10 else f"{a:,.0f}"
    return ("+" if v >= 0 else "−") + "$" + body.replace(",", " ")


def _coin(inst: str) -> str:
    return re.sub(r"^1000", "", str(inst or "").replace("-USDT-SWAP", "").replace("-USDT", ""))


def following(out: str) -> list[dict]:
    """[{name, src_ru, win, pf, decisions}] — the whales the desk copies now (whales_top.json «following»)."""
    w = _load(out, "whales_top.json")
    rows = []
    for f in w.get("following") or []:
        if not isinstance(f, dict):
            continue
        rows.append({"name": f.get("name") or f.get("short") or "", "addr": f.get("addr"),
                     "src_ru": SRC_RU.get(f.get("src") or "", f.get("src") or ""), "win": f.get("win"), "pf": f.get("pf"),
                     "decisions": f.get("decisions"), "pnl_60d": f.get("pnl_60d")})
    return [r for r in rows if r["name"]]


def _sig_coin_side(sig: str) -> tuple[str, str] | None:
    m = re.match(r"whales:W-([A-Z0-9]+)-([LS])-(\d+)", str(sig or ""))
    return (re.sub(r"^1000", "", m.group(1)), "long" if m.group(2) == "L" else "short") if m else None


def copies(out: str, now_ms: float | None = None, hours: int = 24) -> dict:
    """The real account's futures as whale copies: open ones (with the whale's name) and the ones closed in the last `hours`.
    A closed trade is a whale copy when real_why.json remembers a whale signal for the same coin and side opened before it closed."""
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    real = _load(out, "public_real.json")
    why = _load(out, "real_why.json")
    names = {f["addr"]: f["name"] for f in following(out)}
    memo = {**(why.get("memory") or {}), **(why.get("by_sig") or {})}

    def label(w: dict) -> str | None:
        """a name people can read: the trader's nickname, else «трейдер Binance / Hyperliquid» (never a raw id)"""
        nm = w.get("name") or names.get(w.get("addr")) or ""
        if nm and not re.match(r"^(bn:|okx:|0x)", nm):
            return nm
        src = str(w.get("source") or "")
        return "трейдера " + ("Hyperliquid" if "hyper" in src else "Binance" if "binance" in src else "OKX" if "okx" in src else "копитрейдинга")

    def whale_of(sig):
        w = ((memo.get(sig) or {}).get("whale") or {}) if sig else {}
        return label(w) if w else None

    opn = []
    for o in real.get("futures") or []:
        if not isinstance(o, dict):
            continue
        sig = o.get("sig") or ""
        is_whale = str(sig).startswith("whales:")
        w = whale_of(sig) if is_whale else None
        addr = (((memo.get(sig) or {}).get("whale") or {}).get("addr")) if is_whale else None
        opn.append({"coin": _coin(o.get("inst")), "side": str(o.get("side") or "long").lower(), "upnl": o.get("upnl_usd"),
                    "whale": w, "copy": is_whale, "in_top": bool(addr and addr in names)})
    keys = []
    for sig, v in memo.items():
        if str(sig).startswith("whales:") and isinstance(v, dict):
            cs = _sig_coin_side(sig)
            if cs:
                keys.append((cs, int(re.search(r"(\d+)$", sig).group(1)), label(v.get("whale") or {})))
    closed = []
    for t in real.get("closed") or []:
        if not isinstance(t, dict) or t.get("partial") or (t.get("closed") or 0) <= now_ms - hours * 3600_000:
            continue
        cs = (_coin(t.get("inst")), str(t.get("side") or "long").lower())
        hit = [k for k in keys if k[0] == cs and k[1] <= (t.get("closed") or 0)]
        closed.append({"coin": cs[0], "side": cs[1], "pnl": float(t.get("pnl_usd") or 0.0), "closed": t.get("closed"),
                       "copy": bool(hit), "whale": (max(hit, key=lambda k: k[1])[2] if hit else None)})
    cc = [c for c in closed if c["copy"]]
    return {"open": opn, "closed": closed, "copies_closed": cc, "copies_usd": round(sum(c["pnl"] for c in cc), 2),
            "open_copies": [o for o in opn if o["copy"]], "open_other": [o for o in opn if not o["copy"]]}


def desk(out: str) -> dict | None:
    """The whale desk's demo account (the same rules on $10 000, whales_top.json desk) — for the demo portfolio and the panel."""
    d = _load(out, "whales_top.json").get("desk") or {}
    if not d.get("start") or d.get("equity") is None:
        return None
    return {"name_ru": d.get("name_ru") or "Киты · копирование топ-5", "start": float(d["start"]), "equity": float(d["equity"]),
            "pnl_usd": float(d.get("pnl_usd") or (float(d["equity"]) - float(d["start"]))), "pnl_pct": d.get("pnl_pct"),
            "trades": int(d.get("trades") or 0), "wins": int(d.get("wins") or 0), "started": d.get("started"),
            "open": d.get("open") or [], "closed": d.get("closed") or [], "history": d.get("history") or []}


def funds_line(out: str, now_ms: float | None = None) -> str | None:
    """One calm line from the funds: today's ARK trades when there are any, else what most of the 13F funds added last quarter."""
    s = _load(out, "stock_whales.json")
    ark = s.get("ark") or {}
    mv = [m for m in ark.get("moves") or [] if isinstance(m, dict) and m.get("ticker")]
    if mv:
        buys = list(dict.fromkeys(m["ticker"] for m in mv if str(m.get("action") or "").lower() in ("buy", "add", "new")))[:3]
        sells = list(dict.fromkeys(m["ticker"] for m in mv if str(m.get("action") or "").lower() in ("sell", "cut", "exit")))[:3]
        parts = ([f"купил {', '.join(buys)}"] if buys else []) + ([f"продал {', '.join(sells)}"] if sells else [])
        if parts:
            return "Фонд ARK (Кэти Вуд) за день: " + "; ".join(parts) + "."
    cons = [c for c in s.get("consensus") or [] if isinstance(c, dict) and c.get("items")]
    if cons:
        c = cons[0]
        top = max(c["items"], key=lambda i: (i.get("n") or 0, i.get("usd") or 0))
        nm = top.get("ticker")                     # a ticker only — a company's English name would make the post «English»
        if top.get("n") and nm:
            return f"Фонды (отчёты 13F за {c.get('period_ru') or 'квартал'}): чаще всего докупали {nm} — {top['n']} из {c.get('n_funds')} фондов."
    return None


def onchain_line(out: str) -> str | None:
    """One calm line from the smart wallets: tokens they are accumulating (status «накопление»), with who buys."""
    m = _load(out, "smart_money.json")
    toks = [t for t in ((m.get("watch") or {}).get("tokens") or []) if isinstance(t, dict) and t.get("status") == "накопление" and t.get("symbol")]
    sig = [t for t in m.get("signals") or [] if isinstance(t, dict) and t.get("symbol")]
    pick = (sig or toks)[:2]
    if not pick:
        return None
    def who(t):   # kinds in Russian («маркетмейкер», «фонд / OTC»), not the English firm names: the post must stay Russian
        k = list(dict.fromkeys(str(x.get("kind") or "").split(" /")[0].strip() for x in t.get("buyers") or [] if isinstance(x, dict) and x.get("kind")))
        k = [x for x in k if x and not re.search(r"[A-Za-z]{3,}", x)]
        return f" (покупают: {' и '.join(k[:2])})" if k else ""
    return "В блокчейне крупные кошельки набирают " + "; ".join(f"{t['symbol']}{who(t)}" for t in pick) + "."


SIDE_SHORT = {"long": "лонг", "short": "шорт"}


def block(out: str, now_ms: float | None = None, slot: str = "evening", with_trades: bool = False, highlights: int = 1,
          hours: int = 24, period_ru: str = "За сутки", closed_line: bool = True) -> str:
    """The short «Система CRYPTO NOVA AI» section of a post: the real account's open futures positions, the period's closed trades and
    their result, one calm market line. 05.11 (owner, 09:52: «нигде не надо писать, что это копии китов … везде должна быть система
    CRYPTO NOVA AI … людям нужен только результат»): no whales, no copying, no trader names or counts — only the system's result.
    Empty without the real account's file."""
    real = _load(out, "public_real.json")
    if not real or real.get("stale"):
        return ""
    c = copies(out, now_ms, hours)
    opn = c["open"]
    lines = []
    # 09.11 (owner): every open position is named — never cut with «…»; past 8 the rest are counted and still named
    nm = [f"{o['coin']} {SIDE_SHORT.get(o['side'], o['side'])}" for o in opn]
    pos = ", ".join(nm) if len(nm) <= 8 else ", ".join(nm[:7]) + f" и ещё {len(nm) - 7} ({', '.join(nm[7:])})"
    if (slot == "evening" or with_trades) and closed_line:     # the evening post counts the day's trades in «Сделки за день»
        cl = c["closed"]
        if cl:
            lines.append(f"— {period_ru} закрыто сделок: {len(cl)}, итог <b>{_usd(sum(x['pnl'] for x in cl))}</b>.")
        else:
            lines.append(f"— {period_ru} закрытых сделок не было.")
    if opn:
        lines.append(f"— В работе {len(opn)}: {pos}; у каждой позиции стоп на бирже.")
    else:
        lines.append("— Открытых позиций сейчас нет: система ждёт свой следующий сигнал.")
    hl = [ln for ln in (onchain_line(out), funds_line(out, now_ms)) if ln]
    lines += ["— " + ln for ln in hl[:highlights]]
    return "<b>Система CRYPTO NOVA AI</b>\n" + "\n".join(lines)


def model_line() -> str:
    """How the system works, for people, in one sentence — never the method (owner 05.11)."""
    return (f"Фьючерсы и спот реального счёта ведёт система CRYPTO NOVA AI: плечо подбирается под каждый сигнал, риск сделки — не больше "
            f"{RISK_PCT}% бюджета, стоп на бирже; основа спота — BTC, ETH и SOL.")
