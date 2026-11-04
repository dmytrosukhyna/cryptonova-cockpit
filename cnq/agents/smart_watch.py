"""«Список токенов китов» — every token a tracked smart wallet buys goes into a persistent watchlist (04.11.2026 22:38, owner: «риски
могут сняться позже — система должна анализировать постоянно и накапливать знания»). Called by smart_money.run every hourly run.
Information only — no orders.

  * sm_tok (data/whales_ext.sqlite): one row per token — first seen, who bought and how much, the latest market check (price,
    liquidity, market cap, holders concentration, GoPlus), risk flags, status, forward returns from the first smart buy.
  * sm_tok_ev: every change with its time — new buyer, a wallet selling, a risk that appeared or cleared, a status change.
  * Status: «новая» (first 24 h) / «под наблюдением» / «риски снялись» (had risk flags, now none) / «накопление» (≥ 2 wallets hold it)
    / «продают» (a tracked wallet sold within 7 days) / «выбыла» (no pool for 3 days, every buyer sold a week ago, or 45 days quiet).
  * Learning: 1 / 7 / 30-day returns from the first smart buy → data/smart_money/token_outcomes.jsonl with the token's pattern at
    first sight (buyers, wallets, risk flags, age, liquidity) → hit rate and median per pattern («что срабатывало»), small samples marked.
  * Wallet discovery: for a token that rose ≥ 50 % after our first smart buy, the wallets that bought it in the 14 days BEFORE that
    buy (Blockscout token transfers, Ethereum) get a point; ≥ 2 such tokens → an auto-found candidate smart wallet that is scanned
    like the others, shown apart from the publicly labelled ones.
  * data/smart_money/watchlist/<day>.json — one snapshot a day (committed by the pipeline).
"""
from __future__ import annotations

import json
import os
import statistics as st
import time
from datetime import datetime, timezone

H, DAY = 3_600_000, 86_400_000
RECHECK_MAX = 200                     # tokens re-priced per run (oldest check first)
GP_PER_RUN = 12
GT_PER_RUN = 6
MINE_PER_RUN = 3
WIN_UP = 0.50                         # «big move» for wallet discovery
AUTO_MIN_TOKENS = 2
AUTO_SCAN_MAX = 10
SMALL_N = 10
HORIZONS = (1, 7, 30)
STATUS_ORDER = ["накопление", "риски снялись", "продают", "новая", "под наблюдением", "выбыла"]
NO_POOL = "нет торгов (пула нет) — похоже на спам-рассылку"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sm_tok (key TEXT PRIMARY KEY, chain TEXT, address TEXT, symbol TEXT, name TEXT, first_t INTEGER,
  first_price REAL, first_price_src TEXT, status TEXT, status_t INTEGER, last_check INTEGER, gp_t INTEGER, m TEXT, buyers TEXT,
  flags TEXT, risk_ever INTEGER DEFAULT 0, ret TEXT, profile TEXT, last_act INTEGER, mined INTEGER DEFAULT 0, sec TEXT);
CREATE TABLE IF NOT EXISTS sm_tok_ev (key TEXT, t INTEGER, kind TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS sm_tok_ev_i ON sm_tok_ev (key, t);
CREATE TABLE IF NOT EXISTS sm_wallet (address TEXT PRIMARY KEY, chain TEXT, first_t INTEGER, score INTEGER, tokens TEXT, status TEXT);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _j(x, d):
    try:
        return json.loads(x) if x else d
    except Exception:  # noqa: BLE001
        return d


def _ev(conn, key, t, kind, text) -> None:
    conn.execute("INSERT INTO sm_tok_ev VALUES (?,?,?,?)", (key, t, kind, text))


def load(conn) -> dict[str, dict]:
    cols = [d[1] for d in conn.execute("PRAGMA table_info(sm_tok)")]
    out = {}
    for r in conn.execute("SELECT * FROM sm_tok"):
        x = dict(zip(cols, r))
        for k, d in (("m", {}), ("buyers", {}), ("flags", []), ("ret", {}), ("profile", {}), ("sec", {})):
            x[k] = _j(x[k], d)
        out[x["key"]] = x
    return out


def save(conn, x: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO sm_tok (key, chain, address, symbol, name, first_t, first_price, first_price_src, status, status_t, "
                 "last_check, gp_t, m, buyers, flags, risk_ever, ret, profile, last_act, mined, sec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (x["key"], x["chain"], x["address"], x["symbol"], x.get("name"), x["first_t"], x.get("first_price"), x.get("first_price_src"),
                  x.get("status"), x.get("status_t"), x.get("last_check"), x.get("gp_t"), json.dumps(x.get("m") or {}),
                  json.dumps(x.get("buyers") or {}, ensure_ascii=False), json.dumps(x.get("flags") or [], ensure_ascii=False),
                  int(bool(x.get("risk_ever"))), json.dumps(x.get("ret") or {}), json.dumps(x.get("profile") or {}, ensure_ascii=False),
                  x.get("last_act"), int(x.get("mined") or 0), json.dumps(x.get("sec") or {})))


# ------------------------------------------------------------------------------------------------------------ 1. buys / sells
def ingest(conn, toks: dict, now: int, scans: list[dict], ignore: set[str], meta: dict) -> int:
    """scans (per wallet × chain: 7-day flows per token) → new tokens, new buyers, sellers. Returns the number of new tokens."""
    new = 0
    for s in scans:
        w = s["wallet"]
        wm = meta.get(w) or {"label": s.get("label"), "kind": s.get("kind")}
        for ta, x in (s.get("flows") or {}).items():
            tok = x.get("tok") or {}
            sym = str(tok.get("symbol") or "").strip()
            if not sym or sym.upper() in ignore:
                continue
            key = f"{s['chain']}:{ta}"
            net = x["in"] - x["out"]
            t = toks.get(key)
            if net > 0 and x.get("n_in"):
                if t is None:
                    t = toks[key] = {"key": key, "chain": s["chain"], "address": ta, "symbol": sym[:16], "name": str(tok.get("name") or "")[:40],
                                     "first_t": x.get("first_in_t") or now, "status": "новая", "status_t": now, "buyers": {}, "flags": [],
                                     "m": {}, "ret": {}, "profile": {}, "last_act": now, "_new": True}
                    new += 1
                    _ev(conn, key, now, "add", f"добавлена: купил {wm['label']}")
                b = t["buyers"].get(w)
                if b is None:
                    if not t.get("_new"):
                        _ev(conn, key, now, "buyer", f"новый покупатель: {wm['label']}")
                    b = t["buyers"][w] = {"label": wm["label"], "kind": wm.get("kind"), "auto": bool(wm.get("auto")),
                                          "first_t": x.get("first_in_t") or now, "chain": s["chain"]}
                    t["last_act"] = now
                elif b.get("sold"):
                    b.pop("sold", None); b.pop("sold_t", None)
                    _ev(conn, key, now, "buyer", f"снова покупает: {wm['label']}")
                    t["last_act"] = now
                b["amount"] = net
                b["first_t"] = min(b.get("first_t") or now, x.get("first_in_t") or now)
                t["first_t"] = min(t["first_t"], b["first_t"])
                t["_touched"] = True
            elif net < 0 and t is not None and w in t["buyers"] and not t["buyers"][w].get("sold"):
                t["buyers"][w].update(sold=True, sold_t=now)
                t["last_act"] = now
                _ev(conn, key, now, "sell", f"продаёт: {wm['label']}")
                t["_touched"] = True
    return new


# ------------------------------------------------------------------------------------------------------------ 3. status
def status_of(t: dict, now: int) -> str:
    buyers = t.get("buyers") or {}
    active = [b for b in buyers.values() if not b.get("sold")]
    sold_ts = [b.get("sold_t") or 0 for b in buyers.values() if b.get("sold")]
    m = t.get("m") or {}
    if (not m.get("pool") and now - t["first_t"] > 3 * DAY) or (not active and sold_ts and now - max(sold_ts) > 7 * DAY) \
            or now - (t.get("last_act") or t["first_t"]) > 45 * DAY and len(active) < 2:
        return "выбыла"
    if sold_ts and now - max(sold_ts) <= 7 * DAY:
        return "продают"
    if len(active) >= 2:
        return "накопление"
    if t.get("risk_ever") and not t.get("flags") and m.get("pool"):
        return "риски снялись"
    if now - t["first_t"] < DAY:
        return "новая"
    return "под наблюдением"


def set_flags(conn, t: dict, flags: list[str], now: int) -> None:
    old = set(t.get("flags") or [])
    key = lambda f: f.split(" (")[0].split(":")[0]           # noqa: E731 — «мало ликвидности ($12k)» → the kind of flag
    ok, nk = {key(f): f for f in old}, {key(f): f for f in flags}
    if t.get("last_check_done"):
        for k in ok.keys() - nk.keys():
            _ev(conn, t["key"], now, "risk_off", f"риск снят: {ok[k]}")
        for k in nk.keys() - ok.keys():
            _ev(conn, t["key"], now, "risk_on", f"новый риск: {nk[k]}")
    t["flags"] = flags
    if flags:
        t["risk_ever"] = True


# ------------------------------------------------------------------------------------------------------------ 4. learning
def bucket_age(d) -> str:
    if d is None:
        return "возраст неизвестен"
    return "моложе 7 дн" if d < 7 else "7–30 дн" if d < 30 else "30–180 дн" if d < 180 else "старше 180 дн"


def bucket_liq(v) -> str:
    if not v:
        return "без пула"
    return "до $100k" if v < 100_000 else "$100k–1M" if v < 1_000_000 else "от $1M"


def flag_kinds(flags: list[str]) -> list[str]:
    out = []
    for f in flags or []:
        k = ("ликвидность" if "ликвидност" in f else "новый токен" if "новый токен" in f else "ловушка/налог" if ("honeypot" in f or "налог" in f)
             else "концентрация" if "топ-10" in f else "спам" if "пула нет" in f else "маркетмейкеры" if "маркетмейкер" in f else "другое")
        if k not in out:
            out.append(k)
    return out


def outcome_row(t: dict, h: int, now: int, price: float) -> dict:
    p = t.get("profile") or {}
    return {"key": t["key"], "symbol": t["symbol"], "h": h, "t0": t["first_t"], "t": now, "price0": t["first_price"], "price": price,
            "ret": round((price / t["first_price"] - 1) * 100, 2), "late": now - (t["first_t"] + h * DAY) > 3 * DAY,
            "n_buyers": len(t.get("buyers") or {}), "wallets": sorted({b["label"] for b in (t.get("buyers") or {}).values()})[:10],
            "flags": p.get("flag_kinds", []), "age": p.get("age_bucket"), "liq": p.get("liq_bucket"), "price_src": t.get("first_price_src")}


def patterns(rows: list[dict]) -> list[dict]:
    """per pattern: n / hit rate / median return for 1, 7 and 30 days (late measurements left out)"""
    rows = [r for r in rows if not r.get("late")]
    groups: dict[tuple, dict] = {}

    def add(kind, name, r):
        g = groups.setdefault((kind, name), {"kind": kind, "name": name, "h": {}})
        g["h"].setdefault(str(r["h"]), []).append(r["ret"])
    for r in rows:
        n = r.get("n_buyers") or 1
        add("Сколько умных кошельков", "1 кошелёк" if n == 1 else "2 кошелька" if n == 2 else "3 и больше", r)
        for w in r.get("wallets") or []:
            add("Кошелёк", w, r)
        fk = r.get("flags") or []
        add("Риски при первой проверке", "без рисков" if not fk else "есть риски", r)
        for k in fk:
            add("Риски при первой проверке", k, r)
        add("Возраст токена", r.get("age") or "возраст неизвестен", r)
        add("Ликвидность", r.get("liq") or "без пула", r)
    out = []
    for g in groups.values():
        hs = {}
        for h, xs in g["h"].items():
            hs[h] = {"n": len(xs), "hit": round(sum(1 for x in xs if x > 0) / len(xs), 3), "median": round(st.median(xs), 2), "small": len(xs) < SMALL_N}
        out.append({"kind": g["kind"], "name": g["name"], "h": hs, "n": max(v["n"] for v in hs.values())})
    kinds = ["Сколько умных кошельков", "Риски при первой проверке", "Возраст токена", "Ликвидность", "Кошелёк"]
    out.sort(key=lambda g: (kinds.index(g["kind"]), -g["n"]))
    return out


# ------------------------------------------------------------------------------------------------------------ 5. wallet discovery
def early_buyers(items: list[dict], t_from: int, t_to: int, skip: set[str], iso_ms) -> set[str]:
    out = set()
    for it in items:
        t = iso_ms(it.get("timestamp"))
        if t is None or t < t_from or t > t_to:
            continue
        to = it.get("to") or {}
        a = str(to.get("hash") or "").lower()
        if a and not to.get("is_contract") and a not in skip:
            out.add(a)
    return out


def auto_wallets(conn, limit: int = AUTO_SCAN_MAX) -> list[dict]:
    rows = conn.execute("SELECT address, chain, score, tokens FROM sm_wallet WHERE status='кандидат' ORDER BY score DESC, first_t LIMIT ?", (limit,)).fetchall()
    return [{"label": f"{a[:6]}…{a[-4:]}", "kind": "найден автоматически", "address": a, "chains": [c or "eth"], "auto_found": True,
             "score": s, "tokens": _j(tk, []), "source": f"https://etherscan.io/address/{a}",
             "source_note": f"купил раньше наших китов {s} токена(ов), которые потом выросли на 50%+"} for a, c, s, tk in rows]


# ------------------------------------------------------------------------------------------------------------ entry point
def update(conn, now: int, scans: list[dict], meta: dict, get, src, deadline: float, ctx: dict, data_dir: str) -> dict:
    """the whole watchlist step; ctx carries smart_money's helpers and caches (ds_fetch, ds_best, goplus, …). Never raises on a source."""
    ensure_schema(conn)
    toks = load(conn)
    new = ingest(conn, toks, now, scans, ctx["IGNORE"], meta)
    # market re-check: oldest check first (new ones first of all)
    active = [t for t in toks.values() if t.get("status") != "выбыла" or t.get("_touched")]
    active.sort(key=lambda t: (not t.get("_new"), t.get("last_check") or 0))
    batch = active[:RECHECK_MAX]
    pairs = list(ctx.get("pairs") or [])
    have = {(p.get("chainId"), str((p.get("baseToken") or {}).get("address", "")).lower()) for p in pairs}
    for ch in ctx["BS"]:
        need = [t["address"] for t in batch if t["chain"] == ch and (ctx["DS_CHAIN"][ch], t["address"]) not in have]
        if need and time.time() < deadline:
            pairs += ctx["ds_fetch"](get, ch, need, src, deadline=deadline)
    gp_n = gt_n = 0
    for t in batch:
        if time.time() > deadline:
            break
        m = ctx["ds_best"](pairs, t["chain"], t["address"])
        prev_m = t.get("m") or {}
        t["m"] = m or {"pool": None}
        if m and m.get("price"):
            for b in t["buyers"].values():
                if b.get("amount") is not None:
                    b["usd"] = round(b["amount"] * m["price"])
            if not t.get("first_price"):
                p0 = None
                if m.get("pool") and gt_n < GT_PER_RUN and now - t["first_t"] > H:
                    gt_n += 1
                    p0 = ctx["gt_price_at"](get, t["chain"], m["pool"], t["first_t"], src)
                t["first_price"], t["first_price_src"] = (p0, "свеча при первой покупке") if p0 else (m["price"], "цена при первой проверке")
        # GoPlus once a day per token (only tokens that trade)
        if m and m.get("pool") and gp_n < GP_PER_RUN and now - (t.get("gp_t") or 0) >= DAY:
            gp_n += 1
            r = ctx["goplus"](get, t["chain"], t["address"], src)
            if r is not None:
                t["gp_t"] = now
                t["sec"] = {"flags": ctx["goplus_flags"](r)[0], "info": ctx["goplus_flags"](r)[1]}
        tok_view = dict(m or {}, mm_only=bool(t["buyers"]) and all("маркетмейкер" in str(b.get("kind")) for b in t["buyers"].values()),
                        gp_flags=(t.get("sec") or {}).get("flags") or [])
        flags = ctx["risk_flags"](tok_view, now) if m and m.get("pool") else [NO_POOL]
        t["last_check_done"] = bool(t.get("last_check"))
        set_flags(conn, t, flags, now)
        t["last_check"] = now
        if not t.get("profile") and (m or now - t["first_t"] > H):
            t["profile"] = {"t": now, "age_d": round((now - m["created"]) / DAY, 1) if m and m.get("created") else None,
                            "liq": (m or {}).get("liq"), "flags": flags, "flag_kinds": flag_kinds(flags)}
            t["profile"]["age_bucket"] = bucket_age(t["profile"]["age_d"])
            t["profile"]["liq_bucket"] = bucket_liq(t["profile"]["liq"])
        if prev_m.get("pool") and not (m or {}).get("pool"):
            _ev(conn, t["key"], now, "pool", "пул пропал — торгов не видно")
        # forward returns from the first smart buy
        if m and m.get("price") and t.get("first_price"):
            for h in HORIZONS:
                if str(h) not in t["ret"] and now >= t["first_t"] + h * DAY:
                    row = outcome_row(t, h, now, m["price"])
                    t["ret"][str(h)] = {"ret": row["ret"], "t": now, "late": row["late"]}
                    ctx["append_jsonl"](os.path.join(data_dir, "token_outcomes.jsonl"), [row])
            t["chg_pct"] = round((m["price"] / t["first_price"] - 1) * 100, 1)
    # statuses
    for t in toks.values():
        s = status_of(t, now)
        if s != t.get("status"):
            if not t.get("_new"):
                _ev(conn, t["key"], now, "status", f"статус: {t.get('status')} → {s}")
            t["status"], t["status_t"] = s, now
        save(conn, t)
    conn.execute("DELETE FROM sm_tok_ev WHERE t<?", (now - 120 * DAY,))
    conn.commit()
    # wallet discovery (Ethereum: Blockscout token transfers)
    mined = 0
    known = set(meta) | set(ctx.get("exchanges") or [])
    for t in toks.values():
        if mined >= MINE_PER_RUN or time.time() > deadline:
            break
        up = (t.get("ret") or {}).get("7", {}).get("ret")
        cur = t.get("chg_pct")
        if t["chain"] != "eth" or t.get("mined") or not ((up is not None and up >= WIN_UP * 100) or (cur is not None and cur >= WIN_UP * 100)):
            continue
        mined += 1
        items, params = [], ""
        try:
            for _ in range(3):
                j = get(f"{ctx['BS']['eth']}/api/v2/tokens/{t['address']}/transfers{params}")
                src.ok("blockscout")
                its = (j or {}).get("items") or []
                items += its
                ts = [ctx["iso_ms"](i.get("timestamp")) for i in its]
                npp = (j or {}).get("next_page_params")
                if not its or not npp or (ts and min(x for x in ts if x) < t["first_t"] - 14 * DAY):
                    break
                params = "?" + "&".join(f"{k}={v}" for k, v in npp.items() if v is not None)
                time.sleep(0.25)
        except Exception as e:  # noqa: BLE001
            src.fail("blockscout", e)
            continue
        for a in early_buyers(items, t["first_t"] - 14 * DAY, t["first_t"], known, ctx["iso_ms"]):
            r = conn.execute("SELECT score, tokens FROM sm_wallet WHERE address=?", (a,)).fetchone()
            tk = _j(r[1], []) if r else []
            if t["symbol"] in tk:
                continue
            tk.append(t["symbol"])
            sc = len(tk)
            conn.execute("INSERT OR REPLACE INTO sm_wallet VALUES (?,?,?,?,?,?)",
                         (a, "eth", now if not r else conn.execute("SELECT first_t FROM sm_wallet WHERE address=?", (a,)).fetchone()[0], sc,
                          json.dumps(tk[-20:], ensure_ascii=False), "кандидат" if sc >= AUTO_MIN_TOKENS else "наблюдаем"))
        t["mined"] = 1
        save(conn, t)
        conn.commit()
    # daily snapshot
    snap = os.path.join(data_dir, "watchlist", datetime.fromtimestamp(now / 1000, tz=timezone.utc).strftime("%Y-%m-%d") + ".json")
    rows = public(conn, toks, now, ev_n=0)
    if not os.path.exists(snap):
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        with open(snap, "w", encoding="utf-8") as fh:
            json.dump({"asof": now, "tokens": rows}, fh, ensure_ascii=False, separators=(",", ":"))
    outs = ctx["read_jsonl"](os.path.join(data_dir, "token_outcomes.jsonl"))
    counts = {s: sum(1 for t in toks.values() if t.get("status") == s) for s in STATUS_ORDER}
    aw = conn.execute("SELECT address, score, tokens, status FROM sm_wallet ORDER BY score DESC LIMIT 20").fetchall()
    return {"tokens": public(conn, toks, now)[:400], "n": len(toks), "new": new, "counts": counts, "patterns": patterns(outs),
            "outcomes_n": len([o for o in outs if not o.get("late")]),
            "wallets_auto": [{"address": a, "score": s, "tokens": _j(tk, []), "status": stt} for a, s, tk, stt in aw],
            "about_ru": "Каждый токен, который купил любой отслеживаемый кошелёк, попадает в список — даже если не прошёл фильтры риска. "
                        "Раз в час система заново проверяет цену, ликвидность, капитализацию, концентрацию держателей и GoPlus, ищет новых "
                        "покупателей и продавцов и записывает каждое изменение. Доходность считаем от цены при первой покупке умного кошелька."}


def public(conn, toks: dict, now: int, ev_n: int = 12) -> list[dict]:
    out = []
    for t in toks.values():
        m = t.get("m") or {}
        bs = sorted(t["buyers"].values(), key=lambda b: -(b.get("usd") or 0))
        row = {"key": t["key"], "chain": t["chain"], "address": t["address"], "symbol": t["symbol"], "name": t.get("name"), "status": t.get("status"),
               "status_t": t.get("status_t"), "first_t": t["first_t"], "first_price": t.get("first_price"), "first_price_src": t.get("first_price_src"),
               "price": m.get("price"), "liq": m.get("liq"), "mcap": m.get("mcap"), "fdv": m.get("fdv"), "url": m.get("url"),
               "age_days": round((now - m["created"]) / DAY, 1) if m.get("created") else None,
               "chg_pct": round((m["price"] / t["first_price"] - 1) * 100, 1) if m.get("price") and t.get("first_price") else None,
               "flags": t.get("flags") or [], "risk_ever": bool(t.get("risk_ever")), "top10_pct": ((t.get("sec") or {}).get("info") or {}).get("top10_pct"),
               "n": len([b for b in bs if not b.get("sold")]), "n_all": len(bs),
               "buyers": [{"label": b["label"], "kind": b.get("kind"), "auto": b.get("auto"), "usd": b.get("usd"), "first_t": b.get("first_t"),
                           "sold": bool(b.get("sold"))} for b in bs][:10],
               "ret": {h: v.get("ret") for h, v in (t.get("ret") or {}).items() if not v.get("late")}, "last_check": t.get("last_check")}
        if ev_n:
            row["events"] = [{"t": e[0], "kind": e[1], "text": e[2]} for e in conn.execute(
                "SELECT t, kind, text FROM sm_tok_ev WHERE key=? ORDER BY t DESC LIMIT ?", (t["key"], ev_n))]
        out.append(row)
    out.sort(key=lambda r: (STATUS_ORDER.index(r["status"]) if r["status"] in STATUS_ORDER else 9, -(r["n"] or 0), -(r["status_t"] or 0)))
    return out
