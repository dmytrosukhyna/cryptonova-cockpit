"""22.11: is our OKX lead-trader profile live? OKX's PUBLIC copy-trading API (no keys) for the code the server publishes in
web/public_real.json, plus the profile page variants. Prints only; changes nothing."""
import json, sys, urllib.request, urllib.error

CODE = sys.argv[1] if len(sys.argv) > 1 else json.load(open("web/public_real.json"))["copy_url"].rsplit("/", 1)[-1]
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.status, r.geturl(), r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, e.read(600).decode("utf-8", "replace")
    except Exception as e:
        return None, url, repr(e)


print("code", CODE, "len", len(CODE), "hex16" if len(CODE) == 16 else "NOT the usual 16-hex uniqueCode")
api = "https://www.okx.com/api/v5/copytrading/"
for it in ("SPOT", "SWAP"):
    for p in ("public-stats?lastDays=1", "public-preference-currency?", "public-current-subpositions?limit=5", "public-pnl?lastDays=1"):
        s, _, b = get(f"{api}{p}&instType={it}&uniqueCode={CODE}")
        print(it, p.split("?")[0], s, b[:300].replace("\n", " "))
# is the code in OKX's public list of lead traders?
for it in ("SPOT", "SWAP"):
    found, total = False, 0
    for page in range(1, 80):
        s, _, b = get(f"{api}public-lead-traders?instType={it}&sortType=overview&limit=20&page={page}")
        try:
            d = json.loads(b)["data"][0]
        except Exception:
            print(it, "list page", page, s, b[:200]); break
        ranks = d.get("ranks") or []
        total += len(ranks)
        if any(r.get("uniqueCode") == CODE for r in ranks):
            found = True; break
        if page >= int(d.get("totalPage") or 0):
            break
    print(it, "public list: found" if found else "public list: NOT found", "scanned", total)
for path in (f"copy-trading/account/{CODE}", f"copy-trading/account/{CODE}?tab=spot", f"copy-trading/account/{CODE}?tab=swap",
             f"en-eu/copy-trading/account/{CODE}", f"ua/copy-trading/account/{CODE}"):
    s, final, b = get("https://www.okx.com/" + path)
    t = b.split("<title>", 1)[1].split("</title>", 1)[0][:120] if "<title>" in b else ""
    print("page", path, s, final, "|", t, "| 404" if "404" in b[:4000] or "not found" in b.lower()[:4000] else "")
