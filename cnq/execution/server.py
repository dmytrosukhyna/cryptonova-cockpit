"""Tiny executor HTTP server for the Live panel's «Исполнить» button (stdlib only).

  CNQ_EXEC_KEY=secret BINGX_API_KEY=... BINGX_API_SECRET=... python -m cnq.execution.server [port]

POST /execute  {symbol, side, entry, stop, targets, parts, qty, leverage, order}  header X-CNQ-Key
→ {ok, mode, ...}. CORS is open so the panel can call it from any origin (the key is the guard).
Every request and result is appended to data/executions.jsonl. Demo (VST) unless CNQ_LIVE_TRADING=yes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from .bingx_trade import BingXTrader

KEY = os.environ.get("CNQ_EXEC_KEY", "")
LOG = "data/executions.jsonl"


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CNQ-Key")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self._cors(); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path != "/execute":
            return self._json(404, {"ok": False, "error": "not found"})
        if KEY and self.headers.get("X-CNQ-Key", "") != KEY:
            return self._json(401, {"ok": False, "error": "bad key"})
        try:
            sig = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or b"{}"))
            for k in ("symbol", "side", "entry", "stop", "targets", "qty", "leverage"):
                if k not in sig:
                    raise ValueError(f"missing {k}")
            res = BingXTrader().execute_signal(sig)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": str(e)}
        os.makedirs("data", exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"time": int(time.time() * 1000), "signal": sig if "sig" in dir() else None, "result": res}, ensure_ascii=False, default=str) + "\n")
        self._json(200 if res.get("ok") else 400, res)

    def log_message(self, *a):  # quieter
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    mode = "LIVE" if os.environ.get("CNQ_LIVE_TRADING", "no").lower() == "yes" else "VST-DEMO"
    print(f"executor on :{port}  mode={mode}  key={'set' if KEY else 'NONE (open!)'}")
    HTTPServer(("0.0.0.0", port), H).serve_forever()
