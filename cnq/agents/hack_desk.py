"""«Взломы → шорт (тест)» (owner 20.11.2026: «система должна видеть взломы и открывать шорты»). PAPER ONLY, owner-only, TEST —
never routed to the real account (no «whales:» / «ivan:» signal id; okx_futures opens only copy signals).

Why a forward desk: DefiLlama /hacks gives only the DAY of a hack (no time, no alert link — docs/research/hack-short-2026-11-20.md),
so the minutes after the first alert cannot be studied on history. Here we measure them live, with OUR real detection delay.

Every run (the API, every 5 minutes) polls free public sources and logs the first time WE see each incident:
  • DefiLlama /hacks — a record that was not there on the previous run;
  • rekt.news RSS;
  • public Telegram channel previews (t.me/s/<channel>) of PeckShieldAlert, CyversAlerts, CertiK_Alert — posts with hack words;
  • our own news RSS feeds (cnq/agents/news.FEEDS) — items with hack words.
The first run of each source is a baseline (logged, never traded). An incident's token: DefiLlama's protocol symbol (by id, or the
longest protocol name found in the text) or a $TICKER in the text; majors and stablecoins are never shorted. When that token has an
OKX USDT swap and no paper trade on it in the last 7 days: two PAPER shorts at the OKX last price — «24h» and «7d» (hold),
both with a stop at +15 %; $1 000 each on a $10 000 paper desk; costs 0.10 % a round trip, funding ignored (noted).
Stored per incident: first-seen time, the source's own post time when it has one, the delay, protocol, token, link."""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

H, DAY = 3_600_000, 86_400_000
START, SIZE, STOP, COST = 10_000.0, 1_000.0, 0.15, 0.001
HOLDS = {"24h": DAY, "7d": 7 * DAY}
OKX = "https://www.okx.com"
TG = ("PeckShieldAlert", "CyversAlerts", "CertiK_Alert")
REKT = "https://rekt.news/rss/feed.xml"
HACK_WORDS = ("hack", "exploit", "drain", "stolen", "attack", "compromis", "rug", "breach", "взлом", "infinite mint", "suspicious")
NEVER = {"BTC", "ETH", "SOL", "BNB", "USDT", "USDC", "DAI", "WETH", "WBTC", "TRX", "XRP", "USD", "USDE", "FDUSD", "TUSD", "PYUSD", "STETH",
         "WSTETH", "EUR", "USDS", "DOGE"}


def _get(url, raw=False, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cnq-hack-desk)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    return b.decode("utf-8", "ignore") if raw else json.loads(b.decode())


def hacky(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in HACK_WORDS)


# ── sources → [{key, source, t_src (ms or None), text, link, defillama_id, name}] ─────────────────────────────────────
def from_defillama(hacks: list) -> list[dict]:
    out = []
    for x in hacks or []:
        key = f"dl|{x.get('name')}|{x.get('date')}"
        out.append({"key": key, "source": "defillama", "t_src": None, "name": x.get("name"), "defillama_id": x.get("defillamaId"),
                    "text": f"{x.get('name')} {x.get('technique') or ''} {x.get('description') or ''}"[:600], "amount": x.get("amount"),
                    "link": "https://defillama.com/hacks", "hack": True})
    return out


def parse_rss(xml_text: str, source: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        t = None
        try:
            from email.utils import parsedate_to_datetime
            t = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp() * 1000)
        except Exception:  # noqa: BLE001
            t = None
        text = f"{title} {desc}"[:800]
        out.append({"key": f"{source}|{link or title}", "source": source, "t_src": t, "name": title[:120], "text": text, "link": link,
                    "hack": source == "rekt" or hacky(text)})
    return out


TG_POST = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)


def parse_tg(page: str, channel: str) -> list[dict]:
    """t.me/s/<channel> HTML → posts: id, time (<time datetime>), text"""
    out = []
    for m in TG_POST.finditer(page or ""):
        post, body = m.group(1), m.group(2)
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', body)
        tx = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', body, re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", tx.group(1))) if tx else ""
        t = None
        if tm:
            try:
                from datetime import datetime
                t = int(datetime.fromisoformat(tm.group(1).replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                t = None
        out.append({"key": f"tg|{post}", "source": f"tg:{channel}", "t_src": t, "name": text[:120], "text": text[:800],
                    "link": f"https://t.me/{post}", "hack": hacky(text)})
    return out


# ── token mapping ────────────────────────────────────────────────────────────────────────────────────────────────────
def protocol_index(protos: list) -> dict:
    """{"by_id": {id: symbol}, "names": [(lower name, symbol)] longest first} — protocols with a real symbol only"""
    by_id, names = {}, []
    for p in protos or []:
        s = str(p.get("symbol") or "").upper()
        if not s or s == "-":
            continue
        by_id[str(p.get("id"))] = s
        n = str(p.get("name") or "").strip().lower()
        if len(n) >= 4:
            names.append((n, s))
    names.sort(key=lambda x: -len(x[0]))
    return {"by_id": by_id, "names": names}


def token_of(inc: dict, idx: dict) -> str | None:
    if inc.get("defillama_id") and str(inc["defillama_id"]) in idx.get("by_id", {}):
        s = idx["by_id"][str(inc["defillama_id"])]
        return None if s in NEVER else s
    text = inc.get("text") or ""
    for t in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b", text):
        if t.upper() not in NEVER:
            return t.upper()
    low = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
    for n, s in idx.get("names", []):
        if f" {re.sub(r'[^a-z0-9 ]', ' ', n)} " in low and s not in NEVER:
            return s
    return None


# ── OKX ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def okx(path, **q):
    d = _get(f"{OKX}{path}?{urllib.parse.urlencode(q)}")
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: {d.get('code')}")
    return d.get("data") or []


def swaps(get=okx) -> set:
    return {x["instId"] for x in get("/api/v5/public/instruments", instType="SWAP") if str(x.get("instId", "")).endswith("-USDT-SWAP")}


def last_px(inst, get=okx):
    d = get("/api/v5/market/ticker", instId=inst)
    return float(d[0]["last"]) if d else None


def high_since(inst, t0, get=okx):
    """the highest 5-minute high since t0 (≤ 100 bars back) — the stop check between runs"""
    d = get("/api/v5/market/candles", instId=inst, bar="5m", limit=100)
    hs = [float(r[2]) for r in d if int(r[0]) + 300_000 > t0]
    return max(hs) if hs else None


# ── one run ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def collect(fetch=None, feeds=None) -> dict:
    """{source: [incidents] or an error string} — every source alone; one failing never stops the others"""
    fetch = fetch or _get
    out = {}
    try:
        out["defillama"] = from_defillama(fetch("https://api.llama.fi/hacks"))
    except Exception as e:  # noqa: BLE001
        out["defillama"] = type(e).__name__
    try:
        out["rekt"] = parse_rss(fetch(REKT, raw=True), "rekt")
    except Exception as e:  # noqa: BLE001
        out["rekt"] = type(e).__name__
    for ch in TG:
        try:
            out[f"tg:{ch}"] = parse_tg(fetch(f"https://t.me/s/{ch}", raw=True), ch)
        except Exception as e:  # noqa: BLE001
            out[f"tg:{ch}"] = type(e).__name__
    if feeds is None:
        try:
            from . import news
            feeds = news.FEEDS
        except Exception:  # noqa: BLE001
            feeds = {}
    for name, url in (feeds or {}).items():
        try:
            out[f"news:{name}"] = [x for x in parse_rss(fetch(url, raw=True), f"news:{name}") if x["hack"]]
        except Exception as e:  # noqa: BLE001
            out[f"news:{name}"] = type(e).__name__
    return out


def step(st: dict | None, now: int, sources: dict, idx: dict, swap_set: set, px=None, high=None) -> dict:
    st = st or {"start": START, "cash": START, "seen": {}, "baseline": [], "incidents": [], "open": [], "closed": [], "started": now}
    for k in ("seen", "baseline", "incidents", "open", "closed"):
        st.setdefault(k, {} if k == "seen" else [])
    px = px or last_px
    high = high or high_since
    status = {}
    for src, items in sources.items():
        if isinstance(items, str):
            status[src] = {"ok": False, "err": items}
            continue
        status[src] = {"ok": True, "n": len(items)}
        first = src not in st["baseline"]
        for inc in items:
            if inc["key"] in st["seen"]:
                continue
            st["seen"][inc["key"]] = now
            if first or not inc.get("hack"):
                continue
            tok = token_of(inc, idx)
            inst = f"{tok}-USDT-SWAP" if tok else None
            row = {"t_seen": now, "t_src": inc.get("t_src"), "delay_s": round((now - inc["t_src"]) / 1000) if inc.get("t_src") else None,
                   "source": src, "name": inc.get("name"), "token": tok, "okx": bool(inst and inst in swap_set), "link": inc.get("link"),
                   "amount": inc.get("amount"), "traded": False}
            if row["okx"] and not any(o["token"] == tok and now - o["opened_t"] < 7 * DAY for o in st["open"] + st["closed"]):
                p = None
                try:
                    p = px(inst)
                except Exception:  # noqa: BLE001
                    p = None
                if p:
                    for hv in HOLDS:
                        st["open"].append({"token": tok, "inst": inst, "hold": hv, "entry": p, "stop": p * (1 + STOP), "opened_t": now,
                                           "t_src": inc.get("t_src"), "source": src, "link": inc.get("link"), "name": inc.get("name"), "size": SIZE,
                                           "last_chk": now})
                    row["traded"] = True
            st["incidents"].append(row)
        if first:
            st["baseline"].append(src)
    for o in list(st["open"]):
        try:
            hi = high(o["inst"], o["last_chk"])
            p = px(o["inst"])
        except Exception:  # noqa: BLE001
            continue
        why, ex = None, None
        if hi and hi >= o["stop"]:
            why, ex = "стоп +15%", o["stop"]
        elif now - o["opened_t"] >= HOLDS[o["hold"]]:
            why, ex = "время", p
        o["last_chk"] = now
        if why and ex:
            pct = (-(ex / o["entry"] - 1) - COST) * 100
            usd = round(o["size"] * pct / 100, 2)
            st["cash"] += usd
            st["open"].remove(o)
            st["closed"].append({**o, "exit": ex, "closed_t": now, "pct": round(pct, 3), "usd": usd, "why": why})
        else:
            o["upnl_pct"] = round((-(p / o["entry"] - 1)) * 100, 3) if p else None
    st["status"] = status
    st["t"] = now
    st["incidents"] = st["incidents"][-500:]
    st["closed"] = st["closed"][-500:]
    if len(st["seen"]) > 20_000:
        st["seen"] = dict(sorted(st["seen"].items(), key=lambda kv: kv[1])[-15_000:])
    return st


def public(st: dict, now: int) -> dict:
    by = {}
    for hv in HOLDS:
        cl = [c for c in st.get("closed") or [] if c["hold"] == hv]
        by[hv] = {"closed": len(cl), "pnl_usd": round(sum(c["usd"] for c in cl), 2), "wins": sum(1 for c in cl if c["usd"] > 0),
                  "open": sum(1 for o in st.get("open") or [] if o["hold"] == hv)}
    delays = sorted(i["delay_s"] for i in st.get("incidents") or [] if i.get("delay_s") is not None)
    return {"name": "Взломы → шорт (тест)", "test": True, "owner_only": True, "asof": now, "start": START, "equity": round(st.get("cash", START), 2),
            "by_hold": by, "open": st.get("open") or [], "closed": list(reversed((st.get("closed") or [])[-50:])),
            "incidents": list(reversed((st.get("incidents") or [])[-50:])), "sources": st.get("status") or {},
            "delay_s_median": delays[len(delays) // 2] if delays else None, "started": st.get("started"),
            "rule_ru": ("ТЕСТ, бумага $10 000. Каждые 5 минут: DefiLlama /hacks, rekt.news, Telegram PeckShield / Cyvers / CertiK, наши "
                        "новости. Новый взлом, у токена есть своп OKX → два бумажных шорта по $1 000: держим 24 ч и 7 дней, стоп +15%. "
                        "Задержка — от поста источника до того, как увидели мы. На реальный счёт ничего не попадает.")}
