"""OKX spot account for «Инвестировать» (18.10.2026, owner: «переключим мои живые деньги на этот спотовый режим» — his own coins
are on OKX). Same interface as spot_mirror.SpotAccount (holdings / instrument / price / market), so spot_mirror.plan() and
step() work unchanged:  python -m cnq.execution.spot_mirror --exchange okx [--portfolio] [--keep SOL,LINK] [--run]

Safety: OKX DEMO by default (header x-simulated-trading: 1 — needs keys made in OKX «Демо-торговля»); the real account only
with CNQ_LIVE_TRADING=yes. Keys from the environment on the owner's Mac (OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE),
trade permission only — never withdrawal; they never leave the machine. Without --run only the plan is printed.
Signing (OKX v5): OK-ACCESS-SIGN = base64(HMAC-SHA256(secret, timestamp + METHOD + requestPath + body)). Stdlib only.
22.10: the host is found by itself — www.okx.com, then eea.okx.com, then my.okx.com (an OKX Europe key fails on www with 60032);
OKX_HOST forces one. The owner's REAL account is run by cnq/execution/okx_autopilot.py (spot_mirror refuses --run live on OKX)."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

BASE = "https://www.okx.com"
HOSTS = ["https://www.okx.com", "https://eea.okx.com", "https://my.okx.com"]
WRONG_HOST = {"50101", "50111", "50119", "50114", "60032"}   # «key not here» — try the next host (60032: OKX Europe key on www)


class OkxSpot:
    def __init__(self, key: str | None = None, secret: str | None = None, passphrase: str | None = None, live: bool | None = None,
                 opener=None):
        self.key = key or os.environ.get("OKX_API_KEY", "")
        self.secret = (secret or os.environ.get("OKX_API_SECRET", "")).encode()
        self.passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
        self.live = (os.environ.get("CNQ_LIVE_TRADING", "no").lower() == "yes") if live is None else live
        self.mode = "LIVE" if self.live else "OKX-DEMO"
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=15))
        self._inst: dict = {}
        forced = (os.environ.get("OKX_HOST") or "").strip().rstrip("/")
        self.base = (forced if forced.startswith("http") else "https://" + forced) if forced else None
        self._fixed = bool(forced)
        self._found = False

    def _send(self, req) -> dict:
        try:
            with self._open(req) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:          # OKX answers a wrong host / bad key with HTTP 401 and a JSON body
            try:
                return json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                return {"code": str(-e.code), "msg": f"HTTP {e.code}", "data": []}

    def sign(self, ts: str, method: str, path: str, body: str) -> str:
        return base64.b64encode(hmac.new(self.secret, (ts + method + path + body).encode(), hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, body: dict | None = None, signed: bool = False) -> list:
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "cnq-okx/1.0"}
        if not self.live:
            headers["x-simulated-trading"] = "1"
        if signed and not (self.key and self.secret and self.passphrase):
            raise RuntimeError("OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE not set")
        # signed calls look for the host where the key works (remembered); public calls use that host once it is known
        hosts = [self.base] if (self.base and (self._fixed or not signed or self._found)) else ([self.base] if self.base else []) + [h for h in HOSTS if h != self.base]
        out = {}
        for i, host in enumerate(hosts):
            h = dict(headers)
            if signed:
                ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.datetime.now(dt.timezone.utc).microsecond // 1000:03d}Z"
                h.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": self.sign(ts, method, path, payload),
                          "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
            req = urllib.request.Request(host + path, data=payload.encode() if payload else None, method=method, headers=h)
            out = self._send(req)
            if signed and str(out.get("code")) in WRONG_HOST and i < len(hosts) - 1 and not self._fixed and method == "GET":
                continue                                   # only a READ is tried on the next host — an order never is
            if signed and str(out.get("code")) == "0":
                self.base, self._found = host, True
            break
        if str(out.get("code")) != "0":
            raise RuntimeError(f"OKX {path}: {out.get('code')} {out.get('msg')} {json.dumps(out.get('data'))[:200]}")
        return out.get("data") or []

    # ---- the SpotAccount interface ----
    def holdings(self) -> tuple[float, dict]:
        d = self._req("GET", "/api/v5/account/balance", signed=True)
        details = (d[0].get("details") if d else None) or []
        usdt, held = 0.0, {}
        for c in details:
            ccy = c.get("ccy")
            q = float(c.get("cashBal") or c.get("eq") or 0)
            usd = float(c.get("eqUsd") or 0)
            if ccy == "USDT":
                usdt = q
            elif q > 0 and usd >= 1:
                held[ccy] = {"qty": q, "usd": usd}
        return usdt, held

    def instrument(self, coin: str) -> dict | None:
        inst = f"{coin}-USDT"
        if inst not in self._inst:
            try:
                d = self._req("GET", f"/api/v5/public/instruments?instType=SPOT&instId={inst}")
            except Exception:  # noqa: BLE001
                d = []
            x = d[0] if d and d[0].get("state") == "live" else None
            # spot_mirror.plan() reads the base-coin step from lotSizeFilter.basePrecision (Bybit naming)
            self._inst[inst] = ({**x, "lotSizeFilter": {"basePrecision": x.get("lotSz")}} if x else None)
        return self._inst[inst]

    def price(self, coin: str) -> float | None:
        try:
            d = self._req("GET", f"/api/v5/market/ticker?instId={coin}-USDT")
            return float(d[0]["last"]) if d else None
        except Exception:  # noqa: BLE001
            return None

    def market(self, coin: str, side: str, qty: float, by_quote: bool) -> dict:
        body = {"instId": f"{coin}-USDT", "tdMode": "cash", "side": side.lower(), "ordType": "market",
                "sz": f"{qty:.8f}".rstrip("0").rstrip("."), "tgtCcy": "quote_ccy" if by_quote else "base_ccy"}
        d = self._req("POST", "/api/v5/trade/order", body, signed=True)
        o = d[0] if d else {}
        if str(o.get("sCode", "0")) != "0":
            raise RuntimeError(f"OKX order {coin}: {o.get('sCode')} {o.get('sMsg')}")
        return {"orderId": o.get("ordId")}
