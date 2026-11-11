"""«Питер» through the cloud (11.11.2026, owner chose option B): Peter is on a Mac and trades from the phone, so instead of the EA
his MT5 account is READ with the INVESTOR (read-only) password through MetaApi (metaapi.cloud REST). Observation only: this
module calls no trading endpoint — only account provisioning, account information, positions, history deals and candles.

The same store, desk and context snapshots as the EA path (cnq/api/peter.py): deals are deduplicated by id, history is backfilled
365 days, the context of every entry / exit is built from the candles fetched here. The EA path keeps working.

Secrets: the MetaApi token and the account id live only in <run>/data/peter/cloud.json (mode 600). The investor password is sent
to MetaApi once to provision the account and is NOT kept after that (it stays in the file only while MetaApi answers «still
processing»). Nothing here is logged; no endpoint returns a secret — only «задано / не задано» and a masked login.

MetaApi hosts (docs: metaapi.cloud/docs/provisioning, /docs/client):
  provisioning  https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts[/{id}]
  client        https://mt-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/{id}/account-information | positions |
                history-deals/time/{start}/{end}
  market data   https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai/…/historical-market-data/symbols/{s}/timeframes/{tf}/candles"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import peter as P

PROV = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts"
CLIENT = "https://mt-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/{id}"
MARKET = "https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/{id}/historical-market-data/symbols/{sym}/timeframes/{tf}/candles"
BACKFILL_DAYS = 365
POLL_S = 60
TF = {"M5": ("5m", 300_000), "H1": ("1h", 3600_000), "D1": ("1d", 86400_000)}
NOTE_RU = "Только пароль ИНВЕСТОРА (read-only). Основной (торговый) пароль сюда вводить нельзя."


def _http(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 30):
    """→ (status, json | None, response headers). Errors never carry the request (the token is in its headers)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **headers,
                                                                         **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8")) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            doc = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            doc = None
        return e.code, doc, dict(e.headers or {})


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ms(iso) -> int | None:
    try:
        return int(dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def mask(login) -> str:
    s = str(login or "")
    return ("•" * max(0, len(s) - 3) + s[-3:]) if s else ""


class Cloud:
    def __init__(self, peter: P.Peter, http=_http):
        self.p = peter
        self.http = http
        self.path = os.path.join(peter.dir, "cloud.json")
        self.lock = threading.Lock()
        self.last: dict = {}                    # the last poll: {t, ok, state, conn, err} — no secrets

    # ------------------------------------------------------------------ the secret file
    def _cfg(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, cfg: dict) -> None:
        fd = os.open(self.path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.chmod(self.path + ".tmp", 0o600)
        os.replace(self.path + ".tmp", self.path)

    def _wipe(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass

    def view(self) -> dict:
        """what the owner's card may see: flags, a masked login, the server name, the last poll — never a secret"""
        c = self._cfg()
        return {"set": bool(c.get("token")), "token": "задано" if c.get("token") else "не задано",
                "password": "передан в MetaApi, у нас не хранится" if c.get("account_id") else ("задано" if c.get("password") else "не задано"),
                "login": mask(c.get("login")), "server": c.get("server") or "", "provisioned": bool(c.get("account_id")),
                "region": c.get("region"), "last": self.last or None, "note_ru": NOTE_RU, "refused": c.get("refused")}

    # ------------------------------------------------------------------ MetaApi calls (read / provisioning only)
    def _h(self, c: dict, **extra) -> dict:
        return {"auth-token": c["token"], **extra}

    def _provision(self, c: dict) -> tuple[bool, str]:
        """create the MetaApi account (once); 202 = still processing → the same transaction id is retried on the next poll"""
        c.setdefault("txn", secrets.token_hex(16))
        code, doc, _ = self.http("POST", PROV, self._h(c, **{"transaction-id": c["txn"]}),
                                 {"name": "cnq-peter", "login": str(c["login"]), "password": c.get("password") or "", "server": c["server"],
                                  "platform": "mt5", "magic": 0, "type": "cloud-g2", "reliability": "regular"})
        if code == 201 and isinstance(doc, dict) and doc.get("id"):
            c["account_id"] = doc["id"]
            c.pop("password", None); c.pop("txn", None)         # MetaApi has it now — we keep no copy
            self._save(c)
            return True, "создан"
        if code == 202:
            self._save(c)
            return False, "MetaApi ещё подключает счёт — проверьте через минуту"
        if code in (401, 403):
            return False, "MetaApi не принял токен"
        msg = (doc or {}).get("message") if isinstance(doc, dict) else None
        return False, f"MetaApi отказал ({code})" + (f": {str(msg)[:160]}" if msg else "")

    def connect(self, form: dict) -> tuple[int, dict]:
        """POST /v2/owner/peter/cloud {token, login, server, password}"""
        tok, login, server, pw = (str(form.get(k) or "").strip() for k in ("token", "login", "server", "password"))
        with self.lock:
            old = self._cfg()
            tok = tok or str(old.get("token") or "")          # asked once: later the form is login + server + investor password
            if len(tok) < 20:
                return 400, {"error": "need_token", "message_ru": "Нужен токен MetaApi (один раз): app.metaapi.cloud → API access."}
            if not login.isdigit() or not 2 <= len(server) <= 80 or len(pw) < 4:
                return 400, {"error": "bad_form", "message_ru": "Нужны: номер счёта (только цифры), точное имя сервера и пароль инвестора."}
            if old.get("account_id"):
                return 409, {"error": "already_connected", "message_ru": "Счёт уже подключён. Сначала «Отключить»."}
            c = {"token": tok, "login": login, "server": server, "password": pw, "since": int(time.time() * 1000)}
            self._save(c)
            ok, msg = self._provision(c)
        return 200, {"ok": ok, "message_ru": msg, **self.view()}

    def disconnect(self) -> tuple[int, dict]:
        """POST /v2/owner/peter/cloud/disconnect — removes the account at MetaApi and every stored secret"""
        with self.lock:
            c = self._cfg()
            gone = None
            if c.get("account_id") and c.get("token"):
                try:
                    code, _, _ = self.http("DELETE", f"{PROV}/{c['account_id']}", self._h(c))
                    gone = code in (204, 404)
                except Exception:  # noqa: BLE001
                    gone = False
            self._wipe()
            self.last = {}
            with self.p.lock:
                self.p._meta("cloud", {"set": False, "last_ok": (self.p._meta("cloud") or {}).get("last_ok")})
                self.p.db.commit()
        return 200, {"ok": True, "metaapi_deleted": gone, **self.view(),
                     "message_ru": "Отключено, секреты удалены." + ("" if gone is not False else " Счёт в MetaApi удалить не удалось — удалите его в кабинете MetaApi.")}

    def check(self, now: int | None = None) -> tuple[int, dict]:
        """POST /v2/owner/peter/cloud/check — one poll right now"""
        st = self.poll(now)
        return 200, {"ok": bool(st.get("ok")), "message_ru": st.get("msg") or ("подключено" if st.get("ok") else "нет данных"), **self.view()}

    # ------------------------------------------------------------------ polling
    def poll(self, now: int | None = None) -> dict:
        now = int(now or time.time() * 1000)
        with self.lock:
            try:
                st = self._poll(now)
            except Exception as e:  # noqa: BLE001 — only the error's type: a message could quote a URL
                st = {"ok": False, "msg": f"ошибка связи с MetaApi ({type(e).__name__})"}
            self.last = {"t": now, **{k: st.get(k) for k in ("ok", "msg", "state", "conn", "deals", "new")}}
            with self.p.lock:
                self.p._meta("cloud", {"set": bool(self._cfg().get("token")), "last_try": now, "ok": bool(st.get("ok")),
                                       "last_ok": now if st.get("ok") else (self.p._meta("cloud") or {}).get("last_ok")})
                self.p.db.commit()
        return st

    def _poll(self, now: int) -> dict:
        c = self._cfg()
        if not c.get("token"):
            return {"ok": False, "msg": "не подключено"}
        if not c.get("account_id") and c.get("login") and c.get("password"):
            ok, msg = self._provision(c)
            if not ok:
                return {"ok": False, "msg": msg}
        if not c.get("account_id"):
            return {"ok": False, "msg": "введите счёт: логин, сервер, пароль инвестора"}
        aid = c["account_id"]
        code, acc, _ = self.http("GET", f"{PROV}/{aid}", self._h(c))
        if code != 200 or not isinstance(acc, dict):
            return {"ok": False, "msg": "MetaApi не принял токен" if code in (401, 403) else f"счёт в MetaApi не найден ({code})"}
        state, conn = acc.get("state"), acc.get("connectionStatus")
        if acc.get("region") and acc["region"] != c.get("region"):
            c["region"] = acc["region"]
            self._save(c)
        if state != "DEPLOYED" or conn != "CONNECTED":
            return {"ok": False, "state": state, "conn": conn,
                    "msg": "MetaApi не может войти на счёт — проверьте номер, имя сервера и пароль инвестора" if conn == "DISCONNECTED_FROM_BROKER"
                    else f"MetaApi подключается к брокеру ({state or '—'} / {conn or '—'})"}
        base = CLIENT.format(region=c.get("region") or "new-york", id=aid)
        code, info, _ = self.http("GET", base + "/account-information", self._h(c))
        if code != 200 or not isinstance(info, dict):
            return {"ok": False, "state": state, "conn": conn, "msg": f"нет данных счёта ({code})"}
        if info.get("tradeAllowed") is True or info.get("investorMode") is False:
            # a master password lets a terminal trade; an investor login never does → refuse it and keep nothing
            try:
                self.http("DELETE", f"{PROV}/{aid}", self._h(c))
            except Exception:  # noqa: BLE001
                pass
            self._wipe()
            self._save({"refused": "master_password", "token": c["token"]})
            return {"ok": False, "msg": "Отказано: это ОСНОВНОЙ пароль (торговля разрешена). Счёт отключён, пароль не сохранён. Нужен пароль инвестора."}
        code, pos, _ = self.http("GET", base + "/positions", self._h(c))
        pos = pos if code == 200 and isinstance(pos, list) else []
        # deals: the first time 365 days in 30-day windows, then from the last stored deal (1 h overlap)
        with self.p.lock:
            last = self.p.db.execute("SELECT MAX(t) FROM deals").fetchone()[0]
            done = bool(self.p._meta("cloud_backfill"))
        start = (last - 3600_000) if (last and done) else now - BACKFILL_DAYS * 86400_000
        deals: list[dict] = []
        t0 = start
        while t0 < now:
            t1 = min(now + 60_000, t0 + 30 * 86400_000)
            off = 0
            while True:
                code, page, _ = self.http("GET", f"{base}/history-deals/time/{_iso(t0)}/{_iso(t1)}?offset={off}&limit=1000", self._h(c))
                if code != 200 or not isinstance(page, list):
                    return {"ok": False, "state": state, "conn": conn, "msg": f"история сделок не загрузилась ({code})"}
                deals += page
                if len(page) < 1000:
                    break
                off += 1000
            t0 = t1
        deals = sorted({str(d.get("id")): d for d in deals if isinstance(d, dict) and d.get("id")}.values(), key=lambda d: (_ms(d.get("time")) or 0))
        new = self._feed(c, info, pos, deals, now, first=not done)
        return {"ok": True, "state": state, "conn": conn, "deals": len(deals), "new": new, "msg": "подключено, данные идут"}

    def _candles(self, c: dict, sym: str, tf: str, limit: int, before: int | None = None) -> list:
        url = MARKET.format(region=c.get("region") or "new-york", id=c["account_id"], sym=urllib.parse.quote(sym, safe=""), tf=TF[tf][0])
        url += f"?limit={limit}" + (f"&startTime={_iso(before)}" if before else "")
        code, rows, _ = self.http("GET", url, self._h(c), None, 120)
        if code != 200 or not isinstance(rows, list):
            return []
        out = [[_ms(r.get("time")), r.get("open"), r.get("high"), r.get("low"), r.get("close")] for r in rows if isinstance(r, dict)]
        return sorted([b for b in out if b[0]], key=lambda b: b[0])

    def _feed(self, c: dict, info: dict, pos: list, deals: list, now: int, first: bool) -> int:
        trade = [d for d in deals if d.get("type") in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL") and d.get("symbol")]
        with self.p.lock:
            known = {r[0] for r in self.p.db.execute("SELECT deal FROM deals")}
        fresh = [d for d in trade if P._i(d.get("id")) not in known]
        syms = sorted({d["symbol"] for d in fresh} | {p.get("symbol") for p in pos if isinstance(p, dict) and p.get("symbol")})
        bars: dict = {}
        for s in syms[:8]:                      # candles first, so every new deal gets its context from stored bars
            if first:
                h1, before = [], None
                for _ in range(8):              # ≈ a year of H1 bars, 1000 a call
                    part = self._candles(c, s, "H1", 1000, before)
                    if not part:
                        break
                    h1 = part + h1
                    before = part[0][0]
                    if before < now - BACKFILL_DAYS * 86400_000 - 210 * 3600_000:
                        break
                bars[s] = {"D1": self._candles(c, s, "D1", 600), "H1": h1, "M5": self._candles(c, s, "M5", 1000)}
            else:
                bars[s] = {"D1": self._candles(c, s, "D1", 5), "H1": self._candles(c, s, "H1", 30), "M5": self._candles(c, s, "M5", 60)}
        # the balance right after each deal: walk back from today's balance through every deal of the window (deposits too)
        run = P._f(info.get("balance"), 0.0)
        bal_after = {}
        for d in reversed(deals):
            bal_after[str(d.get("id"))] = run
            run -= sum(P._f(d.get(k), 0.0) for k in ("profit", "swap", "commission", "fee"))
        ent = {"DEAL_ENTRY_IN": "in", "DEAL_ENTRY_OUT": "out", "DEAL_ENTRY_INOUT": "inout", "DEAL_ENTRY_OUT_BY": "out_by"}
        with self.p.lock:
            self.p._candles(bars)
            for d in fresh:
                t = _ms(d.get("time"))
                e = {"type": "history" if first else "deal", "deal": d.get("id"), "order": d.get("orderId"), "pos": d.get("positionId"),
                     "symbol": d["symbol"], "dtype": "buy" if d["type"] == "DEAL_TYPE_BUY" else "sell", "entry": ent.get(d.get("entryType"), "other"),
                     "volume": d.get("volume"), "price": d.get("price"), "sl": d.get("stopLoss"), "tp": d.get("takeProfit"),
                     "profit": d.get("profit"), "swap": d.get("swap"), "commission": d.get("commission"), "fee": d.get("fee"),
                     "t": t, "bal_after": bal_after.get(str(d.get("id"))), "src": "cloud"}
                if t and e["entry"] != "other":
                    e["ctx"] = self.p.ctx_from_store(d["symbol"], t)
                self.p._event(e, "cloud", now)
            self.p._event({"type": "heartbeat", "t": now, "balance": info.get("balance"), "equity": info.get("equity"),
                           "currency": info.get("currency"), "leverage": info.get("leverage"), "terminal": "MetaApi cloud",
                           "positions": [{"pos": p.get("id"), "symbol": p.get("symbol"), "side": "buy" if p.get("type") == "POSITION_TYPE_BUY" else "sell",
                                          "volume": p.get("volume"), "open_price": p.get("openPrice"), "price": p.get("currentPrice"),
                                          "sl": p.get("stopLoss"), "tp": p.get("takeProfit"), "profit": p.get("profit"), "swap": p.get("swap"),
                                          "open_t": _ms(p.get("time"))} for p in pos if isinstance(p, dict)]}, "cloud", now)
            self.p._meta("cloud_backfill", True)
            self.p._meta("source", "cloud")
            self.p.db.commit()
            live = self.p._build(now)
        self.p._write(live)
        return len(fresh)

    def start(self) -> None:
        """the API's background reader: one poll a minute while the cloud is configured"""
        def loop():
            while True:
                try:
                    if self._cfg().get("token"):
                        self.poll()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(POLL_S)
        threading.Thread(target=loop, daemon=True, name="peter-cloud").start()
