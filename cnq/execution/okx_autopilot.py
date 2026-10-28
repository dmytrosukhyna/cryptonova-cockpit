"""«Автопилот OKX» — the owner's OWN OKX sub-account follows the CRYPTO NOVA system by itself (22.10.2026, owner: «чтобы уже через
нашу систему … я смог бы подключить на своём OKX эту систему … не вручную … завести там 10 тысяч долларов»; 09:56: «наша система» =
the closed system's header — FUTURES · CRYPTO NOVA and SPOT · CRYPTO NOVA портфель; no DeFi or yield sleeves on this account).

The account is split (okx_alloc.shares; env OKX_SPOT_SHARE / OKX_CORE_SHARE / OKX_FUT_SHARE / OKX_ACT_SHARE) — owner's decision of
28.10 (real money, answered explicitly; until then 65 / 25 / 10):
  spot 65 %     — mirrors «Наш портфель CRYPTO NOVA» (web/portfolio.json → portfolios.ideas): every coin at its weight there, its
                  cash rule (≥ 10 %), its stops / take-profits / sells — they arrive as the portfolio's own changes (this file);
  core +10 %    — BTC / ETH / SOL on top, in the core's plan proportions 30 / 10 / 5 (okx_alloc.core_extra, this file);
  futures 15 %  — the CRYPTO NOVA futures desks, swaps with a stop on every position (okx_futures.py); its budget = 15 % of the
                  starting capital + its own result, never below the open positions' margin;
  active 10 %   — the «Импульс BTC» spot desk; between its trades in BTC, USDT for 3 days after its exit (okx_active.py).
Runs on the owner's Mac; keys never leave it.

  python3 -m cnq.execution.okx_autopilot --check     keys, host, permissions, account mode, money, the futures readiness
  python3 -m cnq.execution.okx_autopilot --plan      EVERY order it would place now (spot, active, futures) with sizes, stops and
                                                     reasons, and whether the account matches the system; places nothing
  python3 -m cnq.execution.okx_autopilot --run       one pass now: spot rebalance (sells first), active spot, futures
  python3 -m cnq.execution.okx_autopilot --loop      the window: futures and active spot every minute, the spot portfolio checked
                                                     every 5 minutes (scripts/okx_autopilot_mac.sh loop)
  python3 -m cnq.execution.okx_autopilot --stop      kill switch: no more orders until --resume (futures keep their stops on OKX)
  python3 -m cnq.execution.okx_autopilot --resume    removes the kill switch
  python3 -m cnq.execution.okx_autopilot --fut-close closes every futures position the autopilot opened (reduce-only), now

After a --run with OKX_AUTOPILOT=on the autopilot is «armed» and the window keeps the account on the system: the spot part is
rebalanced when the public portfolio changes (a buy, a sell, a stop, a trim — its coins or its last action) and when the account
drifted away from it (checked hourly); small differences (under $10 or 0.3 % of the account) are left alone. It runs apart from the
Bybit futures mirror on purpose: CNQ_LIVE_TRADING=yes is given to THIS process only.

Safety:
  • spot orders are tdMode «cash» (no borrowing) — on a spot lead trader «spot_isolated» for the copied coins (okx_lead.py); futures are isolated-margin swaps with the caps of okx_futures.py (leverage ≤ 3,
    the desk's own risk a trade, ≤ 1 %, 5 positions, 3 % daily loss stop); never a withdrawal or a transfer — the client has no such call. A key
    with the «Withdraw» permission is refused outright (nothing is traded with it).
  • real money only with CNQ_LIVE_TRADING=yes, given on the autopilot's own command line (scripts/okx_autopilot_mac.sh --live asks
    to type «ДА»), never in ~/.cryptonova.env (the Bybit futures mirror reads that file). Otherwise OKX demo trading (header
    x-simulated-trading: 1, needs a demo key).
  • the host is found by itself: www.okx.com first (the owner's account is the global one: USDT pairs, spot fees 0.08 % maker /
    0.10 % taker), then eea.okx.com and my.okx.com as a fallback (an OKX Europe key fails on www with 60032); OKX_HOST forces one.
    The quote is USDT on the global account; only an OKX Europe account would trade in USDC / EUR.
  • every order is a limit IOC at most OKX_SLIP (0.4 %) worse than the best price — never a blind market order;
    one order ≤ OKX_MAX_ORDER_USD ($3 000; bigger ones are split), all orders in a day ≤ OKX_MAX_DAY_USD ($25 000).
  • kill switch: the file ~/.cryptonova_okx_stop (made by --stop) is checked before every order; --stop also writes the global
    stop into the lease, so an autopilot on the server stops too.
  • one autopilot per account (22.10, okx_lease.py): every order cycle (spot rebalance, futures, active spot) first asks the
    lease on the repository's branch okx-lease — the right machine (Mac / server), no other live window, no global stop; and
    this client refuses any order once its lease ran out mid-cycle. When in doubt — no orders.
  • coins outside the trading account (Funding, Simple Earn) are counted but never moved: the owner moves them.
  • OKX_KEEP=SOL,LINK — coins the autopilot never touches (not sold, not bought, not counted).
  • a coin with no {COIN}-USDT pair but a {COIN}-USDC one goes through USDC (22.10, owner: «если по USDT ничего нету, то надо через
    USDC конвертировать»; route()): buy = USDC bought on USDC-USDT first (the coin order + fee and slippage room), then COIN-USDC;
    sell = COIN-USDC, then the USDC sold back on USDC-USDT. Both legs are limit IOC within OKX_SLIP; the per-order and per-day caps
    count the coin leg. A leg that did not fill never leaves a half: free USDC (≥ $5) goes back to USDT at the end of every pass
    (sweep()) and the coin leg is retried on the next cycle. USDC is cash (≈ $1) in every share, budget and «СОВПАДАЕТ».
  • new code reaches the running window safely (Updater): at a cycle boundary, when the repository's code changed and the new code
    passes `--selfcheck` in a fresh process, the loop exits cleanly and systemd / the Mac window's bash loop starts it again; the
    lease is kept across that planned restart (the same OKX_LEASE_ID resumes at once). OKX_SELF_UPDATE=off turns it off.
Log: data/okx_autopilot.jsonl (local, ignored by git). State: ~/.cryptonova_okx_autopilot.json (outside the repo). The card shows
the state (on / off / stopped, last rebalance, the split, the futures positions) inside the encrypted web/myspot.enc.json.

Env (~/.cryptonova.env on the Mac, never in the repo or in a chat):
  OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE — an OKX API key with «Read» + «Trade», WITHOUT «Withdraw», bound to the Mac's IP;
  OKX_AUTOPILOT=on — the autopilot window may rebalance by itself (after the first --run).
  Optional: OKX_SPOT_SHARE / OKX_CORE_SHARE / OKX_FUT_SHARE / OKX_ACT_SHARE (0.65 / 0.10 / 0.15 / 0.10), OKX_FUTURES=off,
  OKX_ACTIVE=off, OKX_ACT_PARK=off (the «Импульс BTC» sleeve waits in USDT instead of BTC),
  OKX_FUT_MAX_LEV (2, at most 3), OKX_IS_LEAD=1, OKX_SPOT_LEAD=off (okx_lead.py), OKX_HOST, OKX_QUOTE (found by itself), OKX_KEEP, OKX_MAX_ORDER_USD,
  OKX_MAX_DAY_USD, OKX_SLIP, OKX_TAKER_FEE (0.002 on OKX Europe since 25.09.2026, 0.001 elsewhere), OKX_AUTOPILOT_STATE, OKX_STOP_FILE.
Not financial advice. Stdlib only."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import myspot_watch as ms
from . import okx_alloc as al
from . import okx_lead as ld
from . import okx_lease as lease

LOG = "data/okx_autopilot.jsonl"
HOSTS = ms.HOSTS
EEA_HOSTS = ("https://eea.okx.com", "https://my.okx.com")
QUOTES = ["USDT", "USDC", "EUR"]            # global account
QUOTES_EEA = ["USDC", "EUR", "USDT"]        # OKX Europe: USDT is not tradeable in the EEA under MiCA
TAKER = {"eea": 0.002, "global": 0.001}     # spot taker fee (IOC orders take liquidity): OKX Europe 0.20 % since 25.09.2026
MIN_ORDER = ms.MIN_ORDER             # $10 — smaller moves are not worth an order
BAND = 0.003                          # a coin within 0.3 % of the account of its target is left alone
DRIFT_EVERY_MS = 3_600_000            # the account is compared with the public portfolio at least hourly
SPOT_EVERY_S = 300                    # the public portfolio is read every 5 minutes in the window
PUBLIC_EVERY_S = 15 * 60              # server mode: web/public_real.json once at start, then every 15 minutes (public_real.py)
DAY = 86_400
VIA = "USDC"                          # a coin without a USDT pair is traded through USDC on the global account
PAIR = "USDC-USDT"                    # where the USDC is bought and sold back
VIA_PAD = 0.002                       # USDC bought above the coin order (on top of the fee): price ticks, rounding
SWEEP_MIN = 5.0                       # free USDC below $5 stays (not worth an order)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLANNED = {"restart": False}         # a planned restart onto new code keeps the lease (main)


# ── env / state ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _env(env=None) -> dict:
    return os.environ if env is None else env


def creds(env=None):
    e = _env(env)
    c = tuple((e.get(k) or "").strip() for k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"))
    return c if all(c) else None


def ipv4_only(env=None) -> bool:
    """22.10: the trade key is bound to the Mac's IP. A home Mac usually has IPv6 too, with a temporary address that changes every
    day, and OKX sees whichever protocol the connection used — so the bound address would stop matching. This process talks over
    IPv4 only (a name without an IPv4 address still resolves as before), so the IPv4 address bound to the key (`curl -4 -s
    https://ifconfig.me`) is the one OKX sees. OKX_IPV6=on turns it off."""
    if (_env(env).get("OKX_IPV6") or "").strip().lower() in ("on", "1", "yes", "true"):
        return False
    import socket
    if getattr(socket.getaddrinfo, "_cnq_v4", False):
        return True
    orig = socket.getaddrinfo

    def v4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 — the stdlib's own parameter names
        if family in (0, socket.AF_UNSPEC):
            try:
                return orig(host, port, socket.AF_INET, type, proto, flags)
            except socket.gaierror:
                pass
        return orig(host, port, family, type, proto, flags)
    v4._cnq_v4 = True
    socket.getaddrinfo = v4
    return True


def live(env=None) -> bool:
    return (_env(env).get("CNQ_LIVE_TRADING") or "no").strip().lower() == "yes"


def enabled(env=None) -> bool:
    return (_env(env).get("OKX_AUTOPILOT") or "off").strip().lower() in ("on", "yes", "1", "true")


def limits(env=None) -> dict:
    e = _env(env)

    def num(k, d):
        try:
            v = float(e.get(k) or d)
            return v if v > 0 else d
        except ValueError:
            return d
    return {"max_order": num("OKX_MAX_ORDER_USD", 3000.0), "max_day": num("OKX_MAX_DAY_USD", 25000.0),
            "slip": min(0.02, num("OKX_SLIP", 0.004))}


def keep_set(env=None) -> set:
    return {c.strip().upper() for c in (_env(env).get("OKX_KEEP") or "").split(",") if c.strip()}


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_AUTOPILOT_STATE") or "~/.cryptonova_okx_autopilot.json")


def stop_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_STOP_FILE") or "~/.cryptonova_okx_stop")


def stopped(env=None) -> bool:
    return os.path.exists(stop_path(env))


def load_state(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def log(ev: dict, path: str | None = None) -> None:
    p = path or LOG
    ev = {"t": int(time.time() * 1000), **ev}
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── OKX v5 client: reads, spot limit orders, order status, cancel. Nothing else exists here (no transfer, no withdrawal). ──
class OkxError(ms.OkxError):
    pass


class Okx:
    """One OKX account on one host. opener(method, url, headers, body) → raw JSON dict (tests pass a fake)."""

    def __init__(self, key: str, secret: str, passphrase: str, host: str = HOSTS[0], demo: bool = True, opener=None, sleep=time.sleep):
        self.key, self.secret, self.passphrase, self.host, self.demo = key, secret, passphrase, host, demo
        self._open = opener or self._http
        self._sleep = sleep
        self.lease_deadline = None      # set by a gated cycle (okx_lease.gate): no order goes out after this wall-clock ms

    @staticmethod
    def _http(method: str, url: str, headers: dict, body: str | None) -> dict:
        req = urllib.request.Request(url, data=body.encode() if body else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                return {"code": str(-e.code), "msg": f"HTTP {e.code}", "data": []}

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, signed: bool = True) -> list:
        if method != "GET" and self.lease_deadline is not None and time.time() * 1000 >= self.lease_deadline:
            # 22.10: this process's lease ran out mid-cycle (a sleeping Mac, a long pause) — another autopilot may trade now
            raise lease.LeaseLost("замок автопилота закончился посреди цикла — ордер не отправлен")
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        hdr = {"User-Agent": "cnq-okx-autopilot/1.0", "Accept": "application/json", "Content-Type": "application/json"}
        if self.demo:
            hdr["x-simulated-trading"] = "1"
        if signed:
            ts = ms._ts()
            hdr.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": ms.sign(self.secret, ts, method, path + q, payload),
                        "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
        for attempt in range(2 if method == "GET" else 1):          # a GET is retried once on a network error; a POST never blindly
            try:
                out = self._open(method, self.host + path + q, hdr, payload or None)
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 0 and method == "GET":
                    self._sleep(1.0)
                    continue
                raise
        code = str(out.get("code"))
        if code != "0":
            cls = ms.KeyRejected if code in ms.KEY_REJECTED else OkxError
            data = out.get("data") or []
            msg = (data[0].get("sMsg") if data and isinstance(data[0], dict) else "") or out.get("msg") or ""
            raise cls(code, msg, path)
        return out.get("data") or []

    # GET-only adapter for myspot_watch.read_raw / assets (the same merge of trading + funding + Earn as the card)
    def fetch(self, host: str, path: str, params: dict | None = None, signed: bool = True) -> dict:
        saved = self.host
        self.host = host
        try:
            return {"code": "0", "data": self.call("GET", path, params or None, signed=signed)}
        except ms.OkxError as e:
            return {"code": e.code, "msg": e.msg, "data": []}
        finally:
            self.host = saved

    def config(self) -> dict:
        d = self.call("GET", "/api/v5/account/config")
        return d[0] if d else {}

    def instrument(self, inst_id: str) -> dict | None:
        try:
            d = self.call("GET", "/api/v5/public/instruments", {"instType": "SPOT", "instId": inst_id}, signed=False)
        except ms.OkxError:
            return None
        x = d[0] if d else None
        return x if x and x.get("state") == "live" else None

    def ticker(self, inst_id: str) -> dict | None:
        try:
            d = self.call("GET", "/api/v5/market/ticker", {"instId": inst_id}, signed=False)
        except ms.OkxError:
            return None
        return d[0] if d else None

    def avail(self, ccy: str) -> float:
        d = self.call("GET", "/api/v5/account/balance", {"ccy": ccy})
        for c in ((d[0].get("details") if d else None) or []):
            if c.get("ccy") == ccy:
                return float(c.get("availBal") or c.get("cashBal") or 0)
        return 0.0

    def order(self, inst_id: str, side: str, px: str, sz: str, cl_id: str, td: str = "cash") -> dict:
        if td not in ("cash", ld.TD):
            raise ValueError(f"tdMode {td}")
        body = {"instId": inst_id, "tdMode": td, "side": side, "ordType": "ioc", "px": px, "sz": sz, "clOrdId": cl_id}
        d = self.call("POST", "/api/v5/trade/order", body=body)
        o = d[0] if d else {}
        if str(o.get("sCode", "0")) != "0":
            raise OkxError(o.get("sCode"), o.get("sMsg") or "", "/api/v5/trade/order")
        return o

    def order_state(self, inst_id: str, ord_id: str | None = None, cl_id: str | None = None) -> dict | None:
        p = {"instId": inst_id, **({"ordId": ord_id} if ord_id else {"clOrdId": cl_id})}
        try:
            d = self.call("GET", "/api/v5/trade/order", p)
        except ms.OkxError:
            return None
        return d[0] if d else None

    def pending(self) -> list:
        try:
            return self.call("GET", "/api/v5/trade/orders-pending", {"instType": "SPOT"})
        except ms.OkxError:
            return []

    def cancel(self, inst_id: str, ord_id: str) -> None:
        self.call("POST", "/api/v5/trade/cancel-order", body={"instId": inst_id, "ordId": ord_id})


def connect(env=None, opener=None, state: dict | None = None, sleep=time.sleep) -> Okx:
    """The client on the host where the key works (the remembered one first, then global / EEA / my.okx.com). The key's own world
    (real account or OKX demo trading) is found too, so --check and --plan can READ either way; whether orders may be placed is
    decided in run_once (a real-account key trades only with CNQ_LIVE_TRADING=yes)."""
    c = creds(env)
    if not c:
        raise RuntimeError("нет строк OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE в ~/.cryptonova.env")
    st = state if state is not None else load_state(env)
    demo = not live(env)
    forced = (_env(env).get("OKX_HOST") or "").strip().rstrip("/")
    if forced and not forced.startswith("http"):
        forced = "https://" + forced
    prefer = st.get("host")
    order = [forced] if forced else ([prefer] if prefer in HOSTS else []) + [h for h in HOSTS if h != prefer]
    first = None
    for world in (demo, not demo):              # the configured world first; the other one only to read and explain
        for h in order:
            cl = Okx(*c, host=h, demo=world, opener=opener, sleep=sleep)
            try:
                cl.call("GET", "/api/v5/account/balance")
                st["host"] = h
                return cl
            except ms.KeyRejected as e:
                first = first or e
    raise first or ms.KeyRejected("50111", "key rejected on every OKX host")


def world_ok(cl: Okx, env=None) -> str | None:
    """None when orders may go out; otherwise why not (the key's world and the window's mode differ)."""
    if not cl.demo and not live(env):
        return ("Это ключ РЕАЛЬНОГО счёта, а окно запущено в демо-режиме — ордера не ставлю. Для реальных денег: "
                "bash scripts/okx_autopilot_mac.sh run --live (скрипт попросит написать ДА).")
    if cl.demo and live(env):
        return "Это ключ ДЕМО-торговли OKX, а включён режим реальных денег — ордера не ставлю. Для реального счёта нужен ключ основного счёта."
    return None


def perm_of(cfg: dict) -> set:
    return {p.strip() for p in str(cfg.get("perm") or "").split(",") if p.strip()}


def is_eea(cl: Okx) -> bool:
    return cl.host in EEA_HOSTS


def pick_quote(cl: Okx, env=None) -> str | None:
    """The currency the account trades in. OKX Europe: USDC, else EUR (never USDT — not tradeable in the EEA); global: USDT,
    else USDC, else EUR. OKX_QUOTE forces one."""
    forced = (_env(env).get("OKX_QUOTE") or "").strip().upper()
    for q in ([forced] if forced else QUOTES_EEA[:2] if is_eea(cl) else QUOTES):
        if cl.instrument(f"BTC-{q}"):
            return q
    return None


def taker_fee(cl: Okx, env=None) -> float:
    try:
        v = float(_env(env).get("OKX_TAKER_FEE") or 0)
        if 0 < v < 0.01:
            return v
    except ValueError:
        pass
    return TAKER["eea" if is_eea(cl) else "global"]


# ── through USDC: a coin OKX lists only against USDC (22.10) ─────────────────────────────────────────────────────────────
def usdt_account(cl: Okx, env=None) -> bool:
    """The global account trading in USDT — the only one where a coin may go through USDC (OKX Europe trades in USDC itself and
    has no USDT). No network."""
    return not is_eea(cl) and ((_env(env).get("OKX_QUOTE") or "USDT").strip().upper() == "USDT")


def route(cl: Okx, base: str, quote: str, memo: dict | None = None) -> dict:
    """Where one coin trades: {"inst", "via", "info", "tk"}. {COIN}-{quote} first; on the USDT account a coin with no USDT pair goes
    through USDC — {COIN}-USDC (via «USDC») when both it and USDC-USDT are live. info None = no pair at all ("none" names both)."""
    memo = {} if memo is None else memo

    def get(iid):
        if iid not in memo:
            memo[iid] = (cl.instrument(iid), cl.ticker(iid))
        return memo[iid]
    iid = f"{base}-{quote}"
    info, tk = get(iid)
    if info or quote != "USDT" or base in (VIA, quote):
        return {"inst": iid, "via": None, "info": info, "tk": tk, "none": iid}
    viid = f"{base}-{VIA}"
    vinfo, vtk = get(viid)
    if vinfo and get(PAIR)[0]:
        return {"inst": viid, "via": VIA, "info": vinfo, "tk": vtk}
    return {"inst": iid, "via": None, "info": None, "tk": None, "none": f"{iid} и {viid}"}


def route_text(o: dict) -> str:
    """«через USDC: USDT → USDC → COIN» (a buy) / «через USDC: COIN → USDC → USDT» (a sale); '' for a direct pair."""
    if not o.get("via"):
        return ""
    return (f"через {VIA}: USDT → {VIA} → {o['ccy']}" if o["side"] == "buy" else f"через {VIA}: {o['ccy']} → {VIA} → USDT")


def via_rate(cl: Okx, memo: dict | None = None) -> float:
    """Dollars per USDC (the USDC-USDT price; ≈ 1)."""
    memo = {} if memo is None else memo
    if PAIR not in memo:
        memo[PAIR] = (cl.instrument(PAIR), cl.ticker(PAIR))
    v = ms._f((memo[PAIR][1] or {}).get("last"))
    return v if v and 0.9 < v < 1.1 else 1.0


def convert(cl: Okx, side: str, qty: float, cl_id: str, sleep, slip: float) -> dict:
    """USDT → USDC (side «buy», qty USDC) or USDC → USDT («sell») on USDC-USDT: a limit IOC at most OKX_SLIP from the best price,
    never more than the money that is there."""
    inst, tk = cl.instrument(PAIR), cl.ticker(PAIR)
    ref = ms._f((tk or {}).get("askPx" if side == "buy" else "bidPx")) or ms._f((tk or {}).get("last"))
    if not inst or not ref:
        return {"filled": 0.0, "usd": 0.0, "why": f"нет пары {PAIR}"}
    lot, mn, tick = float(inst["lotSz"]), float(inst["minSz"]), float(inst["tickSz"])
    px = ceil_to(ref * (1 + slip), tick) if side == "buy" else floor_to(ref * (1 - slip), tick)
    if side == "buy":
        q = min(max(ceil_to(qty, lot), mn), floor_to(cl.avail("USDT") / px, lot))
    else:
        q = floor_to(min(qty, cl.avail(VIA)), lot)
    if q < mn or q <= 0:
        return {"filled": 0.0, "usd": 0.0, "why": f"меньше минимума {PAIR} или нет денег"}
    return _fill(cl, {"inst": PAIR, "side": side, "px": fmt(px, inst["tickSz"]), "sz": fmt(q, inst["lotSz"])}, cl_id, sleep)


def fill_via(cl: Okx, o: dict, cl_id: str, sleep, env=None, log_path: str | None = None) -> dict:
    """One coin order through USDC (o["via"] == "USDC", o["inst"] = COIN-USDC, px in USDC). Buy: USDC on USDC-USDT first (only what is
    missing), then COIN-USDC for what that USDC covers. Sell: COIN-USDC, then the USDC back to USDT. Returns the COIN leg's fill
    (_fill) + "half" — a plain line when one leg did not complete. A half is never left: the unused USDC goes back to USDT
    (here after a sale, at the end of the pass after a buy — sweep()) and the coin leg is retried on the next cycle."""
    slip, fee = limits(env)["slip"], taker_fee(cl, env)
    lot, mn = float(o["lot"]), float(o["min"])
    if o["side"] == "buy":
        px, want = float(o["px"]), float(o["sz"])
        have = cl.avail(VIA)
        need = want * px * (1 + fee + VIA_PAD)
        if have < need:
            c = convert(cl, "buy", (need - have) / (1 - fee), cl_id + "u", sleep, slip)
            log({"ev": "usdc_in", "for": o["ccy"], "need": round(need - have, 2), **c,
                 "note": f"купил {VIA} на USDC-USDT для покупки {o['ccy']}"}, log_path)
            have = cl.avail(VIA)
        q = min(want, floor_to(have / px, lot))
        if q < mn or q * px < 1.0:
            why = f"{VIA} не докупился — {o['ccy']} не куплен; {VIA} вернётся в USDT в конце прохода, покупка — на следующем цикле"
            log({"ev": "via_half", "ccy": o["ccy"], "side": "buy", "note": why}, log_path)
            return {"ordId": None, "state": "not_sent", "filled": 0.0, "avg_px": 0.0, "usd": 0.0, "fee": 0.0, "full": False, "half": why}
        if stopped(env):
            why = f"стоп между ногами: {VIA} куплен, {o['ccy']} нет — {VIA} остаётся (это доллары)"
            log({"ev": "via_half", "ccy": o["ccy"], "side": "buy", "note": why}, log_path)
            return {"ordId": None, "state": "stopped", "filled": 0.0, "avg_px": 0.0, "usd": 0.0, "fee": 0.0, "full": False, "half": why}
        f = _fill(cl, {**o, "sz": fmt(q, o["lot"])}, cl_id, sleep)
        f["full"] = bool(f["filled"]) and f["filled"] >= want * 0.999
        if not f["full"]:
            got = f"куплен не весь ({f['filled']:g} из {want:g})" if f["filled"] > 0 else "не куплен (IOC не исполнился)"
            f["half"] = f"{o['ccy']} {got} — лишние {VIA} вернутся в USDT в конце прохода, остальное — на следующем цикле"
            log({"ev": "via_half", "ccy": o["ccy"], "side": "buy", "note": f["half"]}, log_path)
        return f
    f = _fill(cl, o, cl_id, sleep)
    if f["filled"] > 0:
        try:
            c = convert(cl, "sell", cl.avail(VIA), cl_id + "t", sleep, slip)
            log({"ev": "usdc_out", "for": o["ccy"], **c, "note": f"продал {VIA} обратно в USDT после продажи {o['ccy']}"}, log_path)
            ok = c.get("filled", 0) > 0
        except Exception as e:  # noqa: BLE001
            ok, c = False, {"why": str(e)[:160]}
        if not ok:
            f["half"] = f"{o['ccy']} продан, а {VIA} в USDT не вернулся ({c.get('why') or 'не исполнилось'}) — повторю в конце прохода"
            log({"ev": "via_half", "ccy": o["ccy"], "side": "sell", "note": f["half"]}, log_path)
    return f


def sweep(cl: Okx, env=None, cl_id: str = "", sleep=time.sleep, log_path: str | None = None, floor: float = SWEEP_MIN) -> dict | None:
    """Free USDC (≥ $5) back to USDT — at the end of every pass on the USDT account. Not with the kill switch, not when USDC is
    in OKX_KEEP. Margin of an open USDC swap is not free (isolated), so it is never touched. Errors are logged, retried next pass."""
    if not usdt_account(cl, env) or VIA in keep_set(env) or stopped(env):
        return None
    try:
        a = cl.avail(VIA)
        if a < floor:
            return None
        c = convert(cl, "sell", a, cl_id or f"cnqau{int(time.time())}", sleep, limits(env)["slip"])
    except Exception as e:  # noqa: BLE001
        log({"ev": "usdc_back_error", "error": str(e)[:200], "note": f"свободные {VIA} не вернулись в USDT — повторю на следующем проходе"}, log_path)
        return {"filled": 0.0, "error": str(e)[:200]}
    log({"ev": "usdc_back", "avail": round(a, 2), **c, "note": f"свободные {VIA} ≈ {usd(a)} → USDT"}, log_path)
    return c


# ── the plan → orders ────────────────────────────────────────────────────────────────────────────────────────────────
def _dec(step: str) -> int:
    s = str(step)
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def floor_to(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step if step > 0 else x


def ceil_to(x: float, step: float) -> float:
    return math.ceil(x / step - 1e-9) * step if step > 0 else x


def fmt(x: float, step: str) -> str:
    return f"{x:.{_dec(step)}f}"


def trading_avail(raw: dict) -> dict:
    """{CCY: available quantity on the trading account} — only these can be sold by the autopilot."""
    out = {}
    for c in raw.get("trading") or []:
        q = ms._f(c.get("availBal"))
        if q is None:
            q = ms._f(c.get("cashBal")) or ms._f(c.get("eq")) or 0.0
        if q and q > 0:
            out[str(c.get("ccy")).upper()] = q
    return out


def _active_held(env=None, lead: bool | None = None) -> dict:
    try:
        from . import okx_active
        return okx_active.held(env, lead)
    except Exception:  # noqa: BLE001
        return {}


def build(cl: Okx, env=None, model=None) -> dict:
    """Read the account and turn «Наш портфель CRYPTO NOVA» (× the spot share) into exact orders. Places nothing.
    model = web/portfolio.json (default: fetched). A spot lead trader (okx_lead.py): lead positions are counted like ordinary coins,
    sales may take them (lead first), and the orders are marked lead / ordinary with the instruments the next sync would set."""
    keep = keep_set(env)
    quote = pick_quote(cl, env)
    if not quote:
        raise RuntimeError("на этом OKX нет пары BTC в нужной валюте (USDC / EUR" + ("" if is_eea(cl) else " / USDT") + ") — укажи OKX_QUOTE")
    pf = model if model is not None else al.fetch_portfolio()
    pos = al.ideas(pf)
    if not pos:
        raise RuntimeError("публичный портфель CRYPTO NOVA (web/portfolio.json) не загрузился — ничего не меняю")
    meta = al.meta(pf)
    w = al.shares(env)
    raw = ms.read_raw(cl.fetch, cl.host)               # lead spot positions already merged in (okx_lead.merge)
    lctx = ld.context(lambda p, q=None: cl.call("GET", p, q), raw.get("cfg") or {}, env, raw)
    if lctx.get("fresh") and lctx["held"]:
        raw["trading"] = ld.merge(raw.get("trading") or [], lctx["held"])
    act = _active_held(env)                            # coins of the active-spot sleeve: never counted or sold here
    act_lead = _active_held(env, True) if lctx["held"] else {}     # … of them held as lead positions (okx_active, spot lead)
    for c, q in act_lead.items():
        if c in lctx["held"]:
            lctx["held"][c] = {**lctx["held"][c], "avail": max(0.0, lctx["held"][c]["avail"] - q)}
    avail = trading_avail(raw)
    rows_all = ms.assets(raw)
    total = sum(r["usd"] for r in rows_all)
    rows = []
    for r in rows_all:
        q = act.get(r["ccy"], 0.0)
        if q and r["qty"]:
            k = max(0.0, 1 - q / r["qty"])
            r = {**r, "qty": r["qty"] * k, "usd": round(r["usd"] * k, 2)}
            avail[r["ccy"]] = max(0.0, avail.get(r["ccy"], 0.0) - (q - min(q, act_lead.get(r["ccy"], 0.0))))
        if r["ccy"] not in keep and r["usd"] >= 0.01:
            rows.append(r)
    held = {r["ccy"]: r for r in rows}
    lim = limits(env)
    sells, buys, notes, diffs = [], [], [], []
    memo = {}                                          # instId → (instrument, ticker) for this plan
    routes = {}                                        # coin → (via, instId) for the lead marks

    def look(base: str):
        r = route(cl, base, quote, memo)
        last = ms._f((r["tk"] or {}).get("last"))
        return (r["inst"], last * (via_rate(cl, memo) if r["via"] else 1.0)) if r["info"] and last else None

    tgt_share = al.targets(w, pos)
    targets, mapped = {}, {}
    for sym, t in sorted(pos.items(), key=lambda x: -x[1]["w"]):
        m = al.resolve(sym, t.get("price"), look)
        if not m["ok"]:
            diffs.append(f"{sym} ({al._p(tgt_share[sym])} счёта): {m['why']} — эта доля остаётся в {quote}")
            continue
        if m["base"] in keep:
            diffs.append(f"{sym}: монета в OKX_KEEP — автопилот её не трогает (у портфеля {al._p(tgt_share[sym])} счёта)")
            continue
        targets[m["base"]] = targets.get(m["base"], 0.0) + tgt_share[sym]
        mapped[m["base"]] = {**t, "sym": sym, "mult": m["mult"]}
    # owner's decision of 28.10 (real money): the 10 % freed from the futures → the core BTC / ETH / SOL in the plan proportions
    # 30 / 10 / 5, on top of the spot portfolio's own core (okx_alloc.core_extra); bought by the same rebalance (sells first, the
    # $10 minimum, the 0.3 % band, IOC limits, the per-order and daily caps)
    for c, f in al.core_extra(w).items():
        if c in keep:
            diffs.append(f"{c}: монета в OKX_KEEP — доп. доля ядра {al._p(f)} счёта остаётся в {quote}")
            continue
        targets[c] = targets.get(c, 0.0) + f
        mapped.setdefault(c, {"sym": c, "w": 0.0, "name": c, "kind_ru": "ядро", "role_ru": "", "why": "", "stop": None,
                              "take_half": None, "price": None, "mult": 1.0})
        mapped[c]["core_extra"] = f
    outside = [r["ccy"] for r in rows if not r["cash"] and set(r.get("where") or []) - {"торговый счёт"}]
    if outside:
        diffs.append("Монеты вне торгового счёта (Funding / Earn) учтены, но автопилот их не продаёт и не переводит: "
                     + ", ".join(sorted(outside)) + ". Переведи их на торговый счёт.")
    sweeps = usdt_account(cl, env) and VIA not in keep
    other_cash = [r["ccy"] for r in rows if r["cash"] and r["ccy"] != quote and r["usd"] >= MIN_ORDER and not (sweeps and r["ccy"] == VIA)]
    if other_cash:
        notes.append(f"Доллары в другой валюте ({', '.join(other_cash)}) считаются долларами, автопилот их не меняет.")
    if sweeps and held.get(VIA, {}).get("usd", 0) >= SWEEP_MIN:
        notes.append(f"{VIA} ≈ {usd(held[VIA]['usd'])} считаются долларами; свободные {VIA} автопилот вернёт в USDT в конце прохода.")
    band = max(MIN_ORDER, BAND * total)

    def chunks(qty: float, usd_: float, lot: float) -> list:
        n = max(1, math.ceil(usd_ / lim["max_order"]))
        part = floor_to(qty / n, lot)
        out = [part] * (n - 1)
        out.append(floor_to(qty - part * (n - 1), lot))
        return [q for q in out if q > 0]

    for c in sorted(set(targets) | {c for c, r in held.items() if not r["cash"]}):
        r = held.get(c)
        now_usd = r["usd"] if r else 0.0
        tgt = targets.get(c, 0.0)
        gap = tgt * total - now_usd
        now_pct, t_pct = (now_usd / total * 100 if total else 0.0), tgt * 100
        why = al.reason(mapped[c]) if c in mapped else al.sell_reason(c, meta)
        rt = route(cl, c, quote, memo)                                  # {c}-{quote}, or {c}-USDC through USDC (22.10)
        inst_id, inst, tk, via = rt["inst"], rt["info"], rt["tk"], rt["via"]
        routes[c] = (via, inst_id)
        k = via_rate(cl, memo) if via else 1.0                         # dollars per unit of the pair's quote (USDC ≈ $1)
        if gap < 0 and (tgt == 0 or -gap >= band):                    # sell: not in the portfolio, or over its share
            bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
            if not inst or not bid:
                diffs.append(f"{c}: на OKX нет пары {rt['none']} — продать не могу, монета остаётся.")
                continue
            lot, mn, tick = float(inst["lotSz"]), float(inst["minSz"]), float(inst["tickSz"])
            have = avail.get(c, 0.0) + (((lctx["held"].get(c) or {}).get("avail") or 0.0) if lctx["active"] and not via else 0.0)
            qty = floor_to(have if tgt == 0 else min(have, -gap / (bid * k)), lot)
            if qty < mn or qty * bid * k < (1.0 if tgt == 0 else MIN_ORDER):
                if tgt == 0 and have * bid * k >= MIN_ORDER:
                    diffs.append(f"{c}: остаток меньше минимального ордера OKX — остаётся.")
                continue
            if tgt > 0 and have < -gap / (bid * k) * 0.98:
                notes.append(f"{c}: на торговом счёте меньше, чем надо продать по плану — продаю что есть.")
            px = fmt(floor_to(bid * (1 - lim["slip"]), tick), inst["tickSz"])
            for q in chunks(qty, qty * bid * k, lot):
                if q >= mn:
                    sells.append({"ccy": c, "inst": inst_id, "side": "sell", "sz": fmt(q, inst["lotSz"]), "px": px, "usd": round(q * bid * k, 2),
                                  "lot": inst["lotSz"], "min": mn, "via": via, "pxq": inst_id.split("-")[1],
                                  "act": "sell" if tgt == 0 else "trim", "target_pct": round(t_pct, 2), "now_pct": round(now_pct, 2), "why": why})
        elif gap > 0 and gap >= (MIN_ORDER if now_usd < MIN_ORDER else band):   # buy: a new coin, or under its share
            ask = ms._f((tk or {}).get("askPx")) or ms._f((tk or {}).get("last"))
            if not inst or not ask:
                diffs.append(f"{c}: на OKX нет пары {rt['none']} — доля остаётся в {quote}.")
                continue
            lot, mn, tick = float(inst["lotSz"]), float(inst["minSz"]), float(inst["tickSz"])
            qty = floor_to(gap / (ask * k), lot)
            if qty < mn or qty * ask * k < MIN_ORDER:
                diffs.append(f"{c}: покупка {usd(gap)} меньше минимального ордера OKX — пропускаю.")
                continue
            px = fmt(ceil_to(ask * (1 + lim["slip"]), tick), inst["tickSz"])
            for q in chunks(qty, qty * ask * k, lot):
                if q >= mn:
                    buys.append({"ccy": c, "inst": inst_id, "side": "buy", "sz": fmt(q, inst["lotSz"]), "px": px, "usd": round(q * ask * k, 2),
                                 "lot": inst["lotSz"], "min": mn, "ask": ask * k, "via": via, "pxq": inst_id.split("-")[1], "act": "buy",
                                 "target_pct": round(t_pct, 2), "now_pct": round(now_pct, 2), "why": why, "core": c in al.CORE})
    buys.sort(key=lambda o: (not o["core"], -o["usd"]))           # the core first, then the bigger gaps
    fee = taker_fee(cl, env)
    turnover = sum(o["usd"] for o in sells) + sum(o["usd"] for o in buys)
    spot_now = sum(r["usd"] for r in rows if not r["cash"])
    rows_t = [{"ccy": c, "target_pct": round(targets[c] * 100, 2), "now_pct": round((held[c]["usd"] if c in held else 0) / total * 100, 2) if total else 0.0,
               "sym": mapped[c]["sym"], "pf_w": mapped[c]["w"]} for c in sorted(targets, key=lambda x: -targets[x])]
    b = {"quote": quote, "host": cl.host, "total_usd": round(total, 2), "split": w, "pf_name": meta["name"], "pf_cash": meta["cash"],
         "sells": sells, "buys": buys, "notes": notes, "diffs": diffs, "keep": sorted(keep), "sig": al.signature(pf, w),
         "targets": rows_t, "spot_now_usd": round(spot_now, 2), "active_held": act, "fee_rate": fee, "fee_usd": round(turnover * fee, 2),
         "eea": is_eea(cl), "same": not diffs,
         # 22.10 spot lead (okx_lead.py): the unsplit orders, the ordinary coins that can be sold, the routes, prices, the context
         "sells0": sells, "buys0": buys, "_avail": {c: q for c, q in avail.items() if c not in keep}, "_routes": routes,
         "_px": {r["ccy"]: r["usd"] / r["qty"] for r in rows if r.get("qty")}, "_ctx": lctx,
         "_want": ld.want(lctx, [c for c in targets if not routes.get(c, (None,))[0]], quote) if lctx["active"] else set()}
    return ld.mark(b, lctx, b["_want"] if lctx["active"] else set())


def usd(v: float) -> str:
    """$1 234.56 — spaces between thousands (the comma stays free for the text)."""
    return f"${v:,.2f}".replace(",", " ")


def model_sig(pf: dict, env=None) -> str:
    return al.signature(pf, al.shares(env))


def _q(o: dict) -> str:
    return f" {o['pxq']}" if o.get("via") else ""


def _r(o: dict) -> str:
    return f" · {route_text(o)}" if o.get("via") else ""


def plan_text(b: dict, mode: str) -> str:
    """The spot part for the owner's eyes (Russian, digits): the split, every order with its reason, the differences."""
    T = b["total_usd"]
    L = [f"Автопилот OKX · {mode} · {b['host'].replace('https://', '')} · валюта {b['quote']} · счёт {usd(T)}", "ДЕЛЕНИЕ СЧЁТА:"]
    L += ["  " + x for x in al.split_text(b["split"], T)]
    L.append(f"СПОТ · {b['pf_name']} — {al._p(b['split']['spot'])} счёта ≈ {usd(T * b['split']['spot'])}; в монетах сейчас {usd(b['spot_now_usd'])}."
             f" Кэш портфеля {al._p(b['pf_cash'])} (правило: не меньше 10%) — остаётся в {b['quote']}.")
    L.append("  Цель по монетам (доля счёта = доля спота × вес в портфеле; у BTC / ETH / SOL плюс доп. ядро "
             f"{al._p(b['split'].get('core') or 0)} счёта в пропорции 30 / 10 / 5): "
             + ", ".join(f"{r['ccy']} {ms._p(r['target_pct'])}% (сейчас {ms._p(r['now_pct'])}%)" for r in b["targets"]))
    L += ld.plan_lines(b)
    on = (b.get("lead") or {}).get("active")
    cp = (lambda o: (" · копируется" if o.get("lead") else " · не копируется (монеты, купленные до включения)" if o.get("legacy")
                     else " · не копируется") if on else "")
    if b["sells"]:
        L.append("  Сначала продажи:")
        for o in b["sells"]:
            what = "продать всё" if o["act"] == "sell" else f"урезать до {ms._p(o['target_pct'])}%"
            L.append(f"    ПРОДАТЬ {o['sz']} {o['ccy']} ≈ {usd(o['usd'])} · {what} · лимит не ниже {o['px']}{_q(o)}{_r(o)}{cp(o)} · почему: {o['why']}")
    if b["buys"]:
        L.append("  Потом покупки (сначала ядро):")
        for o in b["buys"]:
            L.append(f"    КУПИТЬ {o['sz']} {o['ccy']} ≈ {usd(o['usd'])} · до {ms._p(o['target_pct'])}% счёта · лимит не выше {o['px']}{_q(o)}{_r(o)}"
                     f"{cp(o)} · почему: {o['why']}")
    if not b["sells"] and not b["buys"]:
        L.append("  Спот уже совпадает с портфелем (отличия меньше $10 или 0,3% счёта) — ордеров нет.")
    if b.get("active_held"):
        L.append("  Монеты активного спота (не трогаю): " + ", ".join(f"{c} {q:g}" for c, q in b["active_held"].items()))
    if b["keep"]:
        L.append("  Не трогаю (OKX_KEEP): " + ", ".join(b["keep"]))
    L += ["  " + n for n in b["notes"]]
    L.append(f"  Итого спот: продать ≈ {usd(sum(o['usd'] for o in b['sells']))}, купить ≈ {usd(sum(o['usd'] for o in b['buys']))}, "
             f"комиссия OKX ≈ {usd(b.get('fee_usd', 0))} ({ms._p(round(b.get('fee_rate', 0) * 100, 2))}% с каждой сделки). "
             "Ордера лимитные (IOC): исполняется то, что есть по цене не хуже указанной, остаток — при следующей проверке.")
    if any(o.get("via") for o in b["sells"] + b["buys"]):
        L.append(f"  Через {VIA}: у этих монет на OKX нет пары с USDT. Покупка — сначала {VIA} на паре {PAIR} (сумма ордера + комиссия и "
                 f"запас), потом монета за {VIA}; продажа — монета за {VIA}, потом {VIA} обратно в USDT. Обе части — лимит IOC; "
                 f"лишние {VIA} возвращаются в USDT в конце прохода.")
    return "\n".join(x for x in L if x)


def same_text(b: dict, fut_on: bool, act_on: bool) -> str:
    """«Совпадает с системой CRYPTO NOVA: да / нет» and every difference."""
    d = list(b["diffs"])
    if not fut_on:
        d.append(f"фьючерсы выключены (OKX_FUTURES=off) — их {al._p(b['split']['futures'])} лежат в {b['quote']}")
    if not act_on:
        d.append(f"активный спот выключен (OKX_ACTIVE=off) — его {al._p(b['split']['active'])} лежат в {b['quote']}")
    L = ["СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA: " + ("ДА" if not d else "НЕТ")]
    L += ["  отличие: " + x for x in d]
    L.append("  по устройству: фьючерсы повторяют сигналы с риском как у системы (не больше 1% бюджета на сделку, плечо ≤ 3, до 5 позиций) — "
             "размер может быть меньше, чем у демо-счёта системы; сделку, открытую до запуска, повторяем, только пока цена не дальше 0,3% "
             "от входа системы; продажи и стопы спот-портфеля "
             "приходят с его обновлением (проверка каждые 5 минут).")
    return "\n".join(L)


# ── execution ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _fill(cl: Okx, o: dict, cl_id: str, sleep) -> dict:
    """Place one IOC order and read its final state. A network error after sending is resolved by the client order id."""
    try:
        oid = cl.order(o["inst"], o["side"], o["px"], o["sz"], cl_id, o.get("td") or "cash").get("ordId")
    except (urllib.error.URLError, TimeoutError, OSError):
        sleep(1.0)
        s = cl.order_state(o["inst"], cl_id=cl_id)
        if not s:
            raise
        oid = s.get("ordId")
    s = None
    for _ in range(5):                       # IOC ends at once; OKX may need a moment to report the final state
        s = cl.order_state(o["inst"], ord_id=oid)
        if s and s.get("state") not in ("live", "partially_filled"):
            break
        sleep(0.4)
    fsz = ms._f((s or {}).get("accFillSz")) or 0.0
    apx = ms._f((s or {}).get("avgPx")) or 0.0
    return {"ordId": oid, "state": (s or {}).get("state") or "unknown", "filled": fsz, "avg_px": apx, "usd": round(fsz * apx, 2),
            "fee": ms._f((s or {}).get("fee")) or 0.0, "full": bool(fsz) and fsz >= float(o["sz"]) * 0.999}


def execute(cl: Okx, b: dict, st: dict, env=None, now: int | None = None, sleep=time.sleep, log_path: str | None = None) -> dict:
    """Sells first, then buys scaled to the money that is really there. Stops at the kill switch and the daily limit."""
    now = int(now or time.time() * 1000)
    lim = limits(env)
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    if st.get("day") != day:
        st["day"], st["day_usd"] = day, 0.0
    res = {"sold_usd": 0.0, "bought_usd": 0.0, "orders": 0, "partial": 0, "failed": 0, "skipped": 0, "stopped": False, "limit": False,
           "half": [], "usdc_back": 0.0}
    seq = 0
    rate = via_rate(cl) if any(o.get("via") for o in b["sells"] + b["buys"]) else 1.0

    def go(o: dict) -> None:
        nonlocal seq
        if stopped(env):
            res["stopped"] = True
            return
        if st["day_usd"] + o["usd"] > lim["max_day"]:
            res["limit"] = True
            res["skipped"] += 1
            log({"ev": "day_limit", **{k: o[k] for k in ("ccy", "side", "usd")}, "day_usd": st["day_usd"]}, log_path)
            return
        seq += 1
        cl_id = f"cnqap{now // 1000}{seq:03d}"
        try:
            try:
                f = fill_via(cl, o, cl_id, sleep, env, log_path) if o.get("via") else _fill(cl, o, cl_id, sleep)
            except ms.OkxError as e:
                if not (o.get("lead") and o["side"] == "sell" and o.get("act") == "sell"):
                    raise
                f = ld.close_out(cl, o, e, lambda ev: log(ev, log_path))      # a full exit: close the lead positions instead
        except Exception as e:  # noqa: BLE001
            res["failed"] += 1
            log({"ev": "order_failed", "ccy": o["ccy"], "side": o["side"], "sz": o["sz"], "px": o["px"], "error": str(e)[:200]}, log_path)
            return
        res["orders"] += 1
        if f.get("half"):
            res["half"].append(f"{o['ccy']}: {f['half']}")
        if f["filled"] <= 0:
            res["skipped"] += 1
        elif not f["full"]:
            res["partial"] += 1
        k = rate if o.get("via") else 1.0
        st["day_usd"] = round(st["day_usd"] + f["usd"] * k, 2)                # the day's cap counts the coin leg (not the USDC legs)
        res["sold_usd" if o["side"] == "sell" else "bought_usd"] += f["usd"] * k
        log({"ev": "order", "ccy": o["ccy"], "side": o["side"], "sz": o["sz"], "px": o["px"], "inst": o["inst"],
             **({"td": o["td"]} if o.get("td") else {}),
             **({"route": route_text(o)} if o.get("via") else {}), **f}, log_path)
        sleep(0.15)

    for o in b["sells"]:
        go(o)
        if res["stopped"]:
            break
    if not res["stopped"] and b["buys"]:
        money = cl.avail(b["quote"])
        if usdt_account(cl, env) and any(o.get("via") for o in b["buys"]):
            money += cl.avail(VIA) * rate                         # USDC is dollars too (left from a sale, used first)
        money *= 0.995                                            # a little room for fees and price moves
        need = sum(o["usd"] for o in b["buys"])
        k = min(1.0, money / need) if need else 1.0
        if k < 1.0:
            log({"ev": "scale_buys", "money": round(money, 2), "need": round(need, 2), "k": round(k, 4)}, log_path)
        for o in b["buys"]:
            if k < 1.0:
                lot = float(o["lot"])
                q = floor_to(float(o["sz"]) * k, lot)
                if q < o["min"] or q * o["ask"] < MIN_ORDER:
                    res["skipped"] += 1
                    continue
                o = {**o, "sz": fmt(q, o["lot"]), "usd": round(q * o["ask"], 2)}
            go(o)
            if res["stopped"]:
                break
    if not res["stopped"]:
        sw = sweep(cl, env, f"cnqap{now // 1000}usdc", sleep, log_path)        # free USDC (≥ $5) back to USDT at the end of the pass
        res["usdc_back"] = round((sw or {}).get("usd") or 0.0, 2)
    res["sold_usd"], res["bought_usd"] = round(res["sold_usd"], 2), round(res["bought_usd"], 2)
    return res


def run_once(env=None, opener=None, model=None, now: int | None = None, sleep=time.sleep, reason: str = "вручную",
             log_path: str | None = None, save: bool = True) -> dict:
    """One spot rebalance (used by --run and by the window). Returns the summary; refuses unsafe keys and the kill switch."""
    now = int(now or time.time() * 1000)
    st = load_state(env)
    if stopped(env):
        return {"ok": False, "why": "включён стоп (--stop) — ордера не ставлю. Снять: --resume"}
    g = lease.gate(env, now)                    # 22.10: never two autopilots on one account (okx_lease.py)
    if not g["ok"]:
        return {"ok": False, "why": g["why"]}
    cl = connect(env, opener, st, sleep)
    cl.lease_deadline = lease.deadline(g, now)
    perm = perm_of(cl.config())
    if "withdraw" in perm:
        log({"ev": "refused", "why": "withdraw permission"}, log_path)
        return {"ok": False, "why": "У ключа OKX есть право ВЫВОДА — автопилот с таким ключом не работает. Удали ключ на OKX и создай новый: «Чтение» + «Торговля», без «Вывода»."}
    if "trade" not in perm:
        return {"ok": False, "why": "У ключа OKX нет права «Торговля» — создай ключ с правами «Чтение» + «Торговля» (без «Вывода»)."}
    bad = world_ok(cl, env)
    if bad:
        return {"ok": False, "why": bad}
    b = build(cl, env, model)
    if (b.get("lead") or {}).get("err"):
        log({"ev": "refused", "why": "lead positions unreadable", "error": b["lead"]["err"]}, log_path)
        return {"ok": False, "why": f"лидерские спот-позиции не прочитались ({b['lead']['err']}) — без них доли посчитать нельзя, ордеров нет"}
    if b["_ctx"]["active"]:                     # 22.10: the lead instruments = the portfolio's copyable coins (okx_lead.sync)
        ld.mark(b, b["_ctx"], ld.sync(cl, b["_ctx"], b["_want"], st, now, lambda ev: log(ev, log_path)))
    mode = "РЕАЛЬНЫЕ ДЕНЬГИ" if live(env) else "демо-торговля OKX"
    if live(env) and not cl.demo and not st.get("first_live"):
        st["first_live"] = {"t": now, "usd": b["total_usd"]}       # the start of «результат с начала» on the owner's card
    log({"ev": "plan", "mode": mode, "reason": reason, "total_usd": b["total_usd"], "sells": len(b["sells"]), "buys": len(b["buys"])}, log_path)
    res = execute(cl, b, st, env, now, sleep, log_path)
    summary = {"t": now, "reason": reason, "mode": mode, **res, "total_usd": b["total_usd"], "quote": b["quote"]}
    st.update({"last_run": now, "last_sig": b["sig"], "last": summary, "mode": mode, "split": b["split"], "same": b["same"],
               "diffs": b["diffs"][:12], "targets": b["targets"][:20], "drift_t": now, "pf_cash": b["pf_cash"]})
    if enabled(env) and not res["stopped"] and res["failed"] == 0:
        st["armed"] = True
    if save:
        save_state(st, env)
    log({"ev": "done", **summary}, log_path)
    return {"ok": True, **summary, "plan": b, "cl": cl}


def due(st: dict, pf: dict, now: int, env=None) -> str | None:
    """Why a spot rebalance is due now (None = not due): the public portfolio changed. Only for an armed, not stopped autopilot."""
    if not st.get("armed") or not enabled(env) or stopped(env):
        return None
    sig = model_sig(pf, env)
    if sig and sig != st.get("last_sig"):
        return "портфель CRYPTO NOVA изменился"
    return None


def drifted(b: dict) -> bool:
    """After partial / missed fills or a big move of one coin: any order of the plan bigger than max($25, 1 % of the account)."""
    lim = max(25.0, 0.01 * (b.get("total_usd") or 0))
    return any(o["usd"] >= lim for o in b["sells"] + b["buys"])


def tick(env=None, opener=None, model=None, now: int | None = None, sleep=time.sleep) -> str | None:
    """The window's spot check (every 5 minutes). Returns one Russian line when something happened (None = nothing to do)."""
    if not (enabled(env) and creds(env)):
        return None
    st = load_state(env)
    if not st.get("armed") or stopped(env):
        return None
    now = int(now or time.time() * 1000)
    pf = model if model is not None else al.fetch_portfolio()
    if not al.ideas(pf):
        return None
    why = due(st, pf, now, env)
    if not why and now - int(st.get("drift_t") or 0) >= DRIFT_EVERY_MS:
        st["drift_t"] = now
        save_state(st, env)
        cl = connect(env, opener, st, sleep)
        b = build(cl, env, pf)
        if drifted(b):
            why = "счёт разошёлся с портфелем"
        elif b["_ctx"]["active"] and now - int(st.get("lead_t") or 0) >= ld.DAY_MS:
            ld.sync(cl, b["_ctx"], b["_want"], st, now, log)           # the lead instruments at least once a day
            save_state(st, env)
    if not why:
        return None
    r = run_once(env, opener, pf, now, sleep, reason=why)
    if not r.get("ok"):
        return f"Автопилот OKX: {r.get('why')}"
    return (f"Автопилот OKX ({why}): продано {usd(r['sold_usd'])}, куплено {usd(r['bought_usd'])}, ордеров {r['orders']}"
            + (f", частично {r['partial']}" if r["partial"] else "") + (f", ошибок {r['failed']}" if r["failed"] else "")
            + (" · дневной лимит достигнут" if r["limit"] else "")
            + (f" · через USDC не завершено: {len(r['half'])} (USDC вернётся в USDT, монета — на следующем цикле)" if r.get("half") else ""))


def status(env=None) -> dict | None:
    """For the encrypted card (myspot_watch): on / off / stopped, the last rebalance, the split, futures and active spot. No network."""
    if not creds(env):
        return None
    st = load_state(env)
    last = st.get("last") or {}
    out = {"configured": True, "enabled": enabled(env), "armed": bool(st.get("armed")), "stopped": stopped(env),
           "live": live(env), "mode": "РЕАЛЬНЫЕ ДЕНЬГИ" if live(env) else "демо-торговля OKX",
           "last_run": st.get("last_run"), "last": {k: last.get(k) for k in ("reason", "sold_usd", "bought_usd", "orders", "partial",
                                                                             "failed", "skipped", "limit", "stopped")} if last else None,
           "split": al.shares(env), "same": st.get("same"), "diffs": st.get("diffs") or [], "targets": st.get("targets") or [],
           "rule": "окно автопилота на Маке: спот — как «CRYPTO NOVA портфель» (проверка каждые 5 минут), фьючерсы и активный спот — каждую минуту"}
    try:
        from . import okx_active, okx_futures
        out["futures"] = okx_futures.status(env)
        out["active"] = okx_active.status(env)
    except Exception:  # noqa: BLE001
        pass
    return out


def stop(env=None, opener=None) -> str:
    """Kill switch: the stop file first (no new orders from now on), then cancel open autopilot orders if any."""
    p = stop_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " стоп автопилота OKX\n")
    st = load_state(env)
    st["armed"] = False
    save_state(st, env)
    glob = lease.set_stop(env, True)      # 22.10: the stop reaches every machine (the Mac window and the server service)
    n = 0
    if creds(env):
        try:
            cl = connect(env, opener, st)
            for o in cl.pending():
                if str(o.get("clOrdId") or "").startswith("cnqap"):
                    try:
                        cl.cancel(o["instId"], o["ordId"])
                        n += 1
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
    log({"ev": "stop", "cancelled": n})
    fut = ""
    try:
        from . import okx_futures
        k = len(okx_futures.load_state(env).get("trades") or {})
        if k:
            fut = (f" Фьючерсных позиций открыто: {k} — они остаются со своими стоп-лоссами на OKX; закрыть их сейчас: "
                   "bash scripts/okx_autopilot_mac.sh fut-close.")
    except Exception:  # noqa: BLE001
        pass
    cancelled = f", отменено открытых ордеров: {n}" if n else ""
    where = (" Общий стоп записан: автопилот на сервере тоже остановлен." if glob else
             " Общий стоп НЕ записан (нет связи с GitHub или нет CNQ_GH_TOKEN) — если автопилот работает на сервере, "
             "остановите его там: bash scripts/okx_autopilot_server.sh stop.")
    return f"Автопилот OKX остановлен: новых ордеров не будет{cancelled}.{fut}{where} Включить снова: --resume, затем --run."


def resume(env=None) -> str:
    try:
        os.remove(stop_path(env))
    except FileNotFoundError:
        pass
    glob = lease.set_stop(env, False)
    return ("Стоп снят. Автопилот снова включится после следующего --run (с OKX_AUTOPILOT=on)."
            + ("" if glob else " Общий стоп снять не удалось (нет связи с GitHub или нет CNQ_GH_TOKEN) — повторите resume."))


def check(env=None, opener=None) -> list[str]:
    """Lines for `--check` and `mirror --check`: which key does what, host, permissions, money. Places nothing."""
    out = []
    if creds(env):
        ipv4_only(env)
        try:
            st = load_state(env)
            cl = connect(env, opener, st)
            save_state(st, env)
            cfg = cl.config()
            perm, ip = perm_of(cfg), str(cfg.get("ip") or "").strip()
            mode = ("РЕАЛЬНЫЕ ДЕНЬГИ" if live(env) else "демо (без ордеров на реальном счёте)")
            out.append(f"Автопилот OKX: ключ OKX_API_KEY работает · сервер {cl.host.replace('https://', '')} · "
                       f"{'OKX Europe (ЕЭЗ)' if is_eea(cl) else 'глобальный счёт OKX'} · это ключ {'ДЕМО-торговли' if cl.demo else 'РЕАЛЬНОГО счёта'}"
                       f" · режим окна: {mode}")
            bad = world_ok(cl, env)
            if bad:
                out.append("  " + bad)
            if "withdraw" in perm:
                out.append("!!! ВНИМАНИЕ: у ключа OKX_API_KEY есть право ВЫВОДА. Автопилот с ним работать НЕ будет. "
                           "Удали этот ключ на OKX и создай новый: «Чтение» + «Торговля», без «Вывода».")
            out.append("  права ключа: " + (", ".join(sorted(perm)) or "не прочитались")
                       + (" · торговать может" if "trade" in perm else " · НЕТ права «Торговля» — автопилот не сможет ставить ордера"))
            out.append("  привязка к IP: " + (ip if ip else "нет (лучше привязать ключ к IP этого Mac)"))
            q = pick_quote(cl, env)
            out.append(f"  валюта торговли: {q or 'не найдена'}{' (на OKX Europe USDT не торгуется)' if is_eea(cl) else ''}"
                       f" · комиссия {ms._p(round(taker_fee(cl, env) * 100, 2))}% за сделку"
                       + (f" · доступно {usd(cl.avail(q))[1:]} {q}" if q else ""))
            if q == "USDT" and usdt_account(cl, env):
                out.append(f"  монеты без пары с USDT: через {VIA} (USDT → {VIA} → монета и обратно, обе части — лимит IOC); "
                           f"свободные {VIA} от $5 автопилот возвращает в USDT в конце прохода")
            raw = ms.read_raw(cl.fetch, cl.host)
            rows = ms.assets(raw)
            tot = sum(r["usd"] for r in rows)
            outside = sorted(r["ccy"] for r in rows if set(r.get("where") or []) - {"торговый счёт"})
            out.append(f"  счёт ≈ {usd(tot)} · активов {len(rows)}"
                       + (f" · вне торгового счёта: {', '.join(outside)} (автопилот их не трогает)" if outside else ""))
            out.append("  деление: " + " · ".join(al.split_text(al.shares(env), tot)))
            from . import okx_futures
            out += okx_futures.check_lines(cl, cfg, env)
            out += ld.check_lines(cl, cfg, env)
            st = load_state(env)
            out.append("  автопилот: " + ("СТОП (--resume, чтобы снять)" if stopped(env) else
                                          "включён — ребалансирует сам" if st.get("armed") and enabled(env) else
                                          "выключен (OKX_AUTOPILOT=on и первый --run включат его)"))
            out.append("  " + lease.status_text(env))
        except Exception as e:  # noqa: BLE001
            out.append(describe_error(e))
    else:
        out.append("Автопилот OKX: ключа нет (строки OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE) — автопилот выключен.")
    msg = ms.check()
    out.append(msg if msg else "Мой портфель OKX: ключа «только чтение» нет (строки MYSPOT_*) — карточка в панели не обновляется.")
    return out


def describe_error(e: Exception) -> str:
    """The card's plain-words OKX errors, re-labelled for this key (OKX_API_*)."""
    s = ms.describe_error(e).replace("Мой портфель OKX:", "Автопилот OKX:").replace("MYSPOT_OKX_", "OKX_API_")
    return s.replace("пересоздай ключ «только чтение»", "пересоздай ключ «Чтение» + «Торговля» без «Вывода»")


def plan_all(env=None, opener=None, model=None, desks=None, act_desk=None, now: int | None = None) -> tuple[str, bool]:
    """--plan: the whole picture — split, every spot order with its reason, active spot, every futures order with size and stop,
    and «совпадает с системой: да / нет». Places nothing. Returns (text, orders would go out)."""
    from . import okx_active, okx_futures
    st = load_state(env)
    cl = connect(env, opener, st)
    save_state(st, env)
    cfg = cl.config()
    perm = perm_of(cfg)
    real_in_demo = not cl.demo and not live(env)          # the usual case: plan without --live, then run --live
    bad = ("У ключа есть право ВЫВОДА — с таким ключом автопилот работать не будет." if "withdraw" in perm else
           "У ключа нет права «Торговля»." if "trade" not in perm else None if real_in_demo else world_ok(cl, env))
    if stopped(env):
        bad = "включён стоп (--stop) — ордера не пойдут, пока не снят (--resume)"
    b = build(cl, env, model)
    mode = ("РЕАЛЬНЫЙ счёт (реальные деньги)" if not cl.demo else "ДЕМО-счёт OKX") + (
        " · ордера: НЕТ — " + bad if bad else " · ордера пойдут только после run --live и ответа ДА" if real_in_demo else " · ордера пойдут после run")
    L = [plan_text(b, mode), ""]
    L += okx_active.plan_lines(env, act_desk, now, b["total_usd"])
    L.append("")
    L += okx_futures.plan_lines(cl, cfg, ({**(os.environ if env is None else env), "CNQ_LIVE_TRADING": "yes"} if real_in_demo else env), desks, now)
    L += ["", same_text(b, okx_futures.enabled(env), okx_active.enabled(env))]
    return "\n".join(L), not bad


# ── a planned restart onto new code (22.10) ─────────────────────────────────────────────────────────────────────────────
def repo_head(root: str = ROOT) -> str | None:
    """The repository's HEAD commit, read from .git directly (no git binary: the service user does not own the checkout and git
    would refuse it as «dubious ownership»). None when it can not be read — then there is no self-update."""
    try:
        g = os.path.join(root, ".git")
        if os.path.isfile(g):                                   # a worktree: «gitdir: <path>»
            with open(g, encoding="utf-8") as f:
                line = f.read().strip()
            g = os.path.join(root, line[7:].strip()) if line.startswith("gitdir:") else g
        with open(os.path.join(g, "HEAD"), encoding="utf-8") as f:
            h = f.read().strip()
        if not h.startswith("ref:"):
            return h or None
        ref = h[4:].strip()
        if os.path.isfile(os.path.join(g, ref)):
            with open(os.path.join(g, ref), encoding="utf-8") as f:
                return f.read().strip() or None
        with open(os.path.join(g, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                x = line.split()
                if len(x) == 2 and x[1] == ref:
                    return x[0]
    except OSError:
        return None
    return None


def code_print(root: str = ROOT) -> str:
    """sha256 of every cnq/**/*.py (path + content). The bots' data commits (every few minutes) do not change it."""
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(os.path.join(root, "cnq")):
        dn[:] = sorted(d for d in dn if d != "__pycache__")
        for n in sorted(fn):
            if n.endswith(".py"):
                fp = os.path.join(dp, n)
                h.update(os.path.relpath(fp, root).encode())
                try:
                    with open(fp, "rb") as f:
                        h.update(f.read())
                except OSError:
                    h.update(b"?")
    return h.hexdigest()


def selfcheck() -> list[str]:
    """`--selfcheck`: the code on disk imports and its pure parts answer (no network, no keys, no lease, no orders). [] = OK."""
    errs = []
    try:
        from . import myspot_watch, okx_active, okx_futures, okx_lease, public_real  # noqa: F401 — the import itself is the check
        w = al.shares({})
        if abs(sum(w.values()) - 1) > 1e-6 or not 0 < limits({})["slip"] <= 0.02 or okx_futures.caps({})["lev"] > 3:
            errs.append("доли / лимиты / плечо не сходятся")
        for name, fn in (("run_once", run_once), ("tick", tick), ("route", route), ("sweep", sweep), ("futures.step", okx_futures.step),
                         ("active.step", okx_active.step), ("lease.gate", okx_lease.gate), ("Updater", Updater)):
            if not callable(fn):
                errs.append(f"нет {name}")
        if route_text({"via": VIA, "side": "buy", "ccy": "X"}) != "через USDC: USDT → USDC → X":
            errs.append("route_text")
        m = ld.merge([{"ccy": "X", "eq": "1", "cashBal": "1", "availBal": "1"}], {"X": {"qty": 0.5, "avail": 0.5, "insts": [], "subs": []}})
        if abs(float(m[0]["eq"]) - 1.5) > 1e-9 or abs(float(m[0]["availBal"]) - 1) > 1e-9 or ld.TD != "spot_isolated":
            errs.append("okx_lead.merge")
    except Exception as e:  # noqa: BLE001
        errs.append(f"{type(e).__name__}: {str(e)[:200]}")
    return errs


def _selfcheck_proc(root: str = ROOT) -> tuple[bool, str]:
    """The new code's --selfcheck in a FRESH process (this one keeps the old code in memory)."""
    import subprocess
    pp = os.environ.get("PYTHONPATH")
    env = {**os.environ, "PYTHONPATH": root + (os.pathsep + pp if pp else "")}
    try:
        r = subprocess.run([sys.executable, "-m", "cnq.execution.okx_autopilot", "--selfcheck"], env=env, capture_output=True,
                           text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "САМОПРОВЕРКА: OK" in out, out.strip()[-300:]


class Updater:
    """At a cycle boundary of --loop: the repository's HEAD moved (the server pulls main every 2 minutes) → the code fingerprint
    (cnq/**/*.py) differs from the one this process started on → the new code's --selfcheck in a fresh process → passed: the loop
    exits cleanly and systemd (Restart=always) / the Mac window's bash loop starts it on the new code; failed: the old code keeps
    running and that version is not tried again. Data-only commits never restart anything. OKX_SELF_UPDATE=off turns it off."""

    def __init__(self, root: str = ROOT, run=None, env=None):
        self.root, self.run, self.env = root, run or _selfcheck_proc, env
        self.head = repo_head(root)
        self.code = code_print(root) if self.head else None
        self.bad = None

    def check(self) -> tuple[bool, str | None]:
        """(restart now, a line for the window or None)."""
        if not self.head or (_env(self.env).get("OKX_SELF_UPDATE") or "on").strip().lower() in ("off", "0", "no", "false"):
            return False, None
        h = repo_head(self.root)
        if not h or h == self.head:
            return False, None
        prev, self.head = self.head, h
        c = code_print(self.root)
        if c in (self.code, self.bad):
            return False, None
        ok, out = self.run(self.root)
        if repo_head(self.root) != h or code_print(self.root) != c:
            self.head = prev
            return False, None                                    # moved again during the check — looked at next cycle
        if not ok:
            self.bad = c
            log({"ev": "self_update_refused", "head": h[:12], "out": out[-200:]})
            return False, f"новый код ({h[:7]}) не прошёл самопроверку — работаю на прежнем: {out[-160:]}"
        log({"ev": "self_update", "head": h[:12]})
        return True, f"новый код ({h[:7]}) прошёл самопроверку — перезапуск окна на нём (замок сохраняется)"


def main(argv=None) -> int:
    """22.10: a clean exit (Ctrl+C, a closed Terminal window = SIGHUP, systemctl stop = SIGTERM) gives the lease back at once; a
    planned restart onto new code (Updater) keeps it — the same holder id resumes right after the restart."""
    import signal

    def bye(_signum, _frame):
        raise SystemExit(0)
    for sig in ("SIGTERM", "SIGHUP"):
        try:
            signal.signal(getattr(signal, sig), bye)
        except (AttributeError, ValueError, OSError):
            pass
    try:
        return _main(argv)
    except KeyboardInterrupt:
        return 130
    finally:
        if lease._CACHE and not _PLANNED["restart"]:     # this process held the lease (kept on a planned restart)
            lease.release()


def _main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selfcheck" in argv:
        errs = selfcheck()
        print("САМОПРОВЕРКА: OK" if not errs else "САМОПРОВЕРКА: ОШИБКА — " + "; ".join(errs))
        return 0 if not errs else 1
    ipv4_only()
    if "--stop" in argv:
        print(stop())
        return 0
    if "--resume" in argv:
        print(resume())
        return 0
    if "--check" in argv:
        print("\n".join(check()))
        return 0
    if "--spot-lead-migrate" in argv:            # 22.10: the one-time move into lead positions — a plan; --live asks ДА
        try:
            if "--live" not in argv:
                print(ld.migrate())
                return 0
            env = {**os.environ, "CNQ_LIVE_TRADING": "yes"}

            def ask(text):
                print(text)
                return input("Это реальные деньги. Напишите ДА, чтобы выполнить: ").strip() == "ДА"
            print(ld.migrate(env, confirm=ask))
        except Exception as e:  # noqa: BLE001
            print(describe_error(e))
            return 1
        return 0
    if "--fut-close" in argv:
        from . import okx_futures
        try:
            print(okx_futures.close_all())
        except Exception as e:  # noqa: BLE001
            print(describe_error(e))
            return 1
        return 0
    if "--loop" in argv:
        from . import okx_active, okx_futures
        print(f"Автопилот OKX: окно запущено ({'РЕАЛЬНЫЕ ДЕНЬГИ' if live() else 'демо-торговля OKX'}). Фьючерсы и активный спот — каждую "
              "минуту, спот-портфель — каждые 5 минут. Остановить окно: Ctrl+C; остановить автопилот: --stop.", flush=True)
        last_spot, said, last_pub, pub_said = 0.0, None, 0.0, False
        upd = Updater()
        while True:
            # 22.10: on the server the service also publishes «Реальный счёт CRYPTO NOVA на OKX» (public_real.py) — the Mac mirror
            # does it while the autopilot runs on the Mac; public_real.writer() lets only the lease's machine write
            if lease.node_of() == "server" and time.time() - last_pub >= PUBLIC_EVERY_S:
                last_pub = time.time()
                try:
                    from . import public_real
                    if public_real.run() is not None and not pub_said:
                        pub_said = True
                        print(time.strftime("%H:%M ") + "Реальный счёт CRYPTO NOVA на OKX: web/public_real.json обновляется каждые 15 мин", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(time.strftime("%H:%M ") + "Реальный счёт OKX: " + describe_error(e).replace("Автопилот OKX: ", ""), flush=True)
            armed = load_state().get("armed") and enabled() and not stopped()
            # 22.10: the lease first (okx_lease.py) — another window or the server may be the one trading this account
            g = lease.gate() if armed else {"ok": False, "why": None}
            if armed and not g["ok"] and g["why"] != said:
                said = g["why"]
                print(time.strftime("%H:%M ") + "Замок: " + said, flush=True)
            elif armed and g["ok"] and said:
                said = None
                print(time.strftime("%H:%M ") + "Замок получен — это окно снова ведёт счёт.", flush=True)
            if armed and g["ok"]:
                for name, fn in (("Фьючерсы OKX", lambda: okx_futures.summary(okx_futures.step())),
                                 ("Активный спот OKX", lambda: (lambda r: (f"Активный спот OKX: куплено {r['bought']}, продано {r['sold']}"
                                                                           if r["bought"] or r["sold"] else None))(okx_active.step()))):
                    try:
                        msg = fn()
                        if msg:
                            print(time.strftime("%H:%M ") + msg, flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(time.strftime("%H:%M ") + name + ": " + describe_error(e).replace("Автопилот OKX: ", ""), flush=True)
                if time.time() - last_spot >= SPOT_EVERY_S:
                    last_spot = time.time()
                    try:
                        msg = tick()
                        if msg:
                            print(time.strftime("%H:%M ") + msg, flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(time.strftime("%H:%M ") + describe_error(e), flush=True)
            try:                                       # the cycle is over: a planned restart onto new code, if there is one
                restart, msg = upd.check()
            except Exception:  # noqa: BLE001
                restart, msg = False, None
            if msg:
                print(time.strftime("%H:%M ") + "Обновление: " + msg, flush=True)
            if restart:
                _PLANNED["restart"] = True
                return 0
            time.sleep(60)
    if "--plan" in argv or "--run" in argv:
        try:
            if "--run" in argv:
                from . import okx_active, okx_futures
                if stopped():
                    print("Включён стоп — ордера не ставлю. Снять: python3 -m cnq.execution.okx_autopilot --resume")
                    return 1
                r = run_once()
                if not r.get("ok"):
                    print("Автопилот OKX: " + r["why"])
                    return 1
                print(plan_text(r["plan"], r["mode"]))
                print(f"\nСпот готов ({r['mode']}): продано {usd(r['sold_usd'])}, куплено {usd(r['bought_usd'])}, ордеров {r['orders']}, "
                      f"частично {r['partial']}, не исполнено {r['skipped']}, ошибок {r['failed']}.")
                if r["limit"]:
                    print("Дневной лимит оборота достигнут — остальное автопилот сделает завтра.")
                if not load_state().get("armed"):
                    print("Фьючерсы и активный спот: позиции открывает и ведёт только окно loop, а автопилот не включён "
                          "(нужна строка OKX_AUTOPILOT=on в ~/.cryptonova.env) — сейчас не открываю.")
                else:
                    try:
                        a = okx_active.step(cl=r["cl"], total=r["total_usd"])
                        done = "куплено {}, продано {}".format(a["bought"], a["sold"]) if (a["bought"] or a["sold"]) else "ждёт сигнала «Импульс BTC»"
                        print("Активный спот: " + (a["why"] or done))
                    except Exception as e:  # noqa: BLE001
                        print("Активный спот: " + describe_error(e).replace("Автопилот OKX: ", ""))
                    try:
                        f = okx_futures.step(cl=r["cl"])
                        print("Фьючерсы: " + (f["why"] or (okx_futures.summary(f) or "новых сигналов нет — позиций не открыто")
                                              .replace("Фьючерсы OKX: ", "")))
                        for x in f["skipped"]:
                            print(f"  пропущен {x['id']}: {x['why']}")
                    except Exception as e:  # noqa: BLE001
                        print("Фьючерсы: " + describe_error(e).replace("Автопилот OKX: ", ""))
                print("Автопилот включён: дальше держи открытым окно loop — оно ведёт фьючерсы каждую минуту и спот по портфелю."
                      if load_state().get("armed") else "Автопилот НЕ включён для автоматической работы (нужна строка OKX_AUTOPILOT=on).")
                return 0
            text, go = plan_all()
            print(text)
            print("\nЭто только план, ордеров нет." + (" Выполнить: bash scripts/okx_autopilot_mac.sh run --live (спросит ДА)." if go else ""))
            return 0
        except Exception as e:  # noqa: BLE001
            print(describe_error(e))
            return 1
    print(__doc__.split("\n\n")[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
