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
# build 47 (§13.8): the support links the app and the site really use, checked like the rest — no link is invented here.
# The app's own (ui/src/commonMain/kotlin/com/cryptonova/ui/v2/Links.kt of the app repo, not in this checkout): SUPPORT and
# SUPPORT_PAGE; the site's (web/*.html) are read from the pages themselves. Telegram answers every t.me/<name> with HTTP 200, so a
# t.me page is judged by its content (tg()), with three controls: a channel, a bot and a name nobody has.
APP_SUPPORT = (("app V2Links.SUPPORT (Telegram)", "https://t.me/crypto_nova_support"),
               ("app V2Links.SUPPORT_PAGE", "https://cryptonova.pro/support.html"))
SITE_PAGES = ("support.html", "terms.html", "privacy.html", "concept.html", "index.html")
TG_CONTROLS = (("control: channel @telegram", "https://t.me/telegram"), ("control: bot @BotFather", "https://t.me/BotFather"),
               ("control: a name nobody has", "https://t.me/cnq_no_such_name_7q3x9z"))
TG_LINK = re.compile(r"https://t\.me/([A-Za-z0-9_]{5,32})(?![A-Za-z0-9_/])")


def support_links(root: str = ".") -> list[tuple[str, str]]:
    out = list(APP_SUPPORT)
    seen = {u for _n, u in out}
    for page in SITE_PAGES:
        try:
            html = open(os.path.join(root, "web", page), encoding="utf-8").read()
        except OSError:
            continue
        for m in TG_LINK.finditer(html):
            u = m.group(0)
            if u not in seen:
                seen.add(u)
                out.append((f"web/{page} Telegram", u))
    return out + list(TG_CONTROLS)


def tg(body: str) -> dict:
    """what a t.me page says: the name shown (tgme_page_title), the line under it (tgme_page_extra: @bot / subscribers / members),
    the button; no title = Telegram's empty «contact @name» page = no such username"""
    def grab(rx):
        m = re.search(rx, body, re.I | re.S)
        return re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip()[:80] if m else ""
    title = grab(r'class="tgme_page_title"[^>]*>(.*?)</div>')
    extra = grab(r'class="tgme_page_extra"[^>]*>(.*?)</div>')
    button = grab(r'class="tgme_action_button_new[^"]*"[^>]*>(.*?)</a>')
    kind = ("bot" if "bot" in (button + " " + extra).lower() else "channel" if re.search(r"subscriber|member|online", extra, re.I)
            else "user / group") if title else None
    return {"exists": bool(title), "tg_title": title, "tg_extra": extra, "tg_button": button, "tg_kind": kind}


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
    out = {"code": code, "final": final[:160], "title": title, "verdict": v}
    if h1 in ("t.me", "telegram.me") and code < 400:
        out.update(tg(body))
        out["verdict"] = (f"exists: {out['tg_kind']} «{out['tg_title']}» {out['tg_extra']} [{out['tg_button']}]" if out["exists"]
                          else "NO SUCH USERNAME (Telegram's empty contact page)")
    return out


def main() -> int:
    """GitHub shows at most 10 notices per step: the results go out in groups of 3 (and in full to the job summary)"""
    api = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for name, url in links(".", api) + support_links("."):
        r = check(url) if not url.startswith("ERROR") else {"verdict": url}
        rows.append(f"{name}: {url or '—'} → {r.get('code', '')} {r.get('final', '')} · «{r.get('title', '')}» · {r['verdict']}")
    for i in range(0, len(rows), 3):
        msg = " ⏎ ".join(x[:600] for x in rows[i:i + 3])
        print(f"::notice title=links {i + 1}–{min(i + 3, len(rows))}::{msg.replace('%', '%25')}", flush=True)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write("\n".join("- " + x for x in rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
