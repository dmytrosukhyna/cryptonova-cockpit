"""BingX USDT-M perpetual order execution (stdlib only).

Default environment: VST demo (https://open-api-vst.bingx.com) — no real money.
Real trading needs BOTH env CNQ_LIVE_TRADING=yes AND a key created for the live domain.

Signing per official reference: sort params ASCII, join k=v&…, HMAC-SHA256 hex,
append &signature=; headers X-BX-APIKEY and X-SOURCE-KEY.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

LIVE = "https://open-api.bingx.com"
VST = "https://open-api-vst.bingx.com"


class BingXTrader:
    def __init__(self, api_key: str | None = None, secret: str | None = None, live: bool | None = None):
        self.key = api_key or os.environ.get("BINGX_API_KEY", "")
        self.secret = (secret or os.environ.get("BINGX_API_SECRET", "")).encode()
        self.live = (os.environ.get("CNQ_LIVE_TRADING", "no").lower() == "yes") if live is None else live
        self.base = LIVE if self.live else VST
        self.mode = "LIVE" if self.live else "VST-DEMO"

    # ------------------------------------------------------------ transport
    def _signed(self, method: str, path: str, params: dict) -> dict:
        p = {k: v for k, v in params.items() if v is not None}
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 5000
        qs = "&".join(f"{k}={p[k]}" for k in sorted(p))
        sig = hmac.new(self.secret, qs.encode(), hashlib.sha256).hexdigest()
        enc = "&".join(f"{k}={urllib.parse.quote(str(p[k]), safe='')}" if any(ch in str(p[k]) for ch in "[{") else f"{k}={p[k]}" for k in sorted(p))
        url = f"{self.base}{path}?{enc}&signature={sig}"
        req = urllib.request.Request(url, method=method, headers={"X-BX-APIKEY": self.key, "X-SOURCE-KEY": "BX-AI-SKILL"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode())
        if body.get("code", 0) != 0:
            raise RuntimeError(f"BingX {path}: {body.get('code')} {body.get('msg')}")
        return body.get("data", {})

    # ------------------------------------------------------------ actions
    def contract(self, symbol: str) -> dict:
        with urllib.request.urlopen(urllib.request.Request(f"{self.base}/openApi/swap/v2/quote/contracts?symbol={symbol}",
                                                           headers={"X-SOURCE-KEY": "BX-AI-SKILL"}), timeout=15) as r:
            data = json.loads(r.read().decode()).get("data", [])
        return data[0] if data else {}

    def set_leverage(self, symbol: str, side: str, leverage: int) -> dict:
        return self._signed("POST", "/openApi/swap/v2/trade/leverage", {"symbol": symbol, "side": side, "leverage": int(leverage)})

    def place(self, symbol: str, side: str, position_side: str, otype: str, quantity: float, price: float | None = None,
              stop_price: float | None = None, stop_loss: dict | None = None, take_profit: dict | None = None,
              client_id: str | None = None, reduce_only: bool | None = None) -> dict:
        params = {"symbol": symbol, "side": side, "positionSide": position_side, "type": otype, "quantity": quantity,
                  "price": price, "stopPrice": stop_price, "clientOrderId": client_id,
                  "timeInForce": "GTC" if otype == "LIMIT" else None,
                  "stopLoss": json.dumps(stop_loss, separators=(",", ":")) if stop_loss else None,
                  "takeProfit": json.dumps(take_profit, separators=(",", ":")) if take_profit else None}
        return self._signed("POST", "/openApi/swap/v2/trade/order", params)

    def execute_signal(self, sig: dict) -> dict:
        """sig: {symbol 'BTCUSDT'|'BTC-USDT', side LONG|SHORT, entry, stop, targets[3], parts[3], qty, leverage, order MARKET|LIMIT}
        Places: entry order with attached stop-loss (whole size) + 3 reduce-only take-profit orders."""
        symbol = sig["symbol"].replace("USDT", "-USDT") if "-" not in sig["symbol"] else sig["symbol"]
        long = sig["side"] == "LONG"
        spec = self.contract(symbol)
        qprec = int(spec.get("quantityPrecision", 3)); pprec = int(spec.get("pricePrecision", 2))
        q = round(float(sig["qty"]), qprec)
        if q <= 0 or q < float(spec.get("tradeMinQuantity", 0) or 0):
            raise RuntimeError(f"quantity {q} below exchange minimum {spec.get('tradeMinQuantity')}")
        self.set_leverage(symbol, "LONG" if long else "SHORT", int(sig["leverage"]))
        entry_type = "MARKET" if sig.get("order", "LIMIT") == "MARKET" else "LIMIT"
        sl = {"type": "STOP_MARKET", "stopPrice": round(float(sig["stop"]), pprec), "workingType": "MARK_PRICE"}
        tag = f"cnq{int(time.time())}"
        entry = self.place(symbol, "BUY" if long else "SELL", "LONG" if long else "SHORT", entry_type, q,
                           price=None if entry_type == "MARKET" else round(float(sig["entry"]), pprec), stop_loss=sl, client_id=tag + "e")
        tps = []
        parts = sig.get("parts", [0.4, 0.3, 0.3]); remaining = q
        for i, (tp, part) in enumerate(zip(sig["targets"], parts)):
            qty_i = round(q * part, qprec) if i < len(parts) - 1 else round(remaining, qprec)
            remaining = round(remaining - qty_i, qprec)
            if qty_i <= 0:
                continue
            tps.append(self.place(symbol, "SELL" if long else "BUY", "LONG" if long else "SHORT", "TAKE_PROFIT_MARKET", qty_i,
                                  stop_price=round(float(tp), pprec)))
        return {"ok": True, "mode": self.mode, "symbol": symbol, "entry": entry, "take_profits": tps, "stop": sl}
