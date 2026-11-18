"""«Иван · тест-стратегии» (13.11.2026, owner 22:10): Ivan tests versions of his strategy on Bybit DEMO-trading accounts; we WATCH
them and show what a paper copy would give. Nothing here can place an order.

Runs on the server inside the API for N named accounts. Each account: a READ-ONLY Bybit API key (checked with
GET /v5/user/query-api: readOnly must be 1 and no Withdraw / transfer right — otherwise refused and not stored; 18.11 owner: on
the DEMO host a Read-Write key is accepted too, still without withdraw / transfer, because Ivan's platform trades with it), on the demo host
(https://api-demo.bybit.com, a key made in Demo Trading mode) or mainnet. The reading itself is cnq/execution/ivan_watch.collect
(wallet, positions, closed P&L, spot fills) — the same code as «Иван тест» — every 90 s; the positions alone every 30 s.

17.11 (owner 17:27, real money): «Иван Тест 2 / 3 / 4» may be COPIED to the real OKX futures account like «ivan:». Per account an
owner-only switch «копировать на реальный счёт» (copy_real, DEFAULT OFF — POST /v2/owner/ivan_tests/copy_real {id, on}). Turning
it on re-checks that the key is still read-only and gives the account a slot 2 / 3 / 4 (from the number in its name when free).
This module still places nothing: the real autopilot (cnq/execution/whale_live.py, the same server user) reads accounts.json and
follows the switched-on accounts as «ivan<slot>:bybit» inside the existing copy rules and limits (okx_futures: gate «ivan2:» /
«ivan3:» / «ivan4:», all Ivan accounts = one trader). Off → no new copies; held ones run to his exit / our stop.

Study (cnq/agents/ivan_study.py): every entry / add / reduce / exit of every connected account is stored RAW with its market
context in <run>/data/ivan_tests/ctx_<id>.jsonl (public Bybit candles, no key), and each account's card gets the numbers a win rate
hides: open (unrealised) result, worst open loss, max drawdown, averaging count, and the result of OUR real copies.

Secrets: <run>/data/ivan_tests/accounts.json, mode 600; never returned by any endpoint (only the name, the host, a masked key).
Owner endpoints (cnq/api/server.py): GET /v2/owner/ivan_tests · POST /v2/owner/ivan_tests/add {name, key, secret, demo} ·
POST /v2/owner/ivan_tests/check {id} · POST /v2/owner/ivan_tests/remove {id} · POST /v2/owner/ivan_tests/copy_real {id, on}."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.request

from cnq.agents import ivan_study as IS
from cnq.execution import ivan_watch as iw

EVERY_S = 90
FAST_S = 30                 # 17.11: positions alone (one GET per account) — an entry / add / exit is seen within ~30 s
SLOTS = (2, 3, 4)           # «ivan2:» / «ivan3:» / «ivan4:» — the real-copy ids (cnq/execution/whale_live.py reads slot + copy_real)
KLINE_TTL_S = 300
COPY_RULE_RU = ("Копия на реальный счёт OKX (фьючерсы) идёт по тем же правилам и в тех же лимитах, что копия «Ивана»: тот же риск на "
                "сделку, защитный стоп, правило плеча, дневной стоп, не больше 15 копий, одна копия на монету, без усреднений. Все счета "
                "Ивана вместе — один трейдер: не больше 50% бюджета фьючерсов. Выключили — новых копий нет; открытые копии закрываются "
                "вместе с его позицией или по нашему стопу.")
MAX_ACCOUNTS = 6
PAPER_START, PAPER_MARGIN, PAPER_LEV_CAP, PAPER_COST = 10_000.0, 0.10, 5.0, 0.001
RULE_RU = (f"Наша копия на бумаге: на каждую закрытую сделку счёта — маржа {PAPER_MARGIN * 100:g}% бумажного счёта $10 000, плечо как у "
           f"Ивана, но не больше {PAPER_LEV_CAP:g}× (если плечо не видно — 1×), вход и выход по его ценам, издержки "
           f"{PAPER_COST * 100:g}% за сделку, убыток не больше маржи. На реальный счёт эти сделки не попадают.")
BAD_WALLET = {"Withdraw", "AccountTransfer", "SubMemberTransfer", "SubMemberTransferList"}


def mask(k: str) -> str:
    k = str(k or "")
    return (k[:4] + "…" + k[-3:]) if len(k) > 8 else "…"


def check_key(fetch, base: str) -> tuple[bool, str]:
    """(ok, message): the key must be read-only (readOnly = 1) and carry no withdraw / transfer right"""
    try:
        r = iw._call(fetch, base, "/v5/user/query-api", {})
    except iw.KeyRejected:
        return False, "Bybit не принял ключ (неверный ключ / секрет или не тот режим: ключ демо-счёта работает только с «Демо»)"
    except Exception as e:  # noqa: BLE001
        return False, f"не удалось проверить ключ ({type(e).__name__})"
    perms = r.get("permissions") or {}
    wallet = set(perms.get("Wallet") or [])
    if wallet & BAD_WALLET:
        return False, "У ключа есть право вывода или переводов — нужен ключ только для чтения. Ключ не сохранён."
    if str(r.get("readOnly")) != "1":
        # 18.11 (owner): Ivan's platform trades these DEMO accounts, so their keys are Read-Write. On the demo host only (no real
        # money behind the key, and still no withdraw / transfer right) such a key is accepted — this module and whale_live only
        # ever send GET requests with it. A mainnet key must stay read-only.
        if base == iw.HOSTS["demo"]:
            return True, "ключ демо-счёта с правом торговли — CRYPTO NOVA его только читает"
        return False, "Ключ с правом торговли (Read-Write). Нужен ключ «Только чтение» (Read-Only) — ключ не сохранён."
    return True, "ключ только для чтения"


def public_kline(symbol: str, interval: str, limit: int, timeout: int = 10) -> list:
    """PUBLIC Bybit candles (mainnet market data, no key is sent): [[t, o, h, l, c], …] oldest first"""
    url = f"{iw.HOSTS['main']}/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={int(limit)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-ivan-study"}), timeout=timeout) as r:
        d = json.loads(r.read().decode())
    rows = ((d.get("result") or {}).get("list") or []) if isinstance(d, dict) else []
    return sorted([[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])] for x in rows], key=lambda x: x[0])


def slot_for(name: str, taken: set) -> int | None:
    """the account's real-copy slot: the number in its name («Иван Тест 3» → 3) when it is 2 / 3 / 4 and free, else the first free"""
    m = re.findall(r"\d+", str(name or ""))
    want = int(m[-1]) if m else None
    if want in SLOTS and want not in taken:
        return want
    return next((x for x in SLOTS if x not in taken), None)


def real_copies(web_dir: str | None, key: str) -> dict | None:
    """OUR real copies of one Ivan account (desk key «ivan2» …) from the real account's published numbers: open copies and their
    unrealised result (public_real.json futures by «sig»), closed copies and their result (real_why.json closed by «sig» joined to
    public_real.json closed by instrument + close time). None when the files are not there."""
    if not web_dir or not key:
        return None
    def load(name):
        try:
            with open(os.path.join(web_dir, name), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return None
    pub, why = load("public_real.json"), load("real_why.json")
    if pub is None:
        return None
    pre = key + ":"
    opn = [x for x in pub.get("futures") or [] if isinstance(x, dict) and str(x.get("sig") or "").startswith(pre)]
    mine = {(x.get("inst"), x.get("closed")) for x in (why or {}).get("closed") or [] if isinstance(x, dict) and str(x.get("sig") or "").startswith(pre)}
    cl = [x for x in pub.get("closed") or [] if isinstance(x, dict) and (x.get("inst"), x.get("closed")) in mine]
    pn = [float(x.get("pnl_usd") or 0) for x in cl]
    return {"open_n": len(opn), "open_pnl_usd": round(sum(float(x.get("upnl_usd") or 0) for x in opn), 2),
            "open": [{"inst": x.get("inst"), "side": x.get("side"), "upnl_usd": x.get("upnl_usd"), "opened": x.get("opened")} for x in opn],
            "closed_n": len(cl), "closed_pnl_usd": round(sum(pn), 2), "wins": sum(1 for v in pn if v > 0),
            "total_pnl_usd": round(sum(pn) + sum(float(x.get("upnl_usd") or 0) for x in opn), 2), "asof": pub.get("asof")}


def paper(closed: list[dict], lev_of: dict) -> dict:
    """«наша копия на бумаге» of one account's closed trades (oldest first)"""
    eq, peak, dd, rows, curve = PAPER_START, PAPER_START, 0.0, [], []
    for t in closed:
        if not t.get("entry") or not t.get("exit"):
            continue
        s = 1 if t["side"] == "LONG" else -1
        lev = min(PAPER_LEV_CAP, max(1.0, float(lev_of.get(f"{t['symbol']}|{t['side']}") or 1.0)))
        margin = PAPER_MARGIN * eq
        move = s * (t["exit"] / t["entry"] - 1)
        pnl = max(margin * lev * (move - 2 * PAPER_COST), -margin)
        eq += pnl
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak if peak else 0.0)
        rows.append({"symbol": t["symbol"], "side": t["side"], "close_time": t["close_time"], "lev": lev, "pnl": round(pnl, 2),
                     "pct": round(pnl / (eq - pnl) * 100, 3) if eq - pnl else None})
        curve.append([t["close_time"], round(eq, 2)])
    wins = sum(1 for r in rows if r["pnl"] > 0)
    return {"start": PAPER_START, "equity": round(eq, 2), "pnl_usd": round(eq - PAPER_START, 2), "pnl_pct": round((eq / PAPER_START - 1) * 100, 2),
            "trades": len(rows), "wins": wins, "winrate": round(wins / len(rows), 4) if rows else None, "max_dd_pct": round(dd * 100, 2),
            "curve": curve[-400:], "last": list(reversed(rows[-30:])), "rule_ru": RULE_RU}


def summary(out: dict, pp: dict) -> dict:
    """the side-by-side numbers of one account: its own % (balance), win rate, average trade, max DD; the paper copy"""
    h = out.get("equity_hist") or []
    st = out.get("stats") or {}
    base = h[0][1] if h and h[0][1] else None
    n = st.get("n") or 0
    return {"equity": out.get("equity"), "pnl_pct": round((out["equity"] / base - 1) * 100, 2) if base and out.get("equity") else None,
            "trades": n, "winrate": st.get("winrate"), "avg_trade_usd": round(st["pnl_sum"] / n, 2) if n else None,
            "avg_trade_pct": round(st["pnl_sum"] / n / base * 100, 3) if n and base else None,
            "max_dd_pct": round((out.get("max_dd") or 0) * 100, 2), "open_n": len(out.get("positions") or []),
            "paper_pnl_usd": pp["pnl_usd"], "paper_pnl_pct": pp["pnl_pct"], "paper_max_dd_pct": pp["max_dd_pct"]}


class IvanTests:
    def __init__(self, data_dir: str, make_fetch=iw.make_fetch, web_dir: str | None = None, kline=public_kline):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.make_fetch = make_fetch
        self.web_dir, self.kline = web_dir, kline
        self.lock = threading.Lock()
        self.study_lock = threading.Lock()
        self._kl: dict = {}
        self.path = os.path.join(self.dir, "accounts.json")
        self.last: dict[str, dict] = {}

    # ------------------------------------------------------------------ secrets (mode 600, never returned)
    def _accounts(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return [a for a in d if isinstance(a, dict) and a.get("id")] if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, accs: list[dict]) -> None:
        fd = os.open(self.path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(accs, f)
        os.chmod(self.path + ".tmp", 0o600)
        os.replace(self.path + ".tmp", self.path)

    def _state_path(self, aid: str) -> str:
        return os.path.join(self.dir, f"state_{aid}.json")

    def _load_state(self, aid: str) -> dict:
        try:
            with open(self._state_path(aid), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, aid: str, st: dict) -> None:
        p = self._state_path(aid)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(p + ".tmp", p)

    # ------------------------------------------------------------------ owner actions
    def add(self, form: dict) -> tuple[int, dict]:
        name = str(form.get("name") or "").strip()[:40]
        key, sec = str(form.get("key") or "").strip(), str(form.get("secret") or "").strip()
        host = "demo" if form.get("demo") in (True, "true", "1", 1, "on", "demo") else "main"
        if not name or not 10 <= len(key) <= 64 or not 10 <= len(sec) <= 128:
            return 400, {"error": "bad_form", "message_ru": "Нужны имя, API-ключ и секрет."}
        ok, msg = check_key(self.make_fetch(key, sec), iw.HOSTS[host])
        if not ok:
            return 200, {"ok": False, "message_ru": msg, **self.view()}
        with self.lock:
            accs = self._accounts()
            if len(accs) >= MAX_ACCOUNTS:
                return 409, {"error": "too_many", "message_ru": f"Не больше {MAX_ACCOUNTS} счетов."}
            if any(a["key"] == key for a in accs):
                return 409, {"error": "exists", "message_ru": "Этот ключ уже подключён."}
            accs.append({"id": secrets.token_hex(4), "name": name, "key": key, "secret": sec, "host": host, "added": int(time.time() * 1000)})
            self._save(accs)
        self.poll()
        return 200, {"ok": True, "message_ru": f"Подключено: {name} ({'демо' if host == 'demo' else 'реальный'} счёт Bybit), {msg}.", **self.view()}

    def remove(self, aid: str) -> tuple[int, dict]:
        with self.lock:
            accs = self._accounts()
            keep = [a for a in accs if a["id"] != aid]
            if len(keep) == len(accs):
                return 404, {"error": "not_found"}
            self._save(keep)
            for p in (self._state_path(aid), os.path.join(self.dir, f"out_{aid}.json")):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.last.pop(aid, None)
        return 200, {"ok": True, "message_ru": "Отключено, ключ удалён с сервера. Сам ключ на Bybit удалите в кабинете Bybit.", **self.view()}

    def check(self, aid: str) -> tuple[int, dict]:
        a = next((x for x in self._accounts() if x["id"] == aid), None)
        if not a:
            return 404, {"error": "not_found"}
        ok, msg = check_key(self.make_fetch(a["key"], a["secret"]), iw.HOSTS[a["host"]])
        if not ok:                                   # the key gained trade rights or was revoked → it is not kept
            self.remove(aid)
            return 200, {"ok": False, "message_ru": msg + " Счёт отключён.", **self.view()}
        self.poll(only=aid)
        return 200, {"ok": True, "message_ru": (self.last.get(aid) or {}).get("msg") or msg, **self.view()}

    def set_copy(self, aid: str, on) -> tuple[int, dict]:
        """17.11: the owner's switch «копировать на реальный счёт» of ONE account (default off). On: the key is re-checked (still
        read-only) and the account gets its slot 2 / 3 / 4 — the real autopilot then follows it as «ivan<slot>:bybit»."""
        on = on in (True, "true", "1", 1, "on")
        with self.lock:
            accs = self._accounts()
            a = next((x for x in accs if x["id"] == aid), None)
            if not a:
                return 404, {"error": "not_found"}
            if on:
                ok, msg = check_key(self.make_fetch(a["key"], a["secret"]), iw.HOSTS[a["host"]])
                if not ok:
                    return 200, {"ok": False, "message_ru": msg + " Копирование не включено.", **self.view()}
                if a.get("slot") not in SLOTS:
                    slot = slot_for(a["name"], {x.get("slot") for x in accs if x is not a and x.get("slot") in SLOTS})
                    if slot is None:
                        return 409, {"error": "no_slot", "message_ru": "На реальный счёт копируются не больше трёх тест-счетов (Тест 2, 3, 4)."}
                    a["slot"] = slot
                a["copy_real"], a["copy_t"] = True, int(time.time() * 1000)
            else:
                a["copy_real"], a["copy_off_t"] = False, int(time.time() * 1000)
            self._save(accs)
            slot = a.get("slot")
        msg = (f"Включено: «{a['name']}» копируется на реальный счёт OKX (фьючерсы) как «Иван · тест {slot}». Первая копия — с его "
               f"следующей новой позиции или с уже открытой, пока цена рядом с его входом." if on else
               f"Выключено: новых копий «{a['name']}» на реальном счёте не будет. Уже открытые копии закроются вместе с его позицией или по стопу.")
        return 200, {"ok": True, "message_ru": msg, **self.view()}

    # ------------------------------------------------------------------ study (17.11): raw context of every entry / add / exit
    def _candles(self, symbol: str, interval: str, limit: int) -> list:
        k, now = (symbol, interval), time.time()
        hit = self._kl.get(k)
        if hit and now - hit[0] < KLINE_TTL_S:
            return hit[1]
        try:
            cs = self.kline(symbol, interval, limit)
        except Exception:  # noqa: BLE001 — no candles → the event is stored without its context
            cs = []
        if cs:
            self._kl[k] = (now, cs)
        return cs

    def _study(self, a: dict, positions: list, t: int, out: dict | None = None) -> dict | None:
        """one look at an account's positions → new events (with context) appended to ctx_<id>.jsonl; with the full reading
        (out) also the card's numbers. The first look is a baseline: positions already open are marked so."""
        with self.study_lock:
            p = os.path.join(self.dir, f"study_{a['id']}.json")
            try:
                with open(p, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
            first = "open" not in st
            ev = IS.diff(st.setdefault("open", {}), positions, t)
            rows = []
            for e in ev:
                if first:
                    e["baseline"] = True
                sym = e["symbol"] + "USDT"
                h1, d1 = self._candles(sym, "60", 400), self._candles(sym, "D", 260)
                px = e.get("fill_px") or (e.get("avg_entry") if e["kind"] == "entry" else None) or e.get("px") or (h1[-1][4] if h1 else None)
                if e["kind"] == "exit":
                    px = h1[-1][4] if h1 else None
                    e["px"] = px
                ctx = IS.context(px, t, h1, d1, self._candles("BTCUSDT", "60", 400), self._candles("BTCUSDT", "D", 260)) if px else None
                rows.append({**e, "account": a["id"], "name": a["name"], "slot": a.get("slot"), "ctx": ctx})
            if rows:
                with open(os.path.join(self.dir, f"ctx_{a['id']}.jsonl"), "a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
                IS.count(st, [r for r in rows if not r.get("baseline")])
            card = IS.track(st, out, t) if out is not None else None
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(p + ".tmp", p)
            if card is not None:
                card["last_events"] = list(reversed((st.get("events") or [])[-20:]))
                card["ladders"] = [{"key": k, **{x: L.get(x) for x in ("t0", "steps", "adds", "avg_downs", "size", "avg", "first_px", "last_px",
                                                                         "last_t", "worst_upnl")}} for k, L in (st.get("open") or {}).items()]
            return card

    def watch(self, now: int | None = None) -> None:
        """17.11: the 30-second look — positions only (one GET per account), for the study's event times"""
        for a in self._accounts():
            t = int(now or time.time() * 1000)
            try:
                rows = iw.read_positions(self.make_fetch(a["key"], a["secret"]), iw.HOSTS[a["host"]])
                self._study(a, [iw.norm_position(x, {}) for x in rows], t)
            except Exception:  # noqa: BLE001 — the full reading reports errors
                pass

    # ------------------------------------------------------------------ reading
    def poll(self, now: int | None = None, only: str | None = None) -> None:
        for a in self._accounts():
            if only and a["id"] != only:
                continue
            t = int(now or time.time() * 1000)
            st = self._load_state(a["id"])
            st["host"] = a["host"]                   # the host the owner chose — never the other one
            try:
                raw = self.make_fetch(a["key"], a["secret"])
                base = iw.HOSTS[a["host"]]
                # the key is only ever sent to the host the owner chose (collect() would otherwise probe the other one)
                fetch = lambda b, path, params, raw=raw, base=base: raw(b, path, params) if b == base else {"retCode": 10003, "retMsg": "other host"}  # noqa: E731
                out = iw.collect(st, t, fetch)
                lev = st.setdefault("lev_seen", {})
                for p in out.get("positions") or []:
                    if p.get("leverage"):
                        lev[f"{p['symbol']}|{p['side']}"] = p["leverage"]
                self._save_state(a["id"], st)
                closed = sorted(st.get("closed") or [], key=lambda x: x["close_time"])
                pp = paper(closed, lev)
                try:
                    study = self._study(a, out.get("positions") or [], t, out)
                except Exception:  # noqa: BLE001 — the study never breaks the reading
                    study = None
                doc = {"id": a["id"], "name": a["name"], "host": a["host"], "asof": t, "account": out, "paper": pp, "summary": summary(out, pp),
                       "study": study}
                with open(os.path.join(self.dir, f"out_{a['id']}.json"), "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False)
                self.last[a["id"]] = {"t": t, "ok": True, "msg": "данные идут"}
            except iw.KeyRejected:
                self.last[a["id"]] = {"t": t, "ok": False, "msg": "Bybit не принимает ключ — проверьте его или режим (демо / реальный)"}
            except Exception as e:  # noqa: BLE001 — only the error's type: a message could carry a URL
                self.last[a["id"]] = {"t": t, "ok": False, "msg": f"ошибка чтения ({type(e).__name__})"}

    def view(self) -> dict:
        """the owner's tab: every account's data, flags only about secrets"""
        rows = []
        for a in self._accounts():
            try:
                with open(os.path.join(self.dir, f"out_{a['id']}.json"), encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                doc = None
            slot = a.get("slot") if a.get("slot") in SLOTS else None
            on = a.get("copy_real") is True and slot is not None
            rows.append({"id": a["id"], "name": a["name"], "host": a["host"], "key": mask(a["key"]), "added": a.get("added"),
                         "last": self.last.get(a["id"]), "data": doc,
                         # 17.11: the real-copy switch (default off) and what our real copies of this account gave
                         "copy_real": on, "slot": slot, "copy_id": f"ivan{slot}" if slot else None, "copy_since": a.get("copy_t") if on else None,
                         "real": real_copies(self.web_dir, f"ivan{slot}") if slot else None,
                         "study": (doc or {}).get("study")})
        any_on = any(r["copy_real"] for r in rows)
        return {"asof": int(time.time() * 1000), "accounts": rows, "paper_rule_ru": RULE_RU, "copy_rule_ru": COPY_RULE_RU,
                "test": True, "owner_only": True, "copy_real_any": any_on,
                "label_ru": ("ТЕСТ · наблюдение за счетами Ивана и бумажная копия. На реальный счёт копируются только счета с включённым "
                             "переключателем «копировать на реальный счёт»." if any_on else
                             "ТЕСТ · только наблюдение за демо-счетами Ивана и бумажная копия. На реальный счёт ничего не попадает, "
                             "пока вы сами не включите у счёта «копировать на реальный счёт».")}

    def start(self) -> None:
        def loop():
            n = 0
            while True:
                try:
                    if self._accounts():
                        if n % max(1, EVERY_S // FAST_S) == 0:
                            self.poll()                      # the full reading every 90 s
                        else:
                            self.watch()                     # 17.11: positions only every 30 s
                except Exception:  # noqa: BLE001
                    pass
                n += 1
                time.sleep(FAST_S)
        threading.Thread(target=loop, daemon=True, name="ivan-tests").start()
