"""CRYPTO NOVA as a signal provider on the OKX Signal Marketplace (22.10.2026). OFF until the owner switches it on.

Why: the owner lives in Greece; OKX copy trading (lead trader) is closed to him, the Signal Marketplace is open. A provider creates a
custom signal in OKX's Signal bot (MAIN account), gets a webhook URL and a signal token and sends TradingView-style alert messages;
subscribers create their own signal bots that trade on their own accounts. Start: a FREE signal, no profit share.

What it sends: the trades of the panel header's futures desks («отобранные сигналы», panel_header.nova_base = the main desk chosen by
cnq/agents/desk_select.py + imp; 28.10 — a desk that leaves the set keeps sending the exits of its trades live on the signal), as OKX
alert messages (OKX help «Trading Signal Bot Alert Message 2.0 Specifications», section B):
  • a desk opens a trade        → ENTER_LONG / ENTER_SHORT, market, investmentType percentage_investment, amount = the trade's share of
                                   the CRYPTO NOVA account (Σ equity of the header desks, as the header counts it as ONE account);
  • a desk takes a part off     → EXIT_LONG / EXIT_SHORT, investmentType percentage_position, amount = the part of what is still open;
  • a desk closes the trade     → EXIT_LONG / EXIT_SHORT, percentage_position 100.
Stop-loss and take-profit are NOT in OKX's alert message (they are the bot's own settings): the desk's stop and targets reach the
subscriber as the EXIT message when the desk exits.

Config — only /etc/cnq.env on the server (the engine service loads it; `systemctl restart cnq-engine` after a change):
  CNQ_OKX_SIGNAL=on, CNQ_OKX_SIGNAL_URL=<webhook URL from OKX>, CNQ_OKX_SIGNAL_TOKEN=<signal token from OKX>.
Optional: CNQ_OKX_SIGNAL_MAX_AGE_MIN (30) — an entry older than this is not sent; CNQ_OKX_SIGNAL_PCT — a fixed entry size in % of
the subscriber's bot instead of the desk's own share. Default: off. The token is never logged, never written to a file.

Rules:
  • idempotent: every event has a fixed id in data/agents.sqlite (okx_signal_events) — a restart never sends it twice;
  • a trade that opened before the sender was switched on (okx_signal_meta.on_since) is never sent, nor its exits;
  • an exit is sent only for a trade whose entry was sent;
  • OKX's signal bot is one-way (one position per instrument): while one desk's trade on a coin is live on the signal, another
    desk's trade on the same coin is skipped (logged);
  • a coin OKX does not list as <COIN>-USDT-SWAP is skipped with the reason; an unknown list (OKX unreachable, no cache) waits;
  • each post: 3 tries with backoff inside a run, then again next minute (entries until they are too old, exits up to 60 runs);
    nothing here can break the engine run — every error ends in the public log;
  • web/okx_signal.json: the public log (time, coin, action, desk, status) — no URL, no token.
It places no orders on any account of ours. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from . import panel_header as ph
from .panel_header import NOVA_BASE

DESKS = NOVA_BASE                                   # the default; the live set is the header's: ph.nova_base(web/desk_select.json)
DESK_RU = {"cons": "откуп падений", "bal": "откуп падений", "aggr": "откуп падений", "imp": "Импульс BTC", "own": "Сигналы CRYPTO NOVA"}
ENV_ON, ENV_URL, ENV_TOKEN = "CNQ_OKX_SIGNAL", "CNQ_OKX_SIGNAL_URL", "CNQ_OKX_SIGNAL_TOKEN"
MAX_LAG_S = 60                                      # OKX: [1, 3600], default 60 — a message older than this is not acted on
MAX_AGE_MIN = 30                                    # a limit fill carries its 15-minute candle's open time → up to ~17 min old
EXIT_MAX_TRIES = 60                                 # runs (≈ 1 h at one run a minute) before an exit is given up and flagged
MISSING_MS = 5 * 60_000                             # a sent trade gone from its desk (open and closed) this long → EXIT
TRIES, BACKOFF_S, BUDGET_S, HTTP_TIMEOUT_S = 3, (1.0, 3.0), 35.0, 10.0
SWAPS_FILE, SWAPS_TTL_MS = "okx_swaps.json", 12 * 3_600_000
OKX_SWAPS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
# the 18 core coins (cnq/config.py) — USDT perpetuals on OKX; used only while OKX's own list cannot be fetched
CORE_SWAPS = {f"{c}-USDT-SWAP" for c in ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "NEAR",
                                          "BCH", "TRX", "SUI", "APT", "ARB", "OP")}
ACTION_RU = {"ENTER_LONG": "вход лонг", "ENTER_SHORT": "вход шорт", "EXIT_LONG": "выход из лонга", "EXIT_SHORT": "выход из шорта"}


# ---------- config ----------
def config(env=None) -> dict:
    """{"on", "why" (when off), "url", "token", "max_age_ms", "pct"} — on only with the switch AND both values AND an https OKX URL"""
    env = os.environ if env is None else env
    url, tok = (env.get(ENV_URL) or "").strip(), (env.get(ENV_TOKEN) or "").strip()
    sw = (env.get(ENV_ON) or "").strip().lower() in ("on", "1", "yes", "true")
    why = None
    if not sw:
        why = f"{ENV_ON} не равен on"
    elif not url or not tok:
        why = "нет " + " и ".join(n for n, v in ((ENV_URL, url), (ENV_TOKEN, tok)) if not v)
    else:
        u = urllib.parse.urlparse(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or not (host == "okx.com" or host.endswith(".okx.com")):
            why = f"{ENV_URL}: нужен https-адрес OKX (…okx.com), токен на другой адрес не отправляется"
    try:
        age = float(env.get("CNQ_OKX_SIGNAL_MAX_AGE_MIN") or MAX_AGE_MIN)
    except ValueError:
        age = MAX_AGE_MIN
    try:
        pct = float(env.get("CNQ_OKX_SIGNAL_PCT") or 0) or None
    except ValueError:
        pct = None
    return {"on": why is None, "why": why, "url": url, "token": tok, "max_age_ms": int(age * 60_000),
            "pct": max(1.0, min(100.0, pct)) if pct else None, "has_url": bool(url), "has_token": bool(tok)}


# ---------- symbols ----------
def inst_of(sym: str) -> str:
    """a desk symbol (APT, APTUSDT, APT-USDT, 1000PEPE…) → the OKX USDT perpetual instId (APT-USDT-SWAP, PEPE-USDT-SWAP)"""
    from ..execution.okx_alloc import okx_base     # read-only helper: 1000PEPE → PEPE, KBONK → BONK …
    s = str(sym or "").upper().strip()
    for suf in ("-USDT-SWAP", "-USDT", "USDT"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    base, _ = okx_base(s)
    return f"{base}-USDT-SWAP"


def _fetch_swaps() -> set[str]:
    req = urllib.request.Request(OKX_SWAPS_URL, headers={"User-Agent": "cnq-okx-signal"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        d = json.loads(r.read().decode())
    out = {x["instId"] for x in d.get("data") or [] if isinstance(x, dict) and x.get("settleCcy") == "USDT"
           and x.get("state", "live") == "live" and str(x.get("instId", "")).endswith("-USDT-SWAP")}
    if not out:
        raise RuntimeError("okx instruments: empty")
    return out


def swaps(data_dir: str, now: int, fetch=_fetch_swaps) -> tuple[set[str], str]:
    """(live OKX USDT swaps, source): a fresh fetch every 12 h cached in data/okx_swaps.json; the cache (any age) when OKX fails;
    the 18 core coins as the last resort (source "core" — any other coin is then «unknown», not «not listed»)."""
    p = os.path.join(data_dir, SWAPS_FILE)
    cache = None
    try:
        with open(p, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:  # noqa: BLE001
        pass
    if cache and now - int(cache.get("t") or 0) < SWAPS_TTL_MS and cache.get("ids"):
        return set(cache["ids"]), "okx"
    try:
        ids = fetch()
        try:
            os.makedirs(data_dir, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"t": now, "ids": sorted(ids)}, f)
        except OSError:
            pass
        return ids, "okx"
    except Exception as e:  # noqa: BLE001
        print("okx signal: instrument list:", str(e)[:200])
    if cache and cache.get("ids"):
        return set(cache["ids"]), "okx-cache"
    return set(CORE_SWAPS), "core"


def listed(inst: str, ids: set[str], source: str) -> bool | None:
    """True / False, or None = unknown (only the built-in core list is at hand)"""
    if inst in ids:
        return True
    return None if source == "core" else False


# ---------- the message ----------
def iso(ms: int) -> str:
    """OKX: «yyyy-MM-ddTHH:mm:ss.SSSZ» (UTC)"""
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ms) % 1000:03d}Z"


def _amt(x: float) -> str:
    return f"{round(float(x), 2):g}"


def message(action: str, inst: str, token: str, now_ms: int, inv_type: str | None = None, amount: float | None = None) -> dict:
    """one OKX alert message (section B of the spec). Values are strings, as in OKX's own examples."""
    assert action in ACTION_RU, action
    m = {"action": action, "instrument": inst, "signalToken": token, "timestamp": iso(now_ms), "maxLag": str(MAX_LAG_S),
         "orderType": "market"}
    if inv_type:
        m["investmentType"] = inv_type
        m["amount"] = _amt(amount)
    return m


def entry_pct(t: dict, desks: dict, cfg: dict) -> float:
    """% of the subscriber's bot for an entry: the trade's notional ÷ Σ equity of the header desks (the CRYPTO NOVA account), so a
    bot at leverage 1× holds what the panel's account holds. CNQ_OKX_SIGNAL_PCT overrides it. Bounded 1…50 %."""
    if cfg.get("pct"):
        return round(cfg["pct"], 2)
    try:
        notional = float(t.get("qty") or 0) * float(t.get("entry") or 0)
        eq = sum(float(v.get("equity") or v.get("start_equity") or 0) for v in desks.values() if v)
        p = notional / eq * 100 if eq > 0 else 0
    except (TypeError, ValueError):
        p = 0
    return round(max(1.0, min(50.0, p or 10.0)), 2)


# ---------- the store ----------
def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS okx_signal_events(id TEXT PRIMARY KEY, desk TEXT, trade_id TEXT, symbol TEXT, inst TEXT,
                    kind TEXT, action TEXT, frac REAL, inv_type TEXT, amount REAL, opened INTEGER, created INTEGER, status TEXT,
                    tries INTEGER DEFAULT 0, sent INTEGER, note TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS okx_signal_meta(k TEXT PRIMARY KEY, v TEXT)")
    conn.commit()


def _meta(conn, k, d=None):
    r = conn.execute("SELECT v FROM okx_signal_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else d


def _set_meta(conn, k, v) -> None:
    if v is None:
        conn.execute("DELETE FROM okx_signal_meta WHERE k=?", (k,))
    else:
        conn.execute("INSERT OR REPLACE INTO okx_signal_meta(k, v) VALUES(?, ?)", (k, str(v)))


def _events(conn, desk, tid) -> dict:
    keys = ("id", "kind", "status", "frac", "inst", "action")
    return {r[0]: dict(zip(keys, r)) for r in conn.execute(
        f"SELECT {', '.join(keys)} FROM okx_signal_events WHERE desk=? AND trade_id=?", (desk, tid))}


def _add(conn, eid, desk, t, inst, kind, action, now, status="pending", frac=None, inv_type=None, amount=None, note=None) -> None:
    conn.execute("INSERT OR IGNORE INTO okx_signal_events(id, desk, trade_id, symbol, inst, kind, action, frac, inv_type, amount, opened, "
                 "created, status, tries, note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                 (eid, desk, t["id"], t.get("symbol"), inst, kind, action, frac, inv_type, amount, int(t.get("opened") or now), now,
                  status, note))


def _live_on(conn, inst: str, except_tid: str | None = None) -> str | None:
    """the desk whose trade holds this instrument on the signal now (entry pending/sent, no exit sent), else None"""
    for desk, tid in conn.execute("SELECT desk, trade_id FROM okx_signal_events WHERE inst=? AND kind='enter' AND status IN ('pending','sent')",
                                  (inst,)).fetchall():
        if tid == except_tid:
            continue
        if not conn.execute("SELECT 1 FROM okx_signal_events WHERE desk=? AND trade_id=? AND kind='exit' AND status='sent'",
                            (desk, tid)).fetchone():
            return desk
    return None


# ---------- 1. what the desks did → events ----------
def detect(conn: sqlite3.Connection, profiles: dict, now: int, on_since: int, cfg: dict, swap_ids: set[str], swap_src: str,
           keys: tuple | None = None) -> None:
    desks = {k: profiles.get(k) for k in (keys or DESKS) if profiles.get(k)}
    # 28.10: a desk that left the header's set (desk_select switched the main desk) — its trades already on the signal still get
    # their exits; it opens nothing new
    old = [d for (d,) in conn.execute("SELECT DISTINCT desk FROM okx_signal_events WHERE kind='enter' AND status IN ('pending','sent')")
           .fetchall() if d not in desks]
    for desk, v in [*desks.items(), *((d, profiles.get(d) or {}) for d in old)]:
        seen = set()
        for t in sorted(v.get("open") or [], key=lambda x: x.get("opened") or 0):
            if not t.get("id"):
                continue
            seen.add(t["id"])
            ev = _events(conn, desk, t["id"])
            enter = ev.get(f"{desk}:{t['id']}:enter")
            side = "LONG" if t.get("side") == "LONG" else "SHORT"
            inst = inst_of(t.get("symbol"))
            if enter is None:
                if desk not in desks:
                    continue                                    # 28.10: not in the set any more — no new entries
                if int(t.get("opened") or 0) < on_since:
                    continue                                    # opened before the sender was on — never sent
                eid = f"{desk}:{t['id']}:enter"
                if now - int(t.get("opened") or now) > cfg["max_age_ms"]:
                    _add(conn, eid, desk, t, inst, "enter", "ENTER_" + side, now, "skipped",
                         note=f"сделка старше {cfg['max_age_ms'] // 60_000} мин — вход не отправлен")
                    continue
                ok = listed(inst, swap_ids, swap_src)
                if ok is None:
                    print(f"okx signal: {inst} — список инструментов OKX недоступен, вход ждёт")
                    continue
                if ok is False:
                    _add(conn, eid, desk, t, inst, "enter", "ENTER_" + side, now, "skipped", note=f"OKX не торгует {inst}")
                    print(f"okx signal: skip {t.get('symbol')} ({desk}) — OKX не торгует {inst}")
                    continue
                busy = _live_on(conn, inst)
                if busy:
                    _add(conn, eid, desk, t, inst, "enter", "ENTER_" + side, now, "skipped",
                         note=f"по {inst} уже идёт сигнал стола {busy} (бот OKX держит одну позицию на пару)")
                    continue
                _add(conn, eid, desk, t, inst, "enter", "ENTER_" + side, now, frac=1.0, inv_type="percentage_investment",
                     amount=entry_pct(t, desks, cfg))
                continue
            if enter["status"] != "sent":
                continue
            try:
                q, ql = float(t.get("qty") or 0), float(t.get("qty_left") if t.get("qty_left") is not None else t.get("qty") or 0)
                f = round(ql / q, 4) if q > 0 else 1.0
            except (TypeError, ValueError):
                f = 1.0
            if 0 < f < 0.9999:
                _add(conn, f"{desk}:{t['id']}:part:{f:.4f}", desk, t, enter["inst"], "part", "EXIT_" + side, now, frac=f,
                     inv_type="percentage_position")
        for t in v.get("closed") or []:
            if not t.get("id"):
                continue
            seen.add(t["id"])
            ev = _events(conn, desk, t["id"])
            enter = ev.get(f"{desk}:{t['id']}:enter")
            if not enter:
                continue
            if enter["status"] == "pending":                    # closed before its entry got out — nothing to send
                conn.execute("UPDATE okx_signal_events SET status='skipped', note=? WHERE id=?",
                             ("сделка закрылась раньше, чем ушёл вход", enter["id"]))
                continue
            if enter["status"] == "sent":
                side = "LONG" if t.get("side") == "LONG" else "SHORT"
                _add(conn, f"{desk}:{t['id']}:exit", desk, t, enter["inst"], "exit", "EXIT_" + side, now, frac=0.0,
                     inv_type="percentage_position", amount=100.0, note=t.get("exit_kind"))
        # a trade live on the signal that vanished from its desk (a reset …): close it on the signal after MISSING_MS
        for (tid, inst, action, opened) in conn.execute(
                "SELECT trade_id, inst, action, opened FROM okx_signal_events WHERE desk=? AND kind='enter' AND status='sent'",
                (desk,)).fetchall():
            if tid in seen or f"{desk}:{tid}:exit" in _events(conn, desk, tid):
                _set_meta(conn, f"missing:{desk}:{tid}", None)
                continue
            first = int(_meta(conn, f"missing:{desk}:{tid}", 0) or 0)
            if not first:
                _set_meta(conn, f"missing:{desk}:{tid}", now)
            elif now - first >= MISSING_MS:
                t = {"id": tid, "symbol": inst.split("-")[0], "opened": opened}
                _add(conn, f"{desk}:{tid}:exit", desk, t, inst, "exit", action.replace("ENTER_", "EXIT_"), now, frac=0.0,
                     inv_type="percentage_position", amount=100.0, note="сделки больше нет на столе")
                _set_meta(conn, f"missing:{desk}:{tid}", None)
    conn.commit()


# ---------- 2. events → OKX ----------
def _clean(s: str, cfg: dict) -> str:
    s = str(s)
    for secret in (cfg.get("token"), cfg.get("url")):
        if secret:
            s = s.replace(secret, "***")
    return s[:200]


def post(url: str, body: dict, timeout: float = HTTP_TIMEOUT_S) -> tuple[bool, str]:
    """POST the alert as JSON. (ok, short note). OKX's answer is read for a non-zero «code»; the body is never logged."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "cnq-okx-signal"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(4000).decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__ + ": " + str(e)[:120]
    try:
        d = json.loads(raw)
    except ValueError:
        d = None
    if isinstance(d, dict) and str(d.get("code", "0")) not in ("0", "200"):
        return False, f"OKX code {d.get('code')}: {str(d.get('msg') or '')[:120]}"
    return 200 <= code < 300, f"HTTP {code}"


def _send_with_retry(cfg, body, sender, sleep, deadline) -> tuple[bool, str, int]:
    note, n = "", 0
    for i in range(TRIES):
        if time.monotonic() > deadline:
            break
        n += 1
        try:
            ok, note = sender(cfg["url"], body)
        except Exception as e:  # noqa: BLE001
            ok, note = False, type(e).__name__
        if ok:
            return True, _clean(note, cfg), n
        if i < TRIES - 1:
            sleep(BACKOFF_S[min(i, len(BACKOFF_S) - 1)])
    return False, _clean(note, cfg), n


def flush(conn: sqlite3.Connection, cfg: dict, now: int, sender=post, sleep=time.sleep, budget_s: float = BUDGET_S) -> list[dict]:
    """send the pending events, oldest first; returns what happened this run"""
    deadline = time.monotonic() + budget_s
    keys = ("id", "desk", "trade_id", "inst", "kind", "action", "frac", "inv_type", "amount", "opened", "tries")
    rows = [dict(zip(keys, r)) for r in conn.execute(
        f"SELECT {', '.join(keys)} FROM okx_signal_events WHERE status='pending' ORDER BY created, rowid").fetchall()]
    done = []

    def mark(e, status, note, tries=0, amount=None):
        conn.execute("UPDATE okx_signal_events SET status=?, note=?, tries=tries+?, sent=?, amount=COALESCE(?, amount) WHERE id=?",
                     (status, note, tries, now if status == "sent" else None, amount, e["id"]))
        conn.commit()
        done.append({"id": e["id"], "status": status, "note": note})

    for e in rows:
        ev = _events(conn, e["desk"], e["trade_id"])
        enter = ev.get(f"{e['desk']}:{e['trade_id']}:enter") or {}
        amount = e["amount"]
        if e["kind"] == "enter":
            if now - int(e["opened"] or now) > cfg["max_age_ms"]:
                mark(e, "failed", "вход не ушёл вовремя — устарел, не отправлен")
                continue
        else:
            if enter.get("status") == "pending":
                continue                                        # waits for its entry
            if enter.get("status") != "sent":
                mark(e, "skipped", "вход не отправлялся")
                continue
            if e["kind"] == "part":
                if f"{e['desk']}:{e['trade_id']}:exit" in ev:
                    mark(e, "skipped", "заменён полным выходом")
                    continue
                prev = min([1.0] + [x["frac"] for x in ev.values() if x["kind"] == "part" and x["status"] == "sent"
                                    and x["frac"] is not None])
                if e["frac"] >= prev - 1e-6:
                    mark(e, "skipped", "доля уже снята")
                    continue
                amount = round(max(0.01, min(100.0, (prev - e["frac"]) / prev * 100)), 2)
        if time.monotonic() > deadline:
            break                                               # next minute
        body = message(e["action"], e["inst"], cfg["token"], now, e["inv_type"], amount)
        ok, note, n = _send_with_retry(cfg, body, sender, sleep, deadline)
        if ok:
            mark(e, "sent", note, n, amount)
            if e["kind"] == "exit":
                conn.execute("UPDATE okx_signal_events SET status='skipped', note='заменён полным выходом' WHERE desk=? AND trade_id=? "
                             "AND kind='part' AND status='pending'", (e["desk"], e["trade_id"]))
                conn.commit()
            print(f"okx signal: sent {e['action']} {e['inst']} ({e['desk']})" + (f" {amount}%" if amount else ""))
        else:
            tries = int(e["tries"] or 0) + n
            give_up = e["kind"] != "enter" and tries >= EXIT_MAX_TRIES
            conn.execute("UPDATE okx_signal_events SET tries=?, note=?, status=? WHERE id=?",
                         (tries, note, "failed" if give_up else "pending", e["id"]))
            conn.commit()
            done.append({"id": e["id"], "status": "failed" if give_up else "retry", "note": note})
            print(f"okx signal: NOT sent {e['action']} {e['inst']} ({e['desk']}) try {tries}: {note}")
    return done


# ---------- 3. the public log ----------
def public(conn: sqlite3.Connection, cfg: dict, now: int, swap_src: str | None = None, desk_keys: tuple | None = None) -> dict:
    keys = ("id", "desk", "symbol", "inst", "kind", "action", "inv_type", "amount", "created", "sent", "status", "note")
    rows = [dict(zip(keys, r)) for r in conn.execute(
        f"SELECT {', '.join(keys)} FROM okx_signal_events ORDER BY COALESCE(sent, created) DESC, rowid DESC LIMIT 100").fetchall()]
    cnt = {s: n for s, n in conn.execute("SELECT status, COUNT(*) FROM okx_signal_events GROUP BY status").fetchall()}
    items = []
    for r in rows:
        unit = "% бота" if r["inv_type"] == "percentage_investment" else "% позиции" if r["inv_type"] else ""
        items.append({"t": r["sent"] or r["created"], "coin": (r["symbol"] or (r["inst"] or "").split("-")[0]), "inst": r["inst"],
                      "action": r["action"], "action_ru": ACTION_RU.get(r["action"], r["action"]) + (" (часть)" if r["kind"] == "part" else ""),
                      "desk": r["desk"], "desk_ru": DESK_RU.get(r["desk"], r["desk"]), "size": f"{_amt(r['amount'])}{unit}" if r["amount"] else None,
                      "status": r["status"], "note": _clean(r["note"], cfg) if r["note"] else None})
    last = next((x for x in items if x["status"] == "sent"), None)
    on_since = _meta(conn, "on_since")
    return {"asof": now, "on": bool(cfg["on"]), "why_off": None if cfg["on"] else cfg["why"],
            "configured": {"url": cfg["has_url"], "token": cfg["has_token"]}, "on_since": int(on_since) if on_since else None,
            "desks": list(desk_keys or DESKS), "instruments": swap_src, "max_lag_s": MAX_LAG_S, "max_age_min": cfg["max_age_ms"] // 60_000,
            "sent": cnt.get("sent", 0), "pending": cnt.get("pending", 0), "failed": cnt.get("failed", 0), "skipped": cnt.get("skipped", 0),
            "last": last, "items": items,
            "about_ru": ("Сигнал CRYPTO NOVA на OKX Signal Marketplace: сделки столов «отобранные сигналы» (откуп падений + Импульс BTC) "
                         "уходят в OKX как сообщения сигнала (вход, частичный выход, выход). Подписчик торгует своим ботом на своём счёте; "
                         "плечо и маржу он ставит сам. Прошлые результаты — не обещание. Сигнал бесплатный.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, env=None, data_dir: str = "data", sender=post,
        sleep=time.sleep, fetch_swaps=_fetch_swaps) -> dict:
    """one engine step: off → only the status file; on → detect, send, status. Never raises."""
    now = int(now or time.time() * 1000)
    cfg = config(env)
    swap_src = None
    err = None
    keys = ph.nova_base(ph.load_sel(out))              # 28.10: the header's desks (the main desk from web/desk_select.json + imp)
    try:
        ensure(conn)
        if not cfg["on"]:
            if _meta(conn, "state") == "on":
                _set_meta(conn, "state", "off")
                _set_meta(conn, "off_since", now)
                conn.commit()
            done = []
        else:
            if _meta(conn, "state") != "on" or not _meta(conn, "on_since"):
                _set_meta(conn, "state", "on")
                _set_meta(conn, "on_since", now)
                conn.commit()
            on_since = int(_meta(conn, "on_since"))
            try:
                with open(os.path.join(out, "paper.json"), encoding="utf-8") as f:
                    profiles = json.load(f).get("profiles") or {}
            except Exception as e:  # noqa: BLE001
                profiles, err = {}, f"paper.json: {type(e).__name__}"
            ids, swap_src = swaps(data_dir, now, fetch_swaps)
            if profiles:
                detect(conn, profiles, now, on_since, cfg, ids, swap_src, keys)
            done = flush(conn, cfg, now, sender, sleep)
        doc = public(conn, cfg, now, swap_src, keys)
        doc["this_run"] = done
    except Exception as e:  # noqa: BLE001
        doc = {"asof": now, "on": bool(cfg["on"]), "why_off": cfg["why"], "error": _clean(f"{type(e).__name__}: {e}", cfg)}
    if err:
        doc["error"] = err
    try:
        with open(os.path.join(out, "okx_signal.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    except OSError as e:
        doc["error"] = f"write: {e}"
    return doc
