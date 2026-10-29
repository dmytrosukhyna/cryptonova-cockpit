"""«Из каналов» — what public Telegram channels the owner reads post about launchpads, airdrops, listings and early projects,
written down as INFORMATION (29.10.2026, owner: «вот ещё группы в ТГ, которыми я пользуюсь … там бывает инфа про ранние проекты,
лаунчпад, новости, новые возможности — для общего развития»).

Not a signal and not a rule: nothing here trades or changes a desk. Every server run (≈ 30 min, together with channel_watch) the
newest posts of each channel (public web preview t.me/s/<name>, no login) are sorted into ONE category:
    launchpad  лаунчпад / лаунчпул / IDO / пресейл / TGE / HODLer airdrop      airdrop  аирдроп / ретродроп / тестнет / поинты
    listing    листинг / делистинг на бирже                                     early    ранний проект / раунд / инвесторы
    news       обычная новость                                                  ad       реклама / реферальная ссылка (counted only)
and a record keeps ONLY: date, channel, post link, category, project name (≤ 32 chars) / ticker, exchange named, «on OKX spot»,
«has a referral link» — never the post text (copyright and privacy).
For a ticker with an OKX spot pair the OKX price (and BTC's) is noted when the post is first seen — only if the post is fresh
(≤ 3 h old), otherwise the move before we saw it would count — and 1 / 7 / 30 days later the coin's move and BTC's move are written
down: the channel's mentions get a track record (vs BTC), not a verdict on one post.

The same classifier is used by the 200-day audit (scripts/channel_audit.py), so the live numbers and the history count alike.
Stdlib only; fetch / prices injectable for tests."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.request

DAY = 86_400_000
H = 3_600_000
FRESH = 3 * H                   # a post older than this when first seen gets no price (its move before we saw it is unknown)
HORIZONS = (1, 7, 30)           # days
OKX_TICKERS = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
CAT_ORDER = ("launchpad", "listing", "airdrop", "early", "news")
CAT_RU = {"launchpad": "Лаунчпад / IDO / пресейл", "listing": "Листинг", "airdrop": "Аирдроп / тестнет / поинты",
          "early": "Ранний проект", "news": "Новость", "ad": "Реклама / рефералка"}
LABEL_RU = "Информация, не сигнал; ранние проекты — высокий риск."

# ── the classifier (lower-case text; Russian + English as these channels write) ─────────────────────────────────────────
LAUNCHPAD = re.compile(
    r"(launch ?pad|launch ?pool|лаунч ?пад|лаунч ?пул|лонч ?пад|лонч ?пул|лаунчпул|\bido\b|\bieo\b|\bico\b|\bido-|pre-?sale|пресейл|"
    r"пре-сейл|предпродаж|\btge\b|megadrop|мегадроп|jumpstart|джампстарт|hodler ?airdrop|холдер ?аирдроп|buidlpad|coinlist|"
    r"token ?sale|токен ?сейл|токенсейл|\bсейл[аеу]?\b|public sale|публичн\w* (раунд|сейл|продаж)|launchx|startup ?mining|"
    r"candybomb|кэндибомб|ido-платформ|sale on|\bfjord\b|\blegion\b|\becho\b раунд)")
AIRDROP = re.compile(
    r"(air ?drop|аирдроп|аир ?дроп|эирдроп|эйрдроп|ретро ?дроп|retro ?drop|\bдроп[аыуе]?\b|\bдропов\b|testnet|тестнет|тест-нет|"
    r"поинт|\bpoints\b|фарм|farming|квест|\bquests?\b|galxe|zealy|layer3|клейм|\bclaim|снапшот|snapshot|чекер|checker|eligib|"
    r"элиджиб|whitelist|вайтлист|\bwl\b|\bноды?\b|\bnode\b|активност\w+ в сети|ончейн-активн|кампани\w+ с наград)")
LISTING = re.compile(
    r"(листинг|залист|листят|листанут|listing|will list|lists? \$?[A-Za-z0-9]{2,10}\b|will add|добавит\w* (торговую )?пар|"
    r"новая пара|делист|delist|появ\w+ на (binance|bybit|okx|upbit|coinbase|bitget|kucoin|gate|mexc|бинанс|байбит)|"
    r"торги (начнутся|стартуют|откроются)|trading (starts|opens|will open)|перпетуал|бессрочн\w+ контракт|pre-?market)")
EARLY = re.compile(
    r"(ранн\w+ (проект|стади|этап|доступ)|early[- ]?(stage|access|project)|новый проект|\bгем\w*\b|\bgem\b|сид[- ]?раунд|"
    r"seed[- ]?round|pre-?seed|series [ab]\b|раунд\w* (финансир|инвест)|привлек\w*\s+\$?\d|raised\s+\$?\d|фандрайз|fundrais|"
    r"backed by|инвестор\w*|\ba16z\b|paradigm|binance labs|yzi labs|polychain|pantera|multicoin|coinbase ventures|dragonfly|"
    r"\bvc\b|венчур|оцен\w+ (в|до) \$|без токена|до токена|pre-?token|pre-?tge|не выпустил\w* токен|токена (пока|ещё|еще) нет)")
NEWS = re.compile(
    r"(новост|\bnews\b|⚡|срочно|breaking|\bsec\b|\bcftc\b|\betf\b|\bфрс\b|\bfed\b|пауэлл|powell|ставк\w+ (фрс|ецб)|инфляц|\bcpi\b|"
    r"трамп|trump|регулир|regulat|закон|взлом|\bhack|эксплойт|exploit|банкрот|bankrupt|одобр|approv|blackrock|microstrategy|"
    r"сейлор|saylor|разблок|\bunlock|анлок|сжига|\bburn|хардфорк|hard ?fork|мейннет|mainnet|партн[её]рств|partnership|"
    r"заявил|сообщ|announce|объявил|запуст|launch(es|ed)?\b|выпуст|релиз|release)")
AD = re.compile(
    r"(промокод|promo ?code|реферал|referral|\bref\b|по (моей|нашей|этой) ссылке|регистрир|зарегистр|бонус\w* до|welcome bonus|"
    r"розыгрыш|giveaway|#реклама|\bреклама\b|\berid\b|партн[её]р\w* (материал|пост)|спонсор|sponsored|скидк|discount|"
    r"оплачен|платн\w+ (канал|чат|курс|подписк)|vip[- ]?(канал|чат|клуб|групп)|приватн\w+ (канал|чат|клуб)|закрыт\w+ (канал|чат|клуб)|"
    r"курс по|обучени|вебинар|марафон|наставничеств|менторств|копируй мои сделки|копитрейдинг у)")
REF_LINK = re.compile(r"([?&](ref|referral|ref_?code|invite_?code|invitecode|invite|fpr|aff|affiliate|partner|promo|utm_source|clacCode|"
                      r"inviter|code|r)=|/join/|/register\?|/invite/|/referral|partner\.bybit|promote\.mexc|/r/[A-Za-z0-9]{4,})", re.I)
VENUES = ("binance", "bybit", "okx", "bitget", "gate", "mexc", "kucoin", "upbit", "coinbase", "bithumb", "hyperliquid", "bingx",
          "htx", "kraken", "robinhood", "aster", "pancakeswap")
VENUE_RU = {"бинанс": "binance", "байбит": "bybit", "окх": "okx", "биткет": "bitget", "битгет": "bitget", "мекс": "mexc",
            "кукоин": "kucoin", "апбит": "upbit", "коинбейс": "coinbase", "гейт": "gate"}
# upper-case words that are never tickers here (or are tickers too ambiguous to count from a bare word)
NOT_TICKERS = set("""USD USDT USDC USDE FDUSD DAI EUR RUB UAH CEO CTO ETF SEC CFTC FED FOMC CPI PPI GDP USA UK EU AI API APR APY TVL
NFT DEX CEX DAO KYC TGE IDO IEO ICO ATH ATL OTC P2P TP SL PNL ROI FAQ PDF VPN ID IT ON IN OR TO BY AT OF IS AS BE DO GO NO SO UP
WE ME MY OK TV OKX BINANCE BYBIT MEXC GATE BITGET KUCOIN UPBIT HTX LONG SHORT BUY SELL NEW HOT TOP VIP PRO DCA RSI MACD EMA SMA
LTF HTF ETH2 BTCD USDTD TOTAL TOTAL2 TOTAL3 OTHERS DXY SPX NDX NASDAQ SP500 GOLD XAU OIL WTI USDJPY EURUSD L1 L2 RWA DEFI GAMEFI
DEPIN MEME AMA UTC GMT MSK KYIV PM AM FUD FOMO HODL SAFU GG TBA TBD LP CL OI LS""".split())
# bare upper-case words that are also coin tickers but in these posts are almost always plain words (a #tag still counts)
BARE_STOP = set("""NOT ALT ONE FUN WIN KEY BIG ACT AND THE FOR YOU ALL ANY CAN GET NOW OUT BUT HAS ITS WAS CHECK NEWS MOVE PUMP
BOOST GAME GOOD BEST REAL LIVE OPEN FREE EARN SAFE TRUE MORE MAX MIN HIGH LOW RISE DROP JUST LIKE SUN BIO PEOPLE ZERO MEME AIR
CAT DOG SOON TIME DAY WEEK HOME BANK GAS FORM WELL STEP RED""".split())
STAKE_ASSETS = {"BNB", "ETH", "SOL", "TON", "SUI", "TRX", "OKB", "MNT", "BGB", "GT", "KCS", "HT", "CAKE", "ARB", "OP", "BASE"}
STABLE = {"USDT", "USDC", "USDE", "FDUSD", "DAI", "TUSD", "PYUSD", "USD1", "USDS", "BUSD", "RLUSD", "USDD", "USDG"}
PAIRED = re.compile(r"\b([A-Z][A-Za-z0-9.\-]{1,24}(?: [A-Z][A-Za-z0-9.\-]{1,20})?)\s*\(\$?([A-Z0-9]{2,10})\)")
NAME_STOP = set("""Binance Bybit OKX Bitget Gate Mexc MEXC KuCoin Kucoin Upbit Coinbase Bithumb Hyperliquid BingX HTX Kraken
Telegram Twitter X Discord Launchpool Launchpad Megadrop HODLer Airdrop Airdrops Alpha Earn Wallet Web3 Spot Futures Pre-Market
Trading The New Today Now Big Top Hot Новость Новости Срочно Листинг Лаунчпул Аирдроп Итог Итоги Монета Проект Токен Биржа
Bitcoin Ethereum Solana Ripple Tether Crypto Options Option Policy Market Markets Update Breaking Report Season Points Testnet Mainnet
Galxe Zealy Layer3 Google Apple YouTube Youtube TradingView CoinMarketCap CoinGecko DefiLlama Dune Medium Notion Gitbook Docs App
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August September October
November December Long Short Buy Sell Price Volume Bull Bear Daily Weekly Monthly Total Free Join Click Link Here More Read Watch
Ether Stablecoin Stablecoins Token Tokens Coin Coins Project Projects Exchange Chain Network Protocol Bridge Swap Stake Staking""".split())
NAME_HINT = re.compile(r"(?:проект\w*|project|протокол\w*|testnet|тестнет\w*|аирдроп\w*|airdrop|ретродроп\w*|лаунчпул\w*|launchpool|"
                       r"\bido\b|сейл\w*|\bот|\bу)\s+[«\"']?([A-Z][A-Za-z0-9.\-]{1,24})", re.I)


def categorize(text: str, links=()) -> dict:
    """{"cats": [every category that matches], "primary": the first of CAT_ORDER that matches or None, "promo": ad / referral}"""
    low = (text or "").lower()
    ref = any(REF_LINK.search(u or "") for u in links or ())
    ad = bool(AD.search(low)) or ref
    cats = [c for c, rx in (("launchpad", LAUNCHPAD), ("listing", LISTING), ("airdrop", AIRDROP), ("early", EARLY), ("news", NEWS))
            if rx.search(low)]
    return {"cats": cats + (["ad"] if ad else []), "primary": cats[0] if cats else None, "promo": ad, "ref_link": ref}


def tickers(text: str, known: set | None = None) -> list[str]:
    """tickers named in a post, in order: «Name (TKR)», #TKR / $TKR (any — a tag is deliberate), then bare upper-case words that are a
    known coin (≥ 3 letters, not in NOT_TICKERS). BTC is kept (a caller drops it when it needs alts)."""
    t = text or ""
    cand = [m.group(2) for m in PAIRED.finditer(t)]                                   # «Name (TKR)»: the post says it is a ticker
    for x in re.findall(r"[#$]([A-Za-z][A-Za-z0-9]{1,11})\b", t):                     # #tag / $tag: a coin we know (not «#BTCUSDT»)
        x = x.upper()
        if known is None or x in known:
            cand.append(x)
        elif x.endswith("USDT") and x[:-4] in known:
            cand.append(x[:-4])
    if known:
        cand += [w for w in re.findall(r"(?<![#$\w])([A-Z][A-Z0-9]{2,9})(?![\w])", t) if w in known and w not in BARE_STOP]
    seen, res = set(), []
    for x in cand:
        x = x.upper()
        if x in seen or x in NOT_TICKERS or x in STABLE or x.isdigit():
            continue
        seen.add(x)
        res.append(x)
    return res


def project_name(text: str) -> str | None:
    """«Plasma (XPL)» → «Plasma»; else a capitalised Latin word right after a project word («тестнет от Monad» → «Monad») or one
    the post repeats; else None — a guess, shown as the project's name, never as a ticker"""
    for m in PAIRED.finditer(text or ""):
        nm = m.group(1).strip()
        words = [w for w in nm.split() if w not in NAME_STOP]
        if words:
            return " ".join(words)[:32]
    ok = lambda w: w not in NAME_STOP and w.lower() not in VENUES and w[:1].isupper()  # noqa: E731
    m = next((m.group(1) for m in NAME_HINT.finditer(text or "") if ok(m.group(1))), None)
    if m:                                    # «тестнет от Monad», «проект Plasma» — named right after a project word
        return m[:32]
    words = [w for w in re.findall(r"(?<![#$@/\w.])([A-Z][a-z][A-Za-z0-9]{1,20}(?:\.[a-z]{2,4})?)(?![\w/])", text or "") if ok(w)]
    if words:                                # otherwise only a name the post repeats (a one-off capitalised word is too often noise)
        best = max(dict.fromkeys(words), key=words.count)
        if words.count(best) >= 2:
            return best[:32]
    return None


def venue(text: str) -> str | None:
    low = (text or "").lower()
    best = None
    for v in VENUES:
        i = low.find(v)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, v)
    for ru, v in VENUE_RU.items():
        i = low.find(ru)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, v)
    return best[1] if best else None


def extract(text: str, links=(), known: set | None = None, okx: set | None = None) -> dict:
    """one post → the record fields (no text). cat None = nothing for the opportunities list (still counted as a post)."""
    c = categorize(text, links)
    tk = [x for x in tickers(text, known) if x != "BTC"]
    if c["primary"] in ("launchpad", "airdrop"):
        # in a launchpool / airdrop post BNB, ETH, SOL… are what you stake or pay with, not the project (the 200-day audit: most
        # «launchpad coins» were BNB / ETH / SOL) — the project is the name, or a ticker the post pairs with it
        tk = [x for x in tk if x not in STAKE_ASSETS]
    tick = tk[0] if tk else None
    return {"cat": c["primary"], "cats": c["cats"], "promo": c["promo"], "ref_link": c["ref_link"], "ticker": tick,
            "tickers": tk[:5], "project": project_name(text), "venue": venue(text),
            "okx": (tick in okx) if (tick and okx is not None) else None}


# ── live: record, price, resolve ───────────────────────────────────────────────────────────────────────────────────────
def okx_prices(timeout: int = 15) -> dict:
    """{BASE: last} for every OKX spot pair to USDT (USDC when there is no USDT pair) — one request"""
    req = urllib.request.Request(OKX_TICKERS, headers={"User-Agent": "cnq/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode()).get("data") or []
    out, usdc = {}, {}
    for x in d:
        inst = str(x.get("instId") or "")
        try:
            px = float(x.get("last") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0 or "-" not in inst:
            continue
        b, q = inst.split("-", 1)
        if q == "USDT":
            out[b] = px
        elif q == "USDC":
            usdc[b] = px
    for b, px in usdc.items():
        out.setdefault(b, px)
    return out


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS chop_posts(channel TEXT, post INTEGER, t INTEGER, cat TEXT, promo INTEGER, PRIMARY KEY(channel, post));
      CREATE TABLE IF NOT EXISTS chop_items(channel TEXT, post INTEGER, t INTEGER, t_seen INTEGER, cat TEXT, promo INTEGER,
        project TEXT, ticker TEXT, venue TEXT, okx INTEGER, px0 REAL, btc0 REAL,
        r1 REAL, b1 REAL, r7 REAL, b7 REAL, r30 REAL, b30 REAL, PRIMARY KEY(channel, post));""")


def _pct(x, nd=2):
    return None if x is None else round(x * 100, nd)


def _stats(rows: list, h: int) -> dict | None:
    """rows: (r_h, b_h) pairs → {n, median_vs_btc_pct, mean_vs_btc_pct, beat_btc_pct, median_pct}"""
    v = [(r, b) for r, b in rows if r is not None and b is not None]
    if not v:
        return None
    rel = sorted(r - b for r, b in v)
    ab = sorted(r for r, _ in v)
    med = lambda a: a[len(a) // 2] if len(a) % 2 else (a[len(a) // 2 - 1] + a[len(a) // 2]) / 2  # noqa: E731
    return {"days": h, "n": len(v), "median_pct": _pct(med(ab)), "median_vs_btc_pct": _pct(med(rel)),
            "mean_vs_btc_pct": _pct(sum(rel) / len(rel)), "beat_btc_pct": round(sum(1 for x in rel if x > 0) / len(rel) * 100)}


def step(conn: sqlite3.Connection, posts_by_channel: dict, titles: dict, prices: dict | None, okx: set | None,
         known: set | None, now: int | None = None, audit: dict | None = None) -> dict:
    """posts_by_channel: {channel: [{"id", "text", "t"(ms, optional), "links"(optional)}]} — the same page channel_watch reads"""
    now = int(now or time.time() * 1000)
    ensure(conn)
    stop = sorted(NAME_STOP)                 # a word added to the stop list later stops showing as a «project» on old records too
    conn.execute(f"UPDATE chop_items SET project=NULL WHERE project IN ({','.join('?' * len(stop))})", stop)
    prices = prices or {}
    btc = prices.get("BTC")
    known = set(known or ()) | set(prices) | set(okx or ())
    for ch, posts in posts_by_channel.items():
        first = conn.execute("SELECT COUNT(*) FROM chop_posts WHERE channel=?", (ch,)).fetchone()[0] == 0
        for p in posts or []:
            if conn.execute("SELECT 1 FROM chop_posts WHERE channel=? AND post=?", (ch, p["id"])).fetchone():
                continue
            t = int(p.get("t") or now)
            e = extract(p.get("text") or "", p.get("links") or (), known, okx)
            conn.execute("INSERT INTO chop_posts VALUES(?,?,?,?,?)", (ch, p["id"], t, e["cat"] or ("ad" if e["promo"] else None), int(e["promo"])))
            if e["cat"] in (None, "news") and not (e["cat"] == "news" and e["ticker"]):
                continue                     # plain news without a coin, chatter and ads: counted, not listed
            fresh = (not first) and now - t <= FRESH
            px0 = prices.get(e["ticker"]) if (e["ticker"] and e["okx"] and fresh) else None
            conn.execute("INSERT OR IGNORE INTO chop_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL)",
                         (ch, p["id"], t, now, e["cat"], int(e["promo"]), e["project"], e["ticker"], e["venue"],
                          None if e["okx"] is None else int(e["okx"]), px0, btc if px0 else None))
    # resolve 1 / 7 / 30 days after first seen (OKX last price now vs then, BTC alike)
    if btc:
        for h in HORIZONS:
            for ch, post, tick, px0, btc0 in conn.execute(
                    f"SELECT channel, post, ticker, px0, btc0 FROM chop_items WHERE px0 IS NOT NULL AND r{h} IS NULL AND t_seen<=?",
                    (now - h * DAY,)).fetchall():
                px = prices.get(tick)
                if px:
                    conn.execute(f"UPDATE chop_items SET r{h}=?, b{h}=? WHERE channel=? AND post=?", (px / px0 - 1, btc / btc0 - 1, ch, post))
    conn.commit()
    return build(conn, titles, now, audit)


def build(conn: sqlite3.Connection, titles: dict, now: int, audit: dict | None = None) -> dict:
    ensure(conn)
    chans = {}
    for ch, title in titles.items():
        cnt = dict(conn.execute("SELECT COALESCE(cat,'other'), COUNT(*) FROM chop_posts WHERE channel=? AND t>=? GROUP BY 1",
                                (ch, now - 30 * DAY)).fetchall())
        n30 = sum(cnt.values())
        promo = conn.execute("SELECT COUNT(*) FROM chop_posts WHERE channel=? AND t>=? AND promo=1", (ch, now - 30 * DAY)).fetchone()[0]
        t0 = conn.execute("SELECT MIN(t) FROM chop_posts WHERE channel=?", (ch,)).fetchone()[0]
        span = max(1.0, min(30.0, (now - t0) / DAY)) if t0 else None
        rows = conn.execute("SELECT r1, b1, r7, b7, r30, b30 FROM chop_items WHERE channel=? AND px0 IS NOT NULL", (ch,)).fetchall()
        chans[ch] = {"title": title, "link": f"https://t.me/{ch}", "posts_30d": n30,
                     "per_day": round(n30 / span, 1) if span and n30 else None, "by_cat_30d": cnt,
                     "promo_pct": round(promo / n30 * 100) if n30 else None,
                     "tracked": len(rows), "vs_btc": [s for s in (_stats([(r[0], r[1]) for r in rows], 1), _stats([(r[2], r[3]) for r in rows], 7),
                                                                    _stats([(r[4], r[5]) for r in rows], 30)) if s],
                     "audit": (audit or {}).get(ch)}
    items = []
    for (ch, post, t, cat, promo, proj, tick, ven, okx, px0, r1, b1, r7, b7, r30, b30) in conn.execute(
            "SELECT channel, post, t, cat, promo, project, ticker, venue, okx, px0, r1, b1, r7, b7, r30, b30 FROM chop_items "
            "WHERE t>=? ORDER BY t DESC LIMIT 80", (now - 45 * DAY,)).fetchall():
        items.append({"t": t, "channel": ch, "title": titles.get(ch, ch), "url": f"https://t.me/{ch}/{post}", "cat": cat, "cat_ru": CAT_RU.get(cat, cat),
                      "project": proj, "ticker": tick, "venue": ven, "okx": None if okx is None else bool(okx), "promo": bool(promo),
                      "tracked": px0 is not None,
                      "r1_pct": _pct(r1), "vs_btc_1d_pct": _pct(None if r1 is None else r1 - b1),
                      "r7_pct": _pct(r7), "vs_btc_7d_pct": _pct(None if r7 is None else r7 - b7),
                      "r30_pct": _pct(r30), "vs_btc_30d_pct": _pct(None if r30 is None else r30 - b30)})
    return {"asof": now, "label_ru": LABEL_RU, "categories": {k: CAT_RU[k] for k in CAT_ORDER}, "channels": chans, "items": items,
            "about_ru": ("Что публичные каналы, которые читает владелец, пишут про лаунчпады, аирдропы, листинги и ранние проекты. "
                         "Храним только дату, канал, ссылку на пост, категорию, название / тикер и биржу — без текста поста. "
                         "Если у монеты есть спот-пара на OKX и пост свежий (до 3 часов), записываем цену OKX и через 1 / 7 / 30 дней "
                         "сравниваем с биткоином. Это информация для самостоятельной проверки, не сигнал и не правило торговли.")}
