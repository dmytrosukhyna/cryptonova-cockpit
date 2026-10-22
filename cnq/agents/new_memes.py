"""«Новые мемы дня» — a daily filter of NEW Solana memecoins and a PAPER book of its picks (22.10.2026, owner: «мем-портфель не
рабочий: он ждёт, а новые мемы появляются каждый день; фильтра новых мемов нет»).

Why the meme desk itself does not trade: its only rule (research rule C, docs/research/memes.md) buys a Hyperliquid meme perp after a
4-hour close at least 25 % below the 24-hour high. From the desk's start (17.10) to 22.10 the deepest such drop among liquid HL memes
was −15.4 % (USELESS, 18.10), so there was no signal; in 2026 the rule fired 8 times in 9.5 months. The rule works as designed — it
is just rare. New memes are another market: they trade on Solana DEX pools and do not reach Hyperliquid in their first week.

The filter (every meme-desk run, on the Solana rows the desk already fetches: GeckoTerminal trending + new pools → DexScreener →
Solana RPC authorities → RugCheck → holder concentration; nothing new is downloaded except the price of the picks we hold):
  • the pool is 1 to 7 days old (the first day is where most rugs happen; after a week it is no longer «new»);
  • liquidity ≥ $150k, 24 h volume ≥ $1M, but volume ≤ 30× the pool (more looks like wash trading);
  • ≥ 1 000 distinct buyers in 24 h and at least one seller (a token nobody sells may be unsellable);
  • no red flag of the desk's own check (mint/freeze authority revoked, no permanent delegate, no honeypot sign, no RugCheck
    «danger», top-10 wallets ≤ 60 %, insiders < 25 %), no transfer fee;
  • RugCheck has looked at it and ≥ 50 % of the pool's liquidity is locked or burned (an unlocked pool can be pulled — a rug);
  • top-10 wallets (without pools) ≤ 35 % when known; FDV ≤ 60× the pool; not a chase (≤ +300 % in 24 h).
Picks: up to 3 a day (UTC), best desk score first; a token is not picked again for 14 days.

The paper book ($10 000, no real money): 3 % of the book per pick; DEX costs are charged as 3 % against us on every fill (entry and
exit); prices are sampled about every 30 minutes, so a stop fills at the next sample, worse if the price gapped. Exit: −50 % stop
before the body; at ×2 sell half (the body); the rest is sold 40 % below its peak or after 7 days. A pool with < $10k of liquidity, or
no price for 24 h, is counted as a 100 % loss — on a DEX that is what a rug looks like, and we do not pretend to know better.

Honesty: an EXPERIMENT with no history behind it — the replay on the desk's own snapshots of 17–22.10 is in RETRO below and it lost
money. Memecoins can go to zero in minutes. Stdlib only; the price fetch is injectable for tests."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

H, DAY = 3_600_000, 86_400_000
DS_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"

AGE_H = (24, 168)
MIN_LIQ, MIN_VOL, MAX_TURN = 150_000, 1_000_000, 30
MIN_BUYERS = 1_000
MAX_TOP10 = 35
MIN_LP_LOCK = 50
MAX_FDV_LIQ = 60
MAX_CHG24 = 300
PICKS_PER_DAY, REPICK_DAYS = 3, 14

START = 10_000.0
PICK_SHARE = 0.03
SLIP = 0.03                      # DEX price impact + fees per fill, against us
STOP, BODY_X, BODY_FRAC, TRAIL, MAX_DAYS = -0.50, 2.0, 0.5, 0.40, 7
RUG_LIQ, NO_PRICE_H = 10_000, 24

RULES_RU = [
    "Пул на Solana от 1 до 7 дней: первый день — время большинства «рагов», через неделю мем уже не новый.",
    f"Ликвидность пула от ${MIN_LIQ // 1000}k, оборот за сутки от ${MIN_VOL // 1_000_000}M, но не больше чем в {MAX_TURN} раз больше пула (иначе похоже на накрутку).",
    f"От {MIN_BUYERS:,} разных покупателей за сутки и хотя бы один продавец — токен, который никто не продаёт, может быть непродаваемым.".replace(",", " "),
    "Ни одного красного флага нашей проверки: допечатка и заморозка отключены, нет «вечного делегата», нет признаков ханипота, "
    "RugCheck без «опасно», топ-10 кошельков не больше 60%, инсайдеры меньше 25%; без комиссии за перевод.",
    f"RugCheck проверил токен, и не меньше {MIN_LP_LOCK}% ликвидности пула заблокировано или сожжено — иначе пул могут вывести.",
    f"Топ-10 кошельков (без пулов) не больше {MAX_TOP10}%, если известно; оценка (FDV) не больше {MAX_FDV_LIQ} пулов; не погоня — рост за сутки не больше +{MAX_CHG24}%.",
    f"В день — не больше {PICKS_PER_DAY} мемов, лучшие по баллу; один и тот же токен — не чаще раза в {REPICK_DAYS} дней.",
]
BOOK_RU = (f"Бумажная книга $10 000, без реальных денег: {PICK_SHARE * 100:g}% книги на мем. Издержки DEX — {SLIP * 100:g}% против нас на каждой "
           f"сделке (вход и выход). Цены — замер раз в ~30 минут, поэтому стоп исполняется на следующем замере, при гэпе — хуже. "
           f"Выход: стоп −{abs(STOP) * 100:.0f}% пока не взято тело; на ×{BODY_X:g} продаём половину (тело); остаток — при откате {TRAIL * 100:.0f}% от пика "
           f"или через {MAX_DAYS} дней. Пул меньше ${RUG_LIQ // 1000}k или нет цены {NO_PRICE_H} ч — считаем потерю 100%: на DEX так выглядит «раг».")
WARNINGS = [
    "Новые мемы — самый рискованный рынок: токен может стать нулём за минуты (раг, ханипот), и это не поймать заранее никаким фильтром.",
    "Только бумага: реальных покупок нет. Если повторять — только сумма, которую не жалко потерять полностью. Можно потерять 100%.",
    "Фильтр — эксперимент без истории: проверка на наших же замерах 17–22.10 дала минус (см. ниже). Это не прогноз роста и не финансовый совет.",
]
# replay of this exact filter on the desk's committed snapshots (web/memes.json, 17.10 12:59 → 22.10 07:14 UTC, 462 snapshots, the
# top-30 Solana rows each) — prices only while a token stayed in those rows; after that the result is unknown and said so
RETRO = {
    "period": "17.10–22.10.2026", "snapshots": 462, "picks": 2, "picks_without_lp_rule": 6,
    "text_ru": ("Проверка на наших замерах 17.10–22.10 (462 снимка, только 30 лучших строк Solana в каждом — что выпало из них, дальше "
                "не видно). Фильтр выбрал бы 2 мема: startupSI (20.10) закрылся бы по стопу −59% через 6 часов, а потом его пул вывели "
                "($424k → $7k, «раг»); VEIL (17.10) на последнем замере −32%. Без условия «ликвидность заблокирована от 50%» — 6 мемов: "
                "4 закрылись бы по стопу (−52…−59%), 2 выпали из списка на −34% и +17%. Честный вывод: на этих днях фильтр терял бы "
                "деньги; ведём его только на бумаге, пока не наберётся живая история."),
}


def _f(x, default=None):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def check(r: dict) -> list[str]:
    """Why a Solana row is NOT a new-meme candidate (empty list = it is one). Short Russian reasons, in the order of the rules."""
    why = []
    age, liq, vol = r.get("age_h"), _f(r.get("liquidity"), 0.0), _f(r.get("vol24"), 0.0)
    if age is None:
        why.append("возраст пула неизвестен")
    elif age < AGE_H[0]:
        why.append(f"пулу {age:.0f} ч — меньше суток")
    elif age > AGE_H[1]:
        why.append(f"пулу {age / 24:.0f} дн. — уже не новый")
    if liq < MIN_LIQ:
        why.append(f"пул ${liq / 1e3:.0f}k < ${MIN_LIQ // 1000}k")
    if vol < MIN_VOL:
        why.append(f"оборот ${vol / 1e6:.2f}M < ${MIN_VOL // 1_000_000}M")
    elif liq and vol / liq > MAX_TURN:
        why.append(f"оборот в {vol / liq:.0f} раз больше пула — похоже на накрутку")
    buyers = r.get("buyers24")
    if not isinstance(buyers, (int, float)) or buyers < MIN_BUYERS:
        why.append(f"покупателей за сутки {int(buyers) if isinstance(buyers, (int, float)) else 'нет данных'} < {MIN_BUYERS}")
    if not r.get("sells24"):
        why.append("никто не продаёт")
    reds = [f.get("text") for f in r.get("flags") or [] if f.get("level") == "red"]
    if reds or (r.get("red_flags") or 0) > 0:
        why.append("красный флаг: " + (reds[0] if reds else "есть"))
    fee = ((r.get("auth") or {}).get("fee")) or {}
    try:
        bps = int(((fee.get("newerTransferFee") or fee.get("olderTransferFee") or {}).get("transferFeeBasisPoints")) or 0)
    except (TypeError, ValueError, AttributeError):
        bps = 0
    if bps:
        why.append(f"комиссия за перевод {bps / 100:g}%")
    rug = r.get("rug")
    lp = _f((rug or {}).get("lp_locked_pct"))
    if rug is None:
        why.append("RugCheck не проверял")
    elif lp is None or lp < MIN_LP_LOCK:
        why.append(f"заблокировано {lp:.0f}% ликвидности < {MIN_LP_LOCK}%" if lp is not None else "доля заблокированной ликвидности неизвестна")
    top = _f((r.get("holders") or {}).get("top10_pct"))
    if top is not None and top > MAX_TOP10:
        why.append(f"топ-10 кошельков {top:.0f}% > {MAX_TOP10}%")
    fdv = _f(r.get("fdv"))
    if fdv and liq and fdv / liq > MAX_FDV_LIQ:
        why.append(f"оценка в {fdv / liq:.0f} раз больше пула")
    c24 = _f(r.get("chg24h"))
    if c24 is not None and c24 > MAX_CHG24:
        why.append(f"уже +{c24:.0f}% за сутки — погоня")
    return why


def _row(r: dict) -> dict:
    h = r.get("holders") or {}
    return {"symbol": r.get("symbol"), "name": r.get("name"), "mint": r.get("mint"), "link": r.get("link"), "price": r.get("price"),
            "age_h": r.get("age_h"), "liquidity": r.get("liquidity"), "vol24": r.get("vol24"), "buyers24": r.get("buyers24"),
            "top10_pct": h.get("top10_pct"), "lp_locked_pct": (r.get("rug") or {}).get("lp_locked_pct"), "chg24h": r.get("chg24h"),
            "score": r.get("score")}


def ds_prices(mints: list[str], get=None) -> dict:
    """{mint: {"price", "liquidity"}} from DexScreener (the deepest pool of each token), ≤ 30 mints per call; {} on failure."""
    get = get or (lambda url: json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"}), timeout=10).read()))
    out = {}
    for i in range(0, len(mints), 30):
        try:
            j = get(DS_TOKENS + ",".join(mints[i:i + 30]))
        except Exception:  # noqa: BLE001
            continue
        for pr in j if isinstance(j, list) else []:
            m = (pr.get("baseToken") or {}).get("address")
            liq = _f((pr.get("liquidity") or {}).get("usd"), 0.0)
            if m and liq >= (out.get(m) or {}).get("liquidity", -1):
                out[m] = {"price": _f(pr.get("priceUsd")), "liquidity": liq}
    return out


def _db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS nm_picks(mint TEXT, symbol TEXT, day INTEGER, picked INTEGER, entry REAL, usd REAL, qty REAL, qty0 REAL,
        cash_out REAL, body INTEGER, peak REAL, last_px REAL, last_liq REAL, last_t INTEGER, closed INTEGER, exit_px REAL, pnl_usd REAL,
        why_exit TEXT, link TEXT, facts TEXT, PRIMARY KEY(mint, picked));
      CREATE TABLE IF NOT EXISTS nm_meta(k TEXT PRIMARY KEY, v TEXT);
      CREATE TABLE IF NOT EXISTS nm_equity(time INTEGER PRIMARY KEY, equity REAL);""")


def _cash(conn) -> float:
    r = conn.execute("SELECT v FROM nm_meta WHERE k='cash'").fetchone()
    return float(r[0]) if r else START


def _set_cash(conn, v: float) -> None:
    conn.execute("INSERT OR REPLACE INTO nm_meta VALUES('cash', ?)", (repr(float(v)),))


def _manage(conn, px: dict, now: int, ev: list) -> None:
    cash = _cash(conn)
    for (mint, sym, entry, usd, qty, qty0, cash_out, body, peak, last_px, last_liq, last_t, picked) in conn.execute(
            "SELECT mint, symbol, entry, usd, qty, qty0, cash_out, body, peak, last_px, last_liq, last_t, picked FROM nm_picks WHERE closed IS NULL").fetchall():
        q = px.get(mint) or {}
        p, liq = q.get("price"), q.get("liquidity")
        if p:
            last_px, last_liq, last_t = p, liq, now
        why, fill = None, None
        if p and liq is not None and liq < RUG_LIQ:
            why, fill = f"пул опустел (${liq / 1e3:.1f}k) — считаем потерю 100%", 0.0
        elif not p and now - (last_t or picked) >= NO_PRICE_H * H:
            why, fill = f"нет цены {NO_PRICE_H} ч — считаем потерю 100%", 0.0
        elif p:
            peak = max(peak or entry, p)
            if not body and p <= entry * (1 + STOP):
                why, fill = f"стоп −{abs(STOP) * 100:.0f}%", p * (1 - SLIP)
            elif not body and p >= entry * BODY_X:
                part = qty0 * BODY_FRAC
                v = part * p * (1 - SLIP)
                cash += v; cash_out += v; qty -= part; body = 1
                ev.append(f"Новые мемы: {sym} ×{BODY_X:g} — продали половину по {p:.6g}, получили ${v:,.0f} (тело вернулось).")
            elif body and p <= peak * (1 - TRAIL):
                why, fill = f"откат {TRAIL * 100:.0f}% от пика", p * (1 - SLIP)
            elif now - picked >= MAX_DAYS * DAY:
                why, fill = f"прошло {MAX_DAYS} дней", p * (1 - SLIP)
        if why is not None:
            v = qty * fill
            cash += v; cash_out += v
            pnl = cash_out - usd
            conn.execute("UPDATE nm_picks SET qty=0, cash_out=?, body=?, peak=?, last_px=?, last_liq=?, last_t=?, closed=?, exit_px=?, pnl_usd=?, why_exit=? "
                         "WHERE mint=? AND picked=?", (cash_out, body, peak, last_px, last_liq, last_t, now, fill, round(pnl, 2), why, mint, picked))
            ev.append(f"Новые мемы: закрыли {sym} — {why}; итог ${pnl:+,.0f} ({pnl / usd * 100:+.0f}%).")
        else:
            conn.execute("UPDATE nm_picks SET qty=?, cash_out=?, body=?, peak=?, last_px=?, last_liq=?, last_t=? WHERE mint=? AND picked=?",
                         (qty, cash_out, body, peak, last_px, last_liq, last_t, mint, picked))
    _set_cash(conn, cash)


def _equity(conn) -> float:
    return _cash(conn) + sum((q or 0) * (p or 0) * (1 - SLIP) for q, p in conn.execute("SELECT qty, last_px FROM nm_picks WHERE closed IS NULL"))


def step(conn: sqlite3.Connection, sol_rows: list[dict], now: int | None = None, price_fn=ds_prices) -> dict:
    """One run: mark the open picks, pick today's new memes (paper), return the panel block (memes.json → new_memes)."""
    now = now or int(time.time() * 1000)
    _db(conn)
    ev: list[str] = []
    by_mint = {r.get("mint"): r for r in sol_rows or [] if r.get("mint")}
    held = [m for (m,) in conn.execute("SELECT mint FROM nm_picks WHERE closed IS NULL")]
    px = {m: {"price": _f(by_mint[m].get("price")), "liquidity": _f(by_mint[m].get("liquidity"))} for m in held if m in by_mint and by_mint[m].get("price")}
    miss = [m for m in held if m not in px]
    if miss:
        try:
            px.update({k: v for k, v in (price_fn(miss) or {}).items() if v.get("price")})
        except Exception:  # noqa: BLE001
            pass
    _manage(conn, px, now, ev)
    # today's candidates
    checked = [(r, check(r)) for r in sol_rows or [] if r.get("mint") and r.get("price")]
    cands = sorted([r for r, w in checked if not w], key=lambda r: (-(r.get("score") or 0), -(_f(r.get("vol24"), 0.0))))
    near = sorted([(r, w) for r, w in checked if w and AGE_H[0] <= (r.get("age_h") or -1) <= AGE_H[1]], key=lambda x: (len(x[1]), -(x[0].get("score") or 0)))[:5]
    day = now // DAY
    n_today = conn.execute("SELECT COUNT(*) FROM nm_picks WHERE day=?", (day,)).fetchone()[0]
    recent = {m for (m,) in conn.execute("SELECT mint FROM nm_picks WHERE picked >= ?", (now - REPICK_DAYS * DAY,))}
    for r in cands:
        if n_today >= PICKS_PER_DAY:
            break
        if r["mint"] in recent:
            continue
        cash = _cash(conn)
        usd = round(min(cash, _equity(conn) * PICK_SHARE), 2)
        if usd < 10:
            break
        entry = float(r["price"]) * (1 + SLIP)
        qty = usd / entry
        conn.execute("INSERT INTO nm_picks VALUES(?,?,?,?,?,?,?,?,0,0,?,?,?,?,NULL,NULL,NULL,NULL,?,?)",
                     (r["mint"], r.get("symbol"), day, now, entry, usd, qty, qty, entry, float(r["price"]), _f(r.get("liquidity")), now,
                      r.get("link"), json.dumps(_row(r), ensure_ascii=False)))
        _set_cash(conn, cash - usd)
        n_today += 1
        recent.add(r["mint"])
        ev.append(f"Новые мемы: бумажная покупка {r.get('symbol')} на ${usd:,.0f} по {entry:.6g} (цена DEX +{SLIP * 100:g}% издержек).")
    eq = _equity(conn)
    conn.execute("INSERT OR REPLACE INTO nm_equity VALUES(?, ?)", (now, round(eq, 2)))
    conn.commit()
    cols = ("mint", "symbol", "day", "picked", "entry", "usd", "qty", "qty0", "cash_out", "body", "peak", "last_px", "last_liq", "last_t",
            "closed", "exit_px", "pnl_usd", "why_exit", "link", "facts")
    allp = [dict(zip(cols, x)) for x in conn.execute(f"SELECT {', '.join(cols)} FROM nm_picks ORDER BY picked DESC LIMIT 60")]
    for p in allp:
        p["facts"] = json.loads(p["facts"]) if p.get("facts") else None
        if p["closed"] is None:
            val = (p["qty"] or 0) * (p["last_px"] or 0) * (1 - SLIP)
            p["value"] = round(val, 2)
            p["pnl_usd"] = round(p["cash_out"] + val - p["usd"], 2)
            p["stale_h"] = round((now - (p["last_t"] or p["picked"])) / H, 1)
        p["pnl_pct"] = round(p["pnl_usd"] / p["usd"] * 100, 1) if p.get("usd") and p.get("pnl_usd") is not None else None
    closed = [p for p in allp if p["closed"] is not None]
    hist = conn.execute("SELECT time, equity FROM nm_equity ORDER BY time").fetchall()
    if len(hist) > 240:
        k = (len(hist) - 1) / 239
        hist = [hist[round(i * k)] for i in range(240)]
    first = conn.execute("SELECT MIN(time) FROM nm_equity").fetchone()[0] or now
    return {"asof": now, "name": "Новые мемы дня", "status": "эксперимент · только бумага", "rules_ru": RULES_RU, "book_ru": BOOK_RU,
            "warnings": WARNINGS, "retro": RETRO, "events": ev,
            "limits": {"age_h": list(AGE_H), "min_liq": MIN_LIQ, "min_vol": MIN_VOL, "max_turnover": MAX_TURN, "min_buyers": MIN_BUYERS,
                       "max_top10_pct": MAX_TOP10, "min_lp_locked_pct": MIN_LP_LOCK, "max_fdv_liq": MAX_FDV_LIQ, "max_chg24_pct": MAX_CHG24,
                       "picks_per_day": PICKS_PER_DAY, "pick_share": PICK_SHARE, "slip": SLIP, "stop": STOP, "body_x": BODY_X,
                       "trail": TRAIL, "max_days": MAX_DAYS},
            "today": {"day": day, "checked": len(checked), "candidates": [_row(r) for r in cands[:8]],
                      "near": [{**_row(r), "why_not": w[:3]} for r, w in near],
                      "picks": [p for p in allp if p["day"] == day]},
            "book": {"start": START, "since": first, "equity": round(eq, 2), "cash": round(_cash(conn), 2), "pnl_usd": round(eq - START, 2),
                     "pnl_pct": round((eq / START - 1) * 100, 2), "open": [p for p in allp if p["closed"] is None], "closed": closed[:30],
                     "stats": {"trades": len(closed), "wins": sum(1 for p in closed if (p["pnl_usd"] or 0) > 0),
                               "avg_pct": round(sum(p["pnl_pct"] or 0 for p in closed) / len(closed), 1) if closed else None},
                     "history": [[t, e] for t, e in hist]}}
