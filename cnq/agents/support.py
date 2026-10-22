"""«Поддержка проекта» — who stands behind a coin (owner 22.10.2026: «хороший проект часто умирает без медиа, партнёров и
маркет-мейкеров — следить, куда заходят киты и крупные фонды, кто подписывает партнёрства, кому дают маркет-мейкеров»).

A SEPARATE 0–100 assessment for every coin the spot ideas table evaluates (spot_ideas.write → web/ideas.json: `rows[].support`
for the shown rows, `support` {SYM: …} for the other evaluated coins, `support_about_ru`, `support_sources`).
INFORMATION ONLY: it changes no status, no buy and no sell — the honest check is docs/research/support_score.md.

Parts (points). A part with no data is left out and the score is re-normalised over the parts that have data; `cov` = how many
of the 100 points had data; below 50 → no score («мало данных»). Unknown is always «нет данных», never a guess.
  фонды 0–30   tier-1 funds (meme_spot.TIER1) among the investors: CoinGecko «… Portfolio» categories (coin_cards, written by
               research.py), the meme desk's backers (memes.json), DefiLlama raises (api.llama.fi/raises — HTTP 402 «paid» since
               17.10.2026: tried once a day, usually «нет данных»), the project profile (web/coin_profiles.json investors_ru,
               with its sources), spot ETFs named in the profile. 3+ → 30, 2 → 24, 1 → 16, only other known investors → 8,
               checked and none → 0. Company treasuries buying the coin (news) are shown as a fact line.
  киты 0–20    Hyperliquid top-20 accounts (whales.py → whale_snapshots, perps): per account, long − short of the coin now vs
               ≈ 7 days ago (≥ 3 days of snapshots; only accounts seen at both times). More accounts adding than cutting by
               3+ → 20, 2 → 15, 1 → 10, even → 5, more cutting → 0. No tracked account holds the coin → нет данных.
  биржи 0–20   spot on Binance (8), Coinbase (6), OKX (6) — the exchanges' official public instrument lists, once a day.
               A listing in the last 90 days (OKX listTime / announcement, Binance first daily candle, Coinbase first seen by our
               own daily list) is shown but gives NO points: in our check fresh Binance listings lagged older coins by 12 % over
               90 days (docs/research/support_score.md).
  партнёры и маркет-мейкеры 0–30   our news desk (news table: CoinDesk, Cointelegraph, The Block, Decrypt … RSS) in the last 30
               days — a headline that names the coin with partnership / integration words +12; a market maker (Wintermute, GSR,
               DWF Labs …) among the investors, in the profile or in a headline +12; a partners paragraph in the profile +6.
               Counted only for a coin we know (CoinGecko card or profile) or with a hit — no news about an unknown coin is
               not evidence. A headline counts only when it names the coin (ticker or name), not by the desk's asset tag alone.
Cost: each network source once a day (cached in agents.sqlite `support_cache`), 15 s timeout each, 60 s for the whole step; a
failing source keeps its last good answer and is retried after 6 h; any error leaves the ideas table as it was. Every day the
scores go to `support_hist` (day, symbol, score, cov, parts) for the forward check. Stdlib only."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

H, DAY = 3_600_000, 86_400_000
TIMEOUT, BUDGET_S = 15, 60
FRESH_DAYS = 90
MIN_COV = 50
MAX = {"funds": 30, "whales": 20, "exch": 20, "partners": 30}
PART_RU = {"funds": "фонды", "whales": "киты", "exch": "биржи", "partners": "партнёры и маркет-мейкеры"}

OKX_INS = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
OKX_ANN = "https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings&page={}"
BN_INFO = "https://data-api.binance.vision/api/v3/exchangeInfo"
BN_FIRST = "https://data-api.binance.vision/api/v3/klines?symbol={}USDT&interval=1d&startTime=0&limit=1"
CB_PROD = "https://api.exchange.coinbase.com/products"

MM = {"wintermute": "Wintermute", "gsr": "GSR", "dwf": "DWF Labs", "amber group": "Amber Group", "flow traders": "Flow Traders",
      "cumberland": "Cumberland", "jump crypto": "Jump", "jump trading": "Jump", "keyrock": "Keyrock", "b2c2": "B2C2", "auros": "Auros",
      "kairon": "Kairon Labs", "selini": "Selini"}
NEWS_SRC = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt"}
RX_PARTNER = re.compile(r"\bpartner(s|ship|ships|ed|ing)?\b|\bteams? up\b|\bintegrat(e|es|ed|ion)\b|\bcollaborat|\bjoins forces\b|\balliance\b", re.I)
RX_MM = re.compile(r"market[- ]mak(er|ers|ing)|liquidity provider", re.I)
RX_TREASURY_BUY = re.compile(r"treasury|reserve", re.I)
RX_BUY = re.compile(r"\b(buys?|bought|adds?|added|acquires?|acquired|purchases?|accumulat\w*|raises? stake)\b", re.I)
RX_SELL = re.compile(r"\b(sells?|sold|dumps?|offloads?|unloads?)\b", re.I)

ABOUT_RU = ("«Поддержка проекта» — отдельная оценка 0–100: кто стоит за монетой. Фонды (до 30): фонды первого ряда среди инвесторов "
            "по CoinGecko, DefiLlama и профилю проекта, спотовые ETF. Киты (до 20): крупные счета Hyperliquid нарастили или сократили "
            "позицию за неделю. Биржи (до 20): спот на Binance, Coinbase, OKX. Партнёры и маркет-мейкеры (до 30): партнёрства в "
            "новостях за 30 дней, маркет-мейкер среди инвесторов, партнёры в профиле. Чего нет в данных — «нет данных» и в балл не "
            "идёт; балл считается по тому, что есть, и не показывается, если данных меньше чем на 50 баллов. Только для информации: "
            "на покупки и продажи не влияет. Проверка на истории 2022–2026: монеты с фондами первого ряда не обгоняли остальные "
            "(за 30 дней −1.8% к BTC, надёжность t −1.4), свежие листинги на Binance за 90 дней отставали от старых монет на 12% "
            "(t −3.8). Подробно: docs/research/support_score.md.")


# ----------------------------------------------------------------------------- small helpers
def _d(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%d.%m.%Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _clip(s: str, n: int = 16) -> str:
    w = (s or "").split()
    return " ".join(w[:n]).rstrip(",;:—.") + ("…" if len(w) > n else "")


def _get(url: str, timeout: float = TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "cryptonova-support/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS support_cache(k TEXT PRIMARY KEY, t INTEGER, v TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS support_hist(day INTEGER, symbol TEXT, score INTEGER, cov INTEGER, parts TEXT,"
                 " PRIMARY KEY(day, symbol))")


def _cget(conn, k):
    r = conn.execute("SELECT t, v FROM support_cache WHERE k=?", (k,)).fetchone()
    try:
        return (r[0], json.loads(r[1])) if r else (None, None)
    except ValueError:
        return None, None


def _cput(conn, k, v, now):
    conn.execute("INSERT OR REPLACE INTO support_cache VALUES(?,?,?)", (k, now, json.dumps(v)))


class Fetcher:
    """Daily cache + per-source timeout + a budget for the whole step. A failed source keeps the last good value."""

    def __init__(self, conn, now: int, get=_get, net: bool = True, budget_s: float = BUDGET_S):
        self.conn, self.now, self.get, self.net = conn, now, get, net
        self.t_end = time.monotonic() + budget_s
        self.status: dict[str, dict] = {}

    def daily(self, key: str, fn, max_age: int = DAY, retry: int = 6 * H):
        t, v = _cget(self.conn, key)
        tt, _ = _cget(self.conn, "try:" + key)
        fresh = t is not None and self.now - t <= max_age
        if not fresh and self.net and time.monotonic() < self.t_end and (tt is None or self.now - tt >= retry):
            _cput(self.conn, "try:" + key, 1, self.now)
            try:
                v2 = fn()
                _cput(self.conn, key, v2, self.now)
                t, v, fresh = self.now, v2, True
            except Exception as e:  # noqa: BLE001 — a failing source never breaks the run
                self.status[key] = {"ok": False, "asof": t, "error": f"{type(e).__name__}: {str(e)[:120]}"}
                self.conn.commit()
                return v if (t is not None and self.now - t <= 3 * DAY) else None
            self.conn.commit()
        if key not in self.status:
            self.status[key] = {"ok": v is not None, "asof": t}
        return v if (t is not None and self.now - t <= 3 * DAY) else None


# ----------------------------------------------------------------------------- sources
def okx_spot(get) -> dict:
    d = (get(OKX_INS) or {}).get("data")
    if not d:
        raise RuntimeError("okx instruments")
    out: dict[str, int] = {}
    for x in d:
        b = x.get("baseCcy")
        if b and x.get("quoteCcy") in ("USDT", "USDC", "USD") and x.get("state") in (None, "live"):
            lt = int(x.get("listTime") or 0)
            out[b] = min(v for v in (out.get(b), lt) if v) if (out.get(b) or lt) else 0
    return out


def okx_announcements(get) -> list:
    out = []
    for p in (1, 2):
        d = (get(OKX_ANN.format(p)) or {}).get("data") or []
        for blk in d:
            for a in blk.get("details") or []:
                out.append({"title": a.get("title") or "", "url": a.get("url") or "", "t": int(a.get("pTime") or 0)})
    if not out:
        raise RuntimeError("okx announcements")
    return out[:80]


def binance_spot(get) -> list:
    d = (get(BN_INFO) or {}).get("symbols")
    if not d:
        raise RuntimeError("binance exchangeInfo")
    return sorted({x["baseAsset"] for x in d if x.get("status") == "TRADING" and x.get("quoteAsset") in ("USDT", "USDC", "FDUSD")})


def coinbase_spot(get) -> list:
    d = get(CB_PROD)
    if not isinstance(d, list) or not d:
        raise RuntimeError("coinbase products")
    return sorted({x["base_currency"] for x in d if x.get("status") == "online" and not x.get("trading_disabled")
                   and x.get("quote_currency") in ("USD", "USDC", "USDT")})


def _first_seen(conn, key: str, cur: list | None, now: int) -> dict:
    """{base: first_seen_ms or None}: bases present at our first look have no date (None) — we never guess one."""
    _, st = _cget(conn, key)
    st = st or {}
    if cur:
        seeded = bool(st)
        for b in cur:
            if b not in st:
                st[b] = now if seeded else None
        _cput(conn, key, st, now)
    return st


# ----------------------------------------------------------------------------- data that is already in agents.sqlite / web
def _portfolios(conn) -> dict:
    out = {}
    try:
        for sym, data in conn.execute("SELECT symbol, data FROM coin_cards"):
            cats = (json.loads(data) or {}).get("categories") or []
            out[(sym or "").upper()] = [c.replace(" Portfolio", "").replace(" portfolio", "") for c in cats if "portfolio" in c.lower()
                                        and c.lower() not in ("venture capital portfolios", "ondo intelligent portfolios")]
    except (sqlite3.Error, ValueError):
        return {}
    return out


def whale_moves(conn, now: int) -> dict | None:
    """{coin: {up, down, n, days, usd}} — Hyperliquid top accounts, now vs ≈7 days ago; None = not enough history."""
    try:
        times = [r[0] for r in conn.execute("SELECT DISTINCT time FROM whale_snapshots ORDER BY time")]
    except sqlite3.Error:
        return None
    if not times or now - times[-1] > 2 * DAY:
        return None
    t1 = times[-1]
    cand = [t for t in times if t <= t1 - 3 * DAY]
    if not cand:
        return None
    t0 = min(cand, key=lambda t: abs(t - (t1 - 7 * DAY)))

    def snap(t):
        net, addrs = {}, set()
        for a, c, s, n in conn.execute("SELECT addr, coin, side, notional FROM whale_snapshots WHERE time=?", (t,)):
            c = c[1:] if c[:1] == "k" and c[1:].isupper() else c
            addrs.add(a)
            net[(a, c.upper())] = net.get((a, c.upper()), 0.0) + (n or 0) * (1 if s == "LONG" else -1)
        return net, addrs
    n0, a0 = snap(t0)
    n1, a1 = snap(t1)
    both = a0 & a1
    out: dict[str, dict] = {}
    for a, c in set(n0) | set(n1):
        if a not in both:
            continue
        x0, x1 = n0.get((a, c), 0.0), n1.get((a, c), 0.0)
        o = out.setdefault(c, {"up": 0, "down": 0, "n": 0, "usd": 0.0, "days": round((t1 - t0) / DAY)})
        o["n"] += 1
        dlt = x1 - x0
        if abs(dlt) >= max(50_000.0, 0.10 * max(abs(x0), abs(x1))):
            o["up" if dlt > 0 else "down"] += 1
            o["usd"] += dlt
    return out


def news_hits(conn, now: int, days: int = 30) -> tuple[dict, bool]:
    """{SYM: [{kind, title, link, src, t}]} for the last `days` days; ok = the news table covers ≥ 14 days."""
    try:
        rows = conn.execute("SELECT title, link, published, assets, source FROM news WHERE published>=? ORDER BY published DESC",
                            (now - days * DAY,)).fetchall()
        first = conn.execute("SELECT MIN(published) FROM news").fetchone()[0]
    except sqlite3.Error:
        return {}, False
    ok = bool(first) and now - first >= 14 * DAY and bool(rows)
    out: dict[str, list] = {}
    for title, link, pub, assets, src in rows:
        try:
            tags = [a for a in json.loads(assets or "[]") if a and a != "MARKET"]
        except ValueError:
            tags = []
        title = title or ""
        for sym in tags:
            kinds = []
            if RX_MM.search(title):
                kinds.append("mm")
            if RX_PARTNER.search(title):
                kinds.append("partner")
            if RX_TREASURY_BUY.search(title) and (RX_BUY.search(title) or RX_SELL.search(title)):
                kinds.append("treasury_sell" if RX_SELL.search(title) else "treasury_buy")
            for k in kinds:
                out.setdefault(sym.upper(), []).append({"kind": k, "title": title, "link": link, "src": NEWS_SRC.get(src, src), "t": pub})
    return out, ok


RX_ETF_NO = re.compile(r"\bнет\b|не было|заявк|подал|отозвал|отклон|ожида", re.I)


def _etf_trades(txt: str) -> bool:
    """A sentence of the profile names a spot ETF that trades — not «ETF нет», not a filing still waiting."""
    return any(re.search(r"ETF", s) and not RX_ETF_NO.search(s) for s in re.split(r"(?<=[.;!?])\s+", txt or ""))


def _names_in(title: str, sym: str, name: str) -> bool:
    t = title or ""
    return bool(re.search(rf"\b{re.escape(sym)}\b", t)) or (bool(name) and len(name) >= 4 and name.lower() in t.lower())


# ----------------------------------------------------------------------------- score
def score_coin(sym: str, x: dict, now: int) -> dict:
    """x: everything known about the coin (see attach). Returns {score, cov, parts, lines, nodata}."""
    parts, lines, nodata = {}, [], []

    def line(k, t, url=None, src=None):
        lines.append({k2: v for k2, v in (("k", k), ("t", t), ("url", url)) if v})      # the source's name is in the text

    # фонды
    pf, prof, raises, mb = x.get("portfolios"), x.get("profile") or {}, x.get("raises"), x.get("meme_backers") or {}
    inv_txt = prof.get("investors_ru") or ""
    from .meme_spot import tier1_of
    names = list(dict.fromkeys((pf or []) + list(mb.get("investors") or []) +
                               [i for r in raises or [] for i in (r.get("lead") or []) + (r.get("other") or [])]))
    t1 = tier1_of(names + [inv_txt])
    etf = _etf_trades(inv_txt + " " + (prof.get("partners_ru") or ""))
    checked = pf is not None or bool(prof) or raises is not None or bool(mb.get("checked"))
    if checked:
        k = len(t1) + (1 if etf else 0)
        parts["funds"] = 30 if k >= 3 else 24 if k == 2 else 16 if k == 1 else 8 if (names or (inv_txt and not re.search(r"не было|нет данных", inv_txt))) else 0
        if t1:
            line("funds", "фонды первого ряда: " + ", ".join(t1[:5]) + (" (портфели фондов на CoinGecko)" if pf else ""),
                 x.get("cg_url"), "CoinGecko" if pf else None)
        elif names:
            line("funds", "инвесторы: " + ", ".join(names[:4]) + " — не из первого ряда", x.get("cg_url"), "CoinGecko")
        for r in (raises or [])[:1]:
            amt = f"${r['amount_musd']:.0f}M" if isinstance(r.get("amount_musd"), (int, float)) else "сумма неизвестна"
            line("funds", f"раунд {r.get('round') or ''} {amt}, {_d((r.get('date') or 0) * 1000)[3:]}"
                 + (f", ведущий: {', '.join(r['lead'][:2])}" if r.get("lead") else ""), "https://defillama.com/raises", "DefiLlama")
        if etf:
            line("funds", "спотовые ETF — по профилю проекта", (prof.get("sources") or [None])[0], "профиль")
        if inv_txt:
            line("funds", "из профиля: " + _clip(inv_txt, 12), (prof.get("sources") or [None])[0], "профиль")
        if not (t1 or names or etf or inv_txt):
            line("funds", "фонды: монеты нет в портфелях фондов на CoinGecko", x.get("cg_url"), "CoinGecko")
    else:
        nodata.append("фонды")
    for n in x.get("news", []):
        if n["kind"] in ("treasury_buy", "treasury_sell"):
            line("funds", ("казначейства покупают: «" if n["kind"] == "treasury_buy" else "казначейства продают: «") + _clip(n["title"], 14)
                 + f"» ({n['src']}, {_d(n['t'])[:5]})", n["link"], n["src"])
            break

    # киты
    wm = x.get("whales")
    if wm is None:
        nodata.append("киты")
    else:
        w = wm.get(sym)
        if not w:
            nodata.append("киты")
        else:
            net = w["up"] - w["down"]
            parts["whales"] = 20 if net >= 3 else 15 if net == 2 else 10 if net == 1 else 5 if net == 0 else 0
            usd = w["usd"]
            line("whales", f"киты Hyperliquid (фьючерсы, топ-20 счетов) за {w['days']} дн.: нарастили {w['up']}, сократили {w['down']}"
                 + (f" из {w['n']}" if w["n"] else "") + (f", итог {'+' if usd >= 0 else '−'}${abs(usd) / 1e6:.1f}M" if abs(usd) >= 1e5 else ""),
                 "https://app.hyperliquid.xyz/leaderboard", "Hyperliquid")

    # биржи
    ex = x.get("exch") or {}
    known = [k for k in ("binance", "coinbase", "okx") if ex.get(k) is not None]
    if len(known) >= 2:
        on = {k: sym in (ex.get(k) or {}) for k in known}
        parts["exch"] = round((8 * on.get("binance", False) + 6 * on.get("coinbase", False) + 6 * on.get("okx", False))
                              * 20 / sum({"binance": 8, "coinbase": 6, "okx": 6}[k] for k in known))
        nm = {"binance": "Binance", "coinbase": "Coinbase", "okx": "OKX"}
        yes, no = [nm[k] for k in known if on[k]], [nm[k] for k in known if not on[k]]
        line("exch", ("спот: " + ", ".join(yes) if yes else "спота на крупных биржах нет") + (f"; нет на {', '.join(no)}" if yes and no else ""))
        ann = (x.get("okx_ann") or [None])[0]
        fresh = False
        for k in known:
            ts = (ex.get(k) or {}).get(sym) if on[k] else None
            if ts and now - ts <= FRESH_DAYS * DAY:
                fresh = True
                line("exch", f"свежий листинг: {nm[k]} {_d(ts)} — такие монеты в истории за 90 дней отставали от BTC (балл не даёт)",
                     ann["url"] if (k == "okx" and ann) else None, nm[k])
        if ann and not fresh:
            line("exch", f"OKX: «{_clip(ann['title'], 12)}» {_d(ann['t'])}", ann.get("url"), "OKX")
    else:
        nodata.append("биржи")

    # партнёры и маркет-мейкеры
    news_ok = x.get("news_ok")
    mm_names = [v for k, v in MM.items() if re.search(rf"\b{k}\b", " ".join(names + [inv_txt, prof.get("partners_ru") or ""]).lower())]
    news = x.get("news", [])
    p_news = [n for n in news if n["kind"] == "partner"]
    m_news = [n for n in news if n["kind"] == "mm"]
    if p_news or m_news or mm_names or prof or pf is not None:     # no news hit about an unknown coin is not evidence
        pts = 0
        if p_news:
            pts += 12
            n = p_news[0]
            line("partners", f"партнёрство: «{_clip(n['title'], 14)}» ({n['src']}, {_d(n['t'])[:5]})", n["link"], n["src"])
        elif news_ok:
            line("partners", "партнёрства в новостях за 30 дней: не найдено")
        if mm_names or m_news:
            pts += 12
            if mm_names:
                line("partners", "маркет-мейкер среди инвесторов или партнёров: " + ", ".join(dict.fromkeys(mm_names)), x.get("cg_url"))
            else:
                n = m_news[0]
                line("partners", f"маркет-мейкер: «{_clip(n['title'], 14)}» ({n['src']}, {_d(n['t'])[:5]})", n["link"], n["src"])
        if prof.get("partners_ru"):
            pts += 6
            line("partners", "партнёры (профиль): " + _clip(prof["partners_ru"], 12), (prof.get("sources") or [None])[0], "профиль")
        parts["partners"] = min(30, pts)
    else:
        nodata.append("партнёры")

    cov = sum(MAX[k] for k in parts)
    score = round(100 * sum(parts.values()) / cov) if cov else None
    if cov < MIN_COV:
        score = None
    # at most 7 lines: the first line of every part, then the second ones … (so no part is crowded out)
    by: dict[str, list] = {}
    for ln in lines:
        by.setdefault(ln["k"], []).append(ln)
    keep, i = [], 0
    while len(keep) < 7 and any(len(v) > i for v in by.values()):
        keep += [v[i] for v in by.values() if len(v) > i][:7 - len(keep)]
        i += 1
    lines[:] = [ln for ln in lines if any(ln is k for k in keep)]
    if nodata:
        line("nodata", "нет данных: " + ", ".join(nodata))
    return {"score": score, "cov": cov, "parts": parts, "lines": lines, "nodata": nodata}


# ----------------------------------------------------------------------------- the hook
def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def attach(d: dict, web_dir: str = "web", db: str | None = None, now: int | None = None, get=_get, net: bool | None = None) -> dict:
    """Adds the support assessment to an ideas.json document in place. Never raises (an error is written to d)."""
    t0 = time.monotonic()
    try:
        now = now or int(d.get("asof") or time.time() * 1000)
        db = db or os.path.join(os.path.dirname(os.path.abspath(web_dir)), "data", "agents.sqlite")
        conn = sqlite3.connect(db, timeout=20) if os.path.exists(db) else None
        if net is None:
            net = conn is not None and os.environ.get("CNQ_SUPPORT_NET", "1") != "0"
        research = _load(os.path.join(web_dir, "research.json")) or {}
        memes = _load(os.path.join(web_dir, "memes.json")) or {}
        profiles = (_load(os.path.join(web_dir, "coin_profiles.json")) or {}).get("coins") or {}
        cards = {(c.get("symbol") or "").upper(): c for c in research.get("cards") or []}
        mrows = {(m.get("symbol") or "").upper(): m for m in memes.get("spot_memes") or []}
        syms = sorted(set(d.get("statuses") or {}) | {r["symbol"] for r in d.get("rows") or []})
        src: dict = {}
        exch, okx_ann, raises_by_id, pfs, wm, news, news_ok = {}, [], None, {}, None, {}, False
        if conn is not None:
            ensure(conn)
            F = Fetcher(conn, now, get=get, net=net)
            okx = F.daily("okx_spot", lambda: okx_spot(get))
            bn = F.daily("binance_spot", lambda: binance_spot(get))
            cb = F.daily("coinbase_spot", lambda: coinbase_spot(get))
            ann = F.daily("okx_listings", lambda: okx_announcements(get))
            cb_first = _first_seen(conn, "coinbase_first", cb, now) if cb is not None else None
            bn_first = {}
            if bn is not None:
                for s in syms:
                    if s in bn:
                        r = None
                        try:
                            r = conn.execute("SELECT first_ms FROM sq_age WHERE symbol=?", (s,)).fetchone()
                        except sqlite3.Error:
                            pass
                        if r and r[0]:
                            bn_first[s] = int(r[0])
                            continue
                        _, v = _cget(conn, "bnfirst:" + s)
                        if v:
                            bn_first[s] = int(v)
                        elif net and len([k for k in F.status if k.startswith("bnfirst")]) < 8 and time.monotonic() < F.t_end:
                            try:
                                k = get(BN_FIRST.format(s))
                                if k:
                                    bn_first[s] = int(k[0][0]); _cput(conn, "bnfirst:" + s, bn_first[s], now)
                                F.status["bnfirst:" + s] = {"ok": bool(k)}
                            except Exception as e:  # noqa: BLE001
                                F.status["bnfirst:" + s] = {"ok": False, "error": str(e)[:80]}
            exch = {"okx": okx if okx is not None else None,
                    "binance": ({s: bn_first.get(s) for s in bn} if bn is not None else None),
                    "coinbase": ({b: (cb_first or {}).get(b) for b in cb} if cb is not None else None)}
            okx_ann = [a for a in ann or [] if now - (a.get("t") or 0) <= FRESH_DAYS * DAY]
            # DefiLlama raises by CoinGecko name (paid since 17.10.2026 — one try a day)
            names = {c["id"]: c.get("name") for s, c in cards.items() if s in syms and c.get("id") and c.get("name")}
            from .meme_spot import llama_matches
            raises_by_id = F.daily("llama_raises", lambda: llama_matches(lambda u: get(u, 25), names), retry=DAY)
            pfs = _portfolios(conn)
            wm = whale_moves(conn, now)
            news, news_ok = news_hits(conn, now)
            src = {k: v for k, v in F.status.items() if not k.startswith("bnfirst")}
            nb = [v for k, v in F.status.items() if k.startswith("bnfirst")]
            if nb:
                src["binance_first_day"] = {"ok": all(v.get("ok") for v in nb), "n": len(nb)}
        src["whales"] = {"ok": wm is not None}
        src["news"] = {"ok": news_ok}
        src["coingecko_portfolios"] = {"ok": bool(pfs), "n": len(pfs)}
        src["profiles"] = {"ok": bool(profiles), "n": len(profiles)}
        out = {}
        for s in syms:
            c, m = cards.get(s) or {}, mrows.get(s) or {}
            cid = c.get("id") or m.get("id")
            nm = c.get("name") or m.get("name") or ""
            x = {"portfolios": pfs.get(s) if s in pfs else None, "profile": profiles.get(s) or profiles.get(s.replace("1000", "")) or {},
                 "raises": (raises_by_id or {}).get(cid) if (raises_by_id is not None and cid in (raises_by_id or {})) else None,
                 "meme_backers": m.get("backers") or {}, "whales": wm, "exch": exch, "news_ok": news_ok,
                 "news": [n for n in news.get(s, []) if _names_in(n["title"], s, nm)],
                 "okx_ann": [a for a in okx_ann if _names_in(a["title"], s, "")],
                 "cg_url": f"https://www.coingecko.com/en/coins/{cid}" if cid else None}
            out[s] = score_coin(s, x, now)
        shown = set()
        for r in d.get("rows") or []:
            r["support"] = out.get(r["symbol"]); shown.add(r["symbol"])
        # the other evaluated coins (for the coin card): shorter — 4 evidence lines + «нет данных», no part points
        d["support"] = {s: {"score": v["score"], "cov": v["cov"], "nodata": v["nodata"],
                            "lines": [ln for ln in v["lines"] if ln["k"] != "nodata"][:4] + [ln for ln in v["lines"] if ln["k"] == "nodata"]}
                        for s, v in out.items() if s not in shown}
        d["support_about_ru"] = ABOUT_RU
        d["support_sources"] = src
        if conn is not None:
            day = now // DAY
            conn.executemany("INSERT OR REPLACE INTO support_hist VALUES(?,?,?,?,?)",
                             [(day, s, v["score"], v["cov"], json.dumps(v["parts"])) for s, v in out.items()])
            conn.commit(); conn.close()
        d["support_seconds"] = round(time.monotonic() - t0, 1)
    except Exception as e:  # noqa: BLE001 — the ideas table never fails because of this block
        d["support_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return d
