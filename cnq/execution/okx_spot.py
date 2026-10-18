"""OKX spot account for «Инвестировать» (18.10.2026, owner: «переключим мои живые деньги на этот спотовый режим» — his own coins
are on OKX). Same interface as spot_mirror.SpotAccount (holdings / instrument / price / market), so spot_mirror.plan() and
step() work unchanged:  python -m cnq.execution.spot_mirror --exchange okx [--portfolio] [--keep SOL,LINK] [--run]

Safety: OKX DEMO by default (header x-simulated-trading: 1 — needs keys made in OKX «Демо-торговля»); the real account only
with CNQ_LIVE_TRADING=yes. Keys from the environment on the owner's Mac (OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE),
trade permission only — never withdrawal; they never leave the machine. Without --run only the plan is printed.
Signing (OKX v5): OK-ACCESS-SIGN = base64(HMAC-SHA256(secret, timestamp + METHOD + requestPath + body)). Stdlib only."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.request

BASE = "https://www.okx.com"


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

    def sign(self, ts: str, method: str, path: str, body: str) -> str:
        return base64.b64encode(hmac.new(self.secret, (ts + method + path + body).encode(), hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, body: dict | None = None, signed: bool = False) -> list:
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "cnq-okx/1.0"}
        if not self.live:
            headers["x-simulated-trading"] = "1"
        if signed:
            if not (self.key and self.secret and self.passphrase):
                raise RuntimeError("OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE not set")
            ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.datetime.now(dt.timezone.utc).microsecond // 1000:03d}Z"
            headers.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": self.sign(ts, method, path, payload),
                            "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
        req = urllib.request.Request(BASE + path, data=payload.encode() if payload else None, method=method, headers=headers)
        with self._open(req) as r:
            out = json.loads(r.read().decode())
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
