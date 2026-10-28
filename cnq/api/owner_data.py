"""The owner's private data for the closed panel, served by the API after the owner signs in (28.10.2026, owner: «для доступа к
отдельным разделам запрашиваются непонятные дополнительные пароли»).

How the data is made (unchanged):
  • «Мой портфель · OKX» — the Mac mirror (scripts/mirror_mac.sh → cnq/execution/myspot_watch.py) reads the owner's OKX account with
    a read-only key every 15 min, encrypts the snapshot with MYSPOT_PASS and publishes web/myspot.enc.json (public repo);
  • «Иван тест» — the same mirror (cnq/execution/ivan_watch.py) reads the Bybit account of Ivan's platform read-only, encrypts with
    IVAN_PASS and publishes web/ivan.enc.json.
  The server's engine pulls the repo every 2 minutes, so <run>/web has the same encrypted files.

What is new: the server keeps its own copy of the two passphrases in /etc/cnq.env (MYSPOT_PASS, IVAN_PASS — the same strings as in
~/.cryptonova.env on the Mac) and decrypts the files in memory for a signed-in owner (GET /v2/owner/myspot, /v2/owner/ivan,
/v2/owner/overview — cnq/api/server.py). The panel then never asks for these passphrases. Nothing decrypted is written to disk,
logged or committed; the public repo keeps only the encrypted blobs, exactly as before, and the old in-browser decryption in
web/app.html keeps working as a fallback.

Read-only: nothing here reads exchange keys or touches an account."""
from __future__ import annotations

import json
import os
import threading
import time

from cnq.execution import ivan_watch

SOURCES = {
    "myspot": {"file": "myspot.enc.json", "env": "MYSPOT_PASS", "title": "Мой портфель · OKX"},
    "ivan": {"file": "ivan.enc.json", "env": "IVAN_PASS", "title": "Иван тест"},
}


class OwnerData:
    def __init__(self, web_dir: str, env=None):
        self.web = web_dir
        self.env = env                       # tests pass a dict; the server reads os.environ at call time
        self.cache: dict[str, tuple] = {}    # name → ((mtime, size, pass_fingerprint), data)
        self.lock = threading.Lock()

    def _pass(self, name: str) -> str:
        env = os.environ if self.env is None else self.env
        return env.get(SOURCES[name]["env"]) or ""

    def configured(self) -> dict:
        return {name: bool(self._pass(name)) for name in SOURCES}

    def get(self, name: str) -> tuple[int, dict]:
        """→ (http code, body). 200 {"name", "title", "asof", "age_sec", "data"} · 503 {"error": "not_configured" | "no_data"} ·
        502 {"error": "bad_passphrase" | "bad_file"}"""
        src = SOURCES.get(name)
        if not src:
            return 404, {"error": "not_found"}
        pw = self._pass(name)
        if not pw:
            return 503, {"error": "not_configured", "detail": f"{src['env']} is not set on the server"}
        path = os.path.join(self.web, src["file"])
        try:
            st = os.stat(path)
        except OSError:
            return 503, {"error": "no_data", "detail": f"web/{src['file']} is missing on the server"}
        key = (st.st_mtime_ns, st.st_size, hash(pw))
        with self.lock:
            hit = self.cache.get(name)
            if hit and hit[0] == key:
                data, asof = hit[1]
            else:
                try:
                    with open(path, encoding="utf-8") as f:
                        blob = json.load(f)
                    data = ivan_watch.decrypt(blob, pw)
                    asof = blob.get("asof") if isinstance(blob, dict) else None
                except ivan_watch.BadPassword:
                    return 502, {"error": "bad_passphrase", "detail": f"{src['env']} on the server does not open web/{src['file']}"}
                except (ValueError, KeyError, TypeError, OSError):
                    return 502, {"error": "bad_file"}
                self.cache[name] = (key, (data, asof))
        asof = (data.get("asof") if isinstance(data, dict) else None) or asof
        age = round(time.time() - asof / 1000) if isinstance(asof, (int, float)) and asof > 0 else None
        return 200, {"name": name, "title": src["title"], "asof": asof, "age_sec": age, "data": data}

    # ------------------------------------------------------------------------------------------------ the overview headline
    def summary(self) -> dict:
        """Headline numbers of each private section for the owner's overview — only numbers that are in the data; a section that
        cannot be read says why."""
        out = {}
        for name in SOURCES:
            code, d = self.get(name)
            if code != 200:
                out[name] = {"ok": False, "error": d.get("error"), "title": SOURCES[name]["title"]}
                continue
            x = d["data"] if isinstance(d["data"], dict) else {}
            row = {"ok": True, "title": d["title"], "asof": d["asof"], "age_sec": d["age_sec"]}
            if name == "myspot":
                plan = x.get("plan") if isinstance(x.get("plan"), dict) else {}
                row.update(total_usd=plan.get("total_usd"), assets=len(x.get("assets") or []),
                           autopilot=(x.get("autopilot") or {}).get("state") if isinstance(x.get("autopilot"), dict) else None,
                           sub=bool(x.get("sub")))
            else:
                s = x.get("stats") if isinstance(x.get("stats"), dict) else {}
                # «100 % · 3 сделки»: the win rate is always shown with its sample size (closed trades only — open losers are not in it)
                row.update(closed=s.get("n"), wins=s.get("wins"), losses=s.get("losses"), winrate=s.get("winrate"),
                           pnl_sum=s.get("pnl_sum"), open=len(x.get("positions") or []), equity=x.get("equity"),
                           max_dd=x.get("max_dd"), worst_open=x.get("worst_open"), host=x.get("host"),
                           test=True)          # a strategy under test, watched read-only — never the CRYPTO NOVA portfolio
            out[name] = row
        return out
