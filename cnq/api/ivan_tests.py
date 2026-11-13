"""«Иван · тест-стратегии» (13.11.2026, owner 22:10): Ivan tests 3 versions of his strategy on 3 Bybit DEMO-trading accounts; we
WATCH them and show what a paper copy would give. Observation / paper only — nothing here can place an order, and nothing is ever
routed to the real account (no «ivan:» signal id is made here).

Runs on the server inside the API, every 90 s, for N named accounts. Each account: a READ-ONLY Bybit API key (checked with
GET /v5/user/query-api: readOnly must be 1 and no Withdraw / transfer right — otherwise refused and not stored), on the demo host
(https://api-demo.bybit.com, a key made in Demo Trading mode) or mainnet. The reading itself is cnq/execution/ivan_watch.collect
(wallet, positions, closed P&L, spot fills) — the same code as «Иван тест».

Secrets: <run>/data/ivan_tests/accounts.json, mode 600; never returned by any endpoint (only the name, the host, a masked key).
Owner endpoints (cnq/api/server.py): GET /v2/owner/ivan_tests · POST /v2/owner/ivan_tests/add {name, key, secret, demo} ·
POST /v2/owner/ivan_tests/check {id} · POST /v2/owner/ivan_tests/remove {id}."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time

from cnq.execution import ivan_watch as iw

EVERY_S = 90
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
    if str(r.get("readOnly")) != "1":
        return False, "Ключ с правом торговли (Read-Write). Нужен ключ «Только чтение» (Read-Only) — ключ не сохранён."
    if wallet & BAD_WALLET:
        return False, "У ключа есть право вывода или переводов — нужен ключ только для чтения. Ключ не сохранён."
    return True, "ключ только для чтения"


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
    def __init__(self, data_dir: str, make_fetch=iw.make_fetch):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.make_fetch = make_fetch
        self.lock = threading.Lock()
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
                doc = {"id": a["id"], "name": a["name"], "host": a["host"], "asof": t, "account": out, "paper": pp, "summary": summary(out, pp)}
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
            rows.append({"id": a["id"], "name": a["name"], "host": a["host"], "key": mask(a["key"]), "added": a.get("added"),
                         "last": self.last.get(a["id"]), "data": doc})
        return {"asof": int(time.time() * 1000), "accounts": rows, "paper_rule_ru": RULE_RU, "test": True, "owner_only": True,
                "label_ru": "ТЕСТ · только наблюдение за демо-счетами Ивана и бумажная копия. На реальный счёт ничего не попадает."}

    def start(self) -> None:
        def loop():
            while True:
                try:
                    if self._accounts():
                        self.poll()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="ivan-tests").start()
