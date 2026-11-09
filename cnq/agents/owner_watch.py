"""«Под наблюдением владельца» — ADA, AVAX, LIT (09.11.2026, owner 21:40: «на ADA стоят полки покупателя, AVAX постоянно
мелькает, LIT»). Information only: no trading, no buy/sell advice. Writes web/owner_watch.json (the owner-only card on top of
«Спот» at cryptonova.vip, a line in the owner's morning report and evening digest).

Per coin:
  • price, 24 h / 7 d change, distance to the 20 / 50 / 200-day averages (OKX spot daily candles);
  • the order book within ±5 % of the price: the largest bid walls («полки покупателя») and ask walls, $ size, how long each has
    stood (OKX spot full book + BingX USDT-M depth; a wall = the $ in one 0.25 % price band; «стоит с» carried from run to run);
  • funding (web/funding.json, else OKX) and the 24 h change of open interest (OKX);
  • smart wallets (web/smart_money.json) and big traders' positions on Hyperliquid / Binance (web/whale_watch.json);
  • unlocks (web/tokenomics.json; LIT: the 27.12.2026 cliff with its source);
  • the latest news items WITH a source and a link only (web/decision.json).
Alerts (kept 3 days): a bid wall ≥ $1M appears / disappears, the price crosses a 20 / 50 / 200-day average, a big trader opens
a long, a smart wallet buys. Every number is from a named source; nothing is estimated."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

COINS = {"ADA": {"name": "Cardano", "words": ("cardano", " ada ", "ada ")},
         "AVAX": {"name": "Avalanche", "words": ("avalanche", "avax")},
         "LIT": {"name": "Lighter", "words": ("lighter", " lit ")}}
# unlocks the tokenomics feed does not carry yet — each with its source
UNLOCK_NOTES = {"LIT": [{"date": "2026-12-27", "kind": "cliff",
                         "text_ru": "27.12.2026 заканчивается годовой «замок» команды и инвесторов (50% всего выпуска: команда 26%, инвесторы 24%); "
                                    "дальше — равномерная разблокировка 3 года, около 3,2 млн LIT в неделю.",
                         "source": "Tokenomist", "link": "https://tokenomist.ai/research/lighter-lit-tokenomics-robinhood-hype-a-real-burn-and-the-december-2026-cliff-2"}]}
BAND = 0.0025            # one wall = the $ resting in a 0.25 % price band
RANGE = 0.05             # ±5 % of the price
BIG_WALL = 1_000_000     # alerts for bid walls from $1M
KEEP_ALERTS_MS = 3 * 86400_000
OKX = "https://www.okx.com"
SRC_RU = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt"}


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _http(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-owner-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _okx(path: str, http=_http, **q):
    d = http(f"{OKX}{path}?{urllib.parse.urlencode(q)}")
    if str(d.get("code")) not in ("0", ""):
        raise RuntimeError(f"OKX {path}: {d.get('msg') or d.get('code')}")
    return d.get("data") or []


# ───────────────────────────────────────────── fetch (CI) ─────────────────────────────────────────────
def fetch(coin: str, http=_http, bingx=None) -> dict:
    """raw inputs for one coin; every part is optional (an exchange that fails is listed in errors)"""
    raw: dict = {"errors": []}
    try:   # daily candles, newest first: [ts, o, h, l, c, vol, ...]
        rows = _okx("/api/v5/market/candles", http, instId=f"{coin}-USDT", bar="1Dutc", limit=300)
        raw["daily"] = [[int(r[0]), float(r[4])] for r in reversed(rows)]
    except Exception as e:  # noqa: BLE001
        raw["errors"].append(f"свечи OKX: {str(e)[:80]}")
    books = []
    try:
        b = (_okx("/api/v5/market/books-full", http, instId=f"{coin}-USDT", sz=5000) or [{}])[0]
        books.append({"venue": "OKX спот", "bids": [(float(p), float(q)) for p, q, *_ in b.get("bids") or []],
                      "asks": [(float(p), float(q)) for p, q, *_ in b.get("asks") or []]})
    except Exception as e:  # noqa: BLE001
        raw["errors"].append(f"стакан OKX: {str(e)[:80]}")
    if bingx is not None:
        try:
            d = bingx.depth(f"{coin}-USDT", 1000)
            books.append({"venue": "BingX фьючерсы", "bids": d["bids"], "asks": d["asks"]})
        except Exception as e:  # noqa: BLE001
            raw["errors"].append(f"стакан BingX: {str(e)[:80]}")
    raw["books"] = books
    try:
        f = (_okx("/api/v5/public/funding-rate", http, instId=f"{coin}-USDT-SWAP") or [{}])[0]
        raw["funding_okx"] = float(f["fundingRate"]) if f.get("fundingRate") not in (None, "") else None
    except Exception as e:  # noqa: BLE001
        raw["errors"].append(f"фандинг OKX: {str(e)[:80]}")
    try:   # [ts, oi_usd, vol_usd], newest first
        rows = _okx("/api/v5/rubik/stat/contracts/open-interest-volume", http, ccy=coin, period="1H")
        raw["oi"] = [[int(r[0]), float(r[1])] for r in reversed(rows[:30])]
    except Exception as e:  # noqa: BLE001
        raw["errors"].append(f"открытый интерес OKX: {str(e)[:80]}")
    return raw


# ───────────────────────────────────────────── analysis ─────────────────────────────────────────────
def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def levels(daily: list) -> dict:
    c = [x[1] for x in daily or []]
    if len(c) < 2:
        return {}
    px = c[-1]
    out = {"price": px, "chg24": (px / c[-2] - 1) * 100, "chg7": (px / c[-8] - 1) * 100 if len(c) >= 8 else None}
    for n in (20, 50, 200):
        s = _sma(c, n)
        out[f"sma{n}"] = s
        out[f"dist{n}"] = (px / s - 1) * 100 if s else None
    return out


def walls(book: dict, mid: float, top: int = 3) -> dict:
    """the largest $ bands within ±5 % on each side"""
    res = {}
    for side in ("bids", "asks"):
        bands: dict[int, list] = {}
        for p, q in book.get(side) or []:
            if not p or abs(p / mid - 1) > RANGE:
                continue
            k = int(round((p / mid - 1) / BAND))
            b = bands.setdefault(k, [0.0, 0.0, 0.0])     # $ total, Σ price·$, max single level $
            usd = p * q
            b[0] += usd; b[1] += p * usd; b[2] = max(b[2], usd)
        rows = [{"price": b[1] / b[0], "usd": b[0], "dist": (b[1] / b[0] / mid - 1) * 100} for b in bands.values() if b[0] > 0]
        res[side] = sorted(rows, key=lambda r: -r["usd"])[:top]
    return res


def _match(prev: list[dict], w: dict, venue: str) -> dict | None:
    return next((p for p in prev if p.get("venue") == venue and abs(p["price"] / w["price"] - 1) <= BAND * 1.5), None)


def coin_doc(coin: str, raw: dict, prev: dict, out: str, now: int) -> tuple[dict, list[dict]]:
    """one coin's card and its new alerts (prev = the coin's card from the previous run)"""
    lv = levels(raw.get("daily") or [])
    px = lv.get("price") or prev.get("price")
    alerts = []
    doc = {"coin": coin, "name": COINS[coin]["name"], "asof": now, **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in lv.items()}}
    # walls with their «standing since»
    bw, aw = [], []
    for b in raw.get("books") or []:
        if not px or not b.get("bids"):
            continue
        mid = (max(p for p, _ in b["bids"]) + min(p for p, _ in b["asks"])) / 2 if b.get("asks") else px
        ws = walls(b, mid)
        for side, acc in (("bids", bw), ("asks", aw)):
            for w in ws[side]:
                old = _match(prev.get("bid_walls" if side == "bids" else "ask_walls") or [], w, b["venue"])
                acc.append({"venue": b["venue"], "price": round(w["price"], 6), "usd": round(w["usd"]), "dist": round(w["dist"], 2),
                            "since": (old or {}).get("since") or now})
    doc["bid_walls"] = sorted(bw, key=lambda w: -w["usd"])[:4]
    doc["ask_walls"] = sorted(aw, key=lambda w: -w["usd"])[:4]
    pb = prev.get("bid_walls")
    if pb is not None and raw.get("books"):
        for w in doc["bid_walls"]:
            if w["usd"] >= BIG_WALL and not _match(pb, w, w["venue"]):
                alerts.append({"t": now, "coin": coin, "kind": "wall_new",
                               "text": f"{coin}: появилась полка покупателя {_m(w['usd'])} на {_px(w['price'])} ({_pct(w['dist'])} от цены, {w['venue']})."})
        venues = {b["venue"] for b in raw["books"]}
        for w in pb:
            if w.get("usd", 0) >= BIG_WALL and w.get("venue") in venues and not _match(doc["bid_walls"], w, w["venue"]):
                alerts.append({"t": now, "coin": coin, "kind": "wall_gone",
                               "text": f"{coin}: исчезла полка покупателя {_m(w['usd'])} на {_px(w['price'])} ({w['venue']})."})
    # level breaks: the 20 / 50 / 200-day averages
    pp = prev.get("price")
    if pp and px:
        for n in (20, 50, 200):
            s = lv.get(f"sma{n}")
            if s and (pp - s) * (px - s) < 0:
                alerts.append({"t": now, "coin": coin, "kind": "level",
                               "text": f"{coin}: цена {'поднялась выше' if px > s else 'опустилась ниже'} {n}-дневной средней ({_px(s)})."})
    # funding and OI
    fr = None
    for r in _load(out, "funding.json").get("positions") or []:
        if isinstance(r, dict) and r.get("symbol") == coin:
            fr = {k: v for k, v in (r.get("rates") or {}).items() if v is not None}
    if not fr and raw.get("funding_okx") is not None:
        fr = {"okx": raw["funding_okx"]}
    doc["funding"] = {k: round(v * 100, 4) for k, v in (fr or {}).items()}          # % per 8 h
    oi = raw.get("oi") or []
    if len(oi) >= 2:
        base = next((v for t, v in oi if t <= oi[-1][0] - 24 * 3600_000), oi[0][1])
        doc["oi_usd"], doc["oi_chg24"] = round(oi[-1][1]), round((oi[-1][1] / base - 1) * 100, 1) if base else None
    # big traders (whale_watch) and smart wallets (smart_money)
    big = []
    for w in _load(out, "whale_watch.json").get("whales") or []:
        for p in (w.get("positions") or []) if isinstance(w, dict) else []:
            if isinstance(p, dict) and p.get("coin") == coin and not p.get("hidden"):
                big.append({"who": w.get("name") or w.get("short") or "трейдер", "side": p.get("side"), "usd": round(float(p.get("notional") or 0)),
                            "entry": p.get("entry")})
    doc["traders"] = sorted(big, key=lambda b: -b["usd"])[:5]
    old_long = {(b["who"], b["side"]) for b in prev.get("traders") or []}
    if prev.get("asof"):
        for b in doc["traders"]:
            if b["side"] == "LONG" and (b["who"], "LONG") not in old_long:
                alerts.append({"t": now, "coin": coin, "kind": "whale",
                               "text": f"{coin}: крупный трейдер {b['who']} открыл лонг на {_m(b['usd'])}."})
    sm = []
    M = _load(out, "smart_money.json")
    for t in ((M.get("watch") or {}).get("tokens") or []) + (M.get("signals") or []):
        if isinstance(t, dict) and t.get("symbol") == coin:
            ev = sorted([e for e in t.get("events") or [] if isinstance(e, dict) and e.get("kind") in ("buyer", "sell")], key=lambda e: -(e.get("t") or 0))
            buys = [e for e in ev if e.get("kind") == "buyer"]
            sm.append({"status": t.get("status"), "chain": t.get("chain"), "price": t.get("price"),
                       "last": (ev[0].get("text") if ev else None), "last_t": (ev[0].get("t") if ev else None)})
            for e in buys:
                if prev.get("asof") and (e.get("t") or 0) > prev["asof"]:
                    alerts.append({"t": now, "coin": coin, "kind": "smart", "text": f"{coin}: умный кошелёк — {e.get('text')}."})
    doc["smart"] = sm[:3]
    # unlocks
    un = []
    nx = (_load(out, "tokenomics.json").get("unlocks_next") or {}).get(coin)
    if isinstance(nx, dict) and nx.get("date"):
        un.append({"date": nx["date"], "text_ru": f"{nx['date'][8:10]}.{nx['date'][5:7]}: разблокировка {nx.get('pct_circ')}% обращения.",
                   "source": "Tokenomics", "link": nx.get("source")})
    un += [u for u in UNLOCK_NOTES.get(coin, []) if not any(x["date"] == u["date"] for x in un)]
    doc["unlocks"] = un
    # news with a source only
    D = (_load(out, "decision.json").get("news") or {})
    items = []
    for i in (D.get("impacts") or []) + (D.get("items") or []):
        if not isinstance(i, dict) or not i.get("source") or not i.get("link"):
            continue
        txt = " " + str(i.get("title") or "").lower() + " "
        if i.get("asset") == coin or any(w in txt for w in COINS[coin]["words"]):
            items.append({"title": i.get("title"), "source": SRC_RU.get(str(i["source"]).lower(), i["source"]), "link": i["link"],
                          "t": i.get("created") or i.get("time") or i.get("published")})
    seen, news = set(), []
    for n in sorted(items, key=lambda n: -(n["t"] or 0)):
        if n["link"] not in seen:
            seen.add(n["link"]); news.append(n)
    doc["news"] = news[:3]
    doc["errors"] = raw.get("errors") or []
    return doc, alerts


def _m(usd: float) -> str:
    return f"${usd / 1e6:.1f}M".replace(".", ",") if usd >= 1e6 else f"${usd / 1e3:.0f}K"


def _px(p: float) -> str:
    return (f"${p:,.4f}" if p < 1 else f"${p:,.2f}" if p < 1000 else f"${p:,.0f}").replace(",", " ").replace(".", ",")


def _pct(v: float) -> str:
    return ("+" if v >= 0 else "−") + f"{abs(v):.1f}".replace(".", ",") + "%"


def build(out: str, raws: dict, now: int | None = None) -> dict:
    now = int(now or time.time() * 1000)
    prev = _load(out, "owner_watch.json")
    pc = {c["coin"]: c for c in prev.get("coins") or [] if isinstance(c, dict)}
    coins, new = [], []
    for coin in COINS:
        d, a = coin_doc(coin, raws.get(coin) or {"errors": ["нет данных"]}, pc.get(coin) or {}, out, now)
        coins.append(d); new += a
    alerts = new + [a for a in prev.get("alerts") or [] if isinstance(a, dict) and now - (a.get("t") or 0) <= KEEP_ALERTS_MS]
    return {"asof": now, "coins": coins, "alerts": alerts[:40],
            "about_ru": "Под наблюдением владельца: только информация, не совет покупать или продавать. Полка — сумма заявок в полосе 0,25% цены "
                        "в пределах ±5%; «стоит с» — с какого прогона мы её видим (прогон раз в 30 минут)."}


def owner_line(out: str, now: int | None = None, hours: int = 24) -> str | None:
    """one calm line for the owner's morning report / evening digest: the watched coins' alerts of the last `hours`"""
    d = _load(out, "owner_watch.json")
    now = int(now or time.time() * 1000)
    a = [x for x in d.get("alerts") or [] if isinstance(x, dict) and now - (x.get("t") or 0) <= hours * 3600_000]
    if not d.get("coins"):
        return None
    if not a:
        return "Под наблюдением (ADA, AVAX, LIT): заметных изменений нет."
    by = {}
    for x in a:
        by.setdefault(x["kind"], []).append(x)
    first = [v[0]["text"] for v in by.values()][:2]
    more = len(a) - len(first)
    return "Под наблюдением: " + " ".join(first) + (f" И ещё событий: {more}." if more > 0 else "")


def run(out: str = "web", http=_http, bingx=None, now: int | None = None) -> dict:
    raws = {c: fetch(c, http, bingx) for c in COINS}
    doc = build(out, raws, now)
    tmp = os.path.join(out, "owner_watch.json.tmp")
    json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, os.path.join(out, "owner_watch.json"))
    return doc


if __name__ == "__main__":
    bx = None
    try:
        import sys
        sys.path.insert(0, os.getcwd())
        from cnq.bingx.rest import BingXRest
        from cnq.config import Config
        bx = BingXRest(Config().bingx)
    except Exception:  # noqa: BLE001
        bx = None
    d = run("web", bingx=bx)
    for c in d["coins"]:
        print(c["coin"], c.get("price"), "bids", [(w["venue"], w["price"], w["usd"]) for w in c["bid_walls"][:2]], "errors", c["errors"])
    print("alerts", len(d["alerts"]))
