"""Links audit (28.10.2026, build 46 §16): every link the server gives the app — web/app_config.json (exchange referral and
«Подключиться» pages, app-store pages), the lead trader's OKX profile (web/public_real.json copy_url) and the news links of
GET /v2/home — opened as a browser would (redirects followed), from wherever this runs. Prints, per link: HTTP status, the final
address after redirects, the page title, and a verdict (ok / redirected elsewhere / region page / error). Changes nothing.
Run by .github/workflows/links-audit.yml (a GitHub runner is in the USA: OKX and BingX restrict US visitors, so their answers
there are the US ones — the workflow says so). Stdlib only.  usage: python scripts/links_audit.py [api_base]"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 "
      "Safari/604.1")
CFG_KEYS = ("exchange_ref", "okx_ref", "okx_copy_url", "bingx_copy_url", "okx_signal_url", "okx_app_ios", "okx_app_android", "okx_kyc_url",
            "okx_deposit_url", "bingx_app_ios", "bingx_app_android", "bingx_kyc_url", "bingx_deposit_url")
REGION_WORDS = re.compile(r"not available in your (country|region)|restricted (jurisdiction|region)|access denied|unavailable in your",
                          re.I)


def links(root: str = ".", api: str | None = None) -> list[tuple[str, str]]:
    out = []
    cfg = json.load(open(os.path.join(root, "web", "app_config.json"), encoding="utf-8"))
    for k in CFG_KEYS:
        v = cfg.get(k)
        out.append((f"app_config.{k}", v if isinstance(v, str) else ""))
    tr = cfg.get("exchange_trade_url")
    if isinstance(tr, str) and tr:
        out.append(("app_config.exchange_trade_url (BTC)", tr.replace("{SYM}", "BTC")))
    try:
        pr = json.load(open(os.path.join(root, "web", "public_real.json"), encoding="utf-8"))
        out.append(("public_real.copy_url", pr.get("copy_url") or ""))
    except (OSError, ValueError):
        out.append(("public_real.copy_url", ""))
    if api:
        for lang in ("ru", "en"):
            try:
                with urllib.request.urlopen(urllib.request.Request(f"{api}/v2/home?lang={lang}", headers={"User-Agent": UA}), timeout=20) as r:
                    home = json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001
                out.append((f"/v2/home?lang={lang}", f"ERROR {e}"))
                continue
            for i, n in enumerate(home.get("news") or []):
                out.append((f"news.{lang}[{i}] {str(n.get('source') or '')[:20]}", n.get("link") or ""))
    return out


def check(url: str, timeout: int = 20) -> dict:
    if not url:
        return {"verdict": "empty (the app uses its own default)"}
    if not url.startswith("https://"):
        return {"verdict": "not https"}
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en,ru;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            code, final = r.status, r.geturl()
    except urllib.error.HTTPError as e:
        body = e.read(50_000).decode("utf-8", "replace") if e.fp else ""
        code, final = e.code, e.geturl()
    except Exception as e:  # noqa: BLE001
        return {"verdict": f"error: {type(e).__name__}: {str(e)[:120]}"}
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip()[:90] if m else ""
    h0, h1 = urllib.parse.urlsplit(url).netloc, urllib.parse.urlsplit(final).netloc
    v = "ok"
    if code >= 400:
        v = f"HTTP {code}"
    elif REGION_WORDS.search(body[:200_000]):
        v = "region / restricted page"
    elif h0.split(".")[-2:] != h1.split(".")[-2:]:
        v = f"redirected to another site ({h1})"
    return {"code": code, "final": final[:160], "title": title, "verdict": v}


def main() -> int:
    api = sys.argv[1] if len(sys.argv) > 1 else None
    for name, url in links(".", api):
        r = check(url) if not url.startswith("ERROR") else {"verdict": url}
        msg = f"{url or '—'} → {r.get('code', '')} {r.get('final', '')} · «{r.get('title', '')}» · {r['verdict']}"
        print(f"::notice title={name}::{msg.replace('%', '%25')[:900]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
