"""Bybit v5 executor (USDT perpetuals) — the second venue of the executor.

Why: crypto prop firms such as HyroTrader run their evaluations on Bybit (demo) accounts connected through API keys,
so the same signal the panel shows can be placed there. Bots/automation on a prop account need the firm's written
approval — check their rules first.

Default host: Bybit DEMO trading (https://api-demo.bybit.com, keys from a Bybit demo account). Real money only with
CNQ_LIVE_TRADING=yes and keys of the live account. Signing: X-BAPI-SIGN = HMAC-SHA256(secret, ts + key + recv_window + body).
Stdlib only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.request

LIVE = "https://api.bybit.com"
DEMO = "https://api-demo.bybit.com"
RECV = "5000"


class BybitTrader:
    def __init__(self, api_key: str | None = None, secret: str | None = None, live: bool | None = None):
        self.key = api_key or os.environ.get("BYBIT_API_KEY", "")
        self.secret = (secret or os.environ.get("BYBIT_API_SECRET", "")).encode()
        self.live = (os.environ.get("CNQ_LIVE_TRADING", "no").lower() == "yes") if live is None else live
        self.base = LIVE if self.live else DEMO
        self.mode = "LIVE" if self.live else "BYBIT-DEMO"

    def sign(self, ts: str, body: str) -> str:
        return hmac.new(self.secret, (ts + self.key + RECV + body).encode(), hashlib.sha256).hexdigest()

    def _post(self, path: str, body: dict) -> dict:
        if not self.key or not self.secret:
            raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET not set")
        payload = json.dumps({k: v for k, v in body.items() if v is not None}, separators=(",", ":"))
        ts = str(int(time.time() * 1000))
        req = urllib.request.Request(self.base + path, data=payload.encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts,
                                              "X-BAPI-RECV-WINDOW": RECV, "X-BAPI-SIGN": self.sign(ts, payload)})
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode())
        if out.get("retCode") not in (0, 110043):   # 110043 = leverage not modified
            raise RuntimeError(f"Bybit {path}: {out.get('retCode')} {out.get('retMsg')}")
        return out.get("result", {})

    def _get(self, path: str, params: dict) -> dict:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        with urllib.request.urlopen(urllib.request.Request(f"{self.base}{path}?{q}"), timeout=15) as r:
            out = json.loads(r.read().decode())
        if out.get("retCode") != 0:
            raise RuntimeError(f"Bybit {path}: {out.get('retMsg')}")
        return out.get("result", {})

    def _get_signed(self, path: str, params: dict) -> dict:
        if not self.key or not self.secret:
            raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET not set")
        q = "&".join(f"{k}={v}" for k, v in params.items())
        ts = str(int(time.time() * 1000))
        req = urllib.request.Request(f"{self.base}{path}?{q}", headers={"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts,
                                                                        "X-BAPI-RECV-WINDOW": RECV, "X-BAPI-SIGN": self.sign(ts, q)})
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode())
        if out.get("retCode") != 0:
            raise RuntimeError(f"Bybit {path}: {out.get('retCode')} {out.get('retMsg')}")
        return out.get("result", {})

    def equity(self) -> float:
        res = self._get_signed("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        lst = res.get("list") or [{}]
        return float(lst[0].get("totalEquity") or 0)

    def positions(self) -> dict[str, dict]:
        res = self._get_signed("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        return {p["symbol"]: p for p in res.get("list", []) if float(p.get("size") or 0) > 0}

    def set_stop(self, symbol: str, stop: float) -> dict:
        tick = float(self.instrument(symbol).get("priceFilter", {}).get("tickSize", 0.01))
        return self._post("/v5/position/trading-stop", {"category": "linear", "symbol": symbol, "stopLoss": str(self._round_step(stop, tick)), "positionIdx": 0})

    def instrument(self, symbol: str) -> dict:
        res = self._get("/v5/market/instruments-info", {"category": "linear", "symbol": symbol})
        lst = res.get("list") or []
        if not lst:
            raise RuntimeError(f"unknown symbol {symbol}")
        return lst[0]

    @staticmethod
    def _round_step(x: float, step: float) -> float:
        if step <= 0:
            return x
        n = round(x / step)
        return round(n * step, 10)

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._post("/v5/position/set-leverage", {"category": "linear", "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)})

    def place(self, symbol: str, side: str, otype: str, qty: float, price: float | None = None, stop_loss: float | None = None,
              take_profit: float | None = None, reduce_only: bool = False, trigger_price: float | None = None, link_id: str | None = None) -> dict:
        body = {"category": "linear", "symbol": symbol, "side": side, "orderType": otype, "qty": str(qty), "timeInForce": "GTC",
                "price": str(price) if price is not None else None, "stopLoss": str(stop_loss) if stop_loss is not None else None,
                "takeProfit": str(take_profit) if take_profit is not None else None, "reduceOnly": True if reduce_only else None,
                "orderLinkId": link_id}
        if trigger_price is not None:   # conditional market order (used for take-profit legs)
            body.update({"triggerPrice": str(trigger_price), "triggerDirection": 1 if side == "Sell" else 2, "orderType": "Market"})
        return self._post("/v5/order/create", body)

    def execute_signal(self, sig: dict) -> dict:
        """Same contract as BingXTrader.execute_signal: entry (with attached stop-loss) + up to 3 reduce-only take-profit legs."""
        symbol = sig["symbol"].replace("-", "")
        long = sig["side"] == "LONG"
        info = self.instrument(symbol)
        lot = info.get("lotSizeFilter", {}); pf = info.get("priceFilter", {})
        qstep = float(lot.get("qtyStep", 0.001)); qmin = float(lot.get("minOrderQty", 0)); tick = float(pf.get("tickSize", 0.01))
        q = self._round_step(float(sig["qty"]), qstep)
        if q <= 0 or q < qmin:
            raise RuntimeError(f"quantity {q} below exchange minimum {qmin}")
        self.set_leverage(symbol, int(sig["leverage"]))
        entry_type = "Market" if sig.get("order", "LIMIT") == "MARKET" else "Limit"
        tag = f"cnq{int(time.time())}"
        entry = self.place(symbol, "Buy" if long else "Sell", entry_type, q,
                           price=None if entry_type == "Market" else self._round_step(float(sig["entry"]), tick),
                           stop_loss=self._round_step(float(sig["stop"]), tick), link_id=tag + "e")
        tps = []
        parts = sig.get("parts", [0.4, 0.3, 0.3]); remaining = q
        for i, (tp, part) in enumerate(zip(sig["targets"], parts)):
            qty_i = self._round_step(q * part, qstep) if i < len(parts) - 1 else self._round_step(remaining, qstep)
            remaining = round(remaining - qty_i, 10)
            if qty_i <= 0:
                continue
            tps.append(self.place(symbol, "Sell" if long else "Buy", "Market", qty_i, reduce_only=True,
                                  trigger_price=self._round_step(float(tp), tick), link_id=f"{tag}t{i}"))
        return {"ok": True, "mode": self.mode, "venue": "bybit", "symbol": symbol, "entry": entry, "take_profits": tps, "stop": sig["stop"]}
