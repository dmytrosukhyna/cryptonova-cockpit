"""22.11: the profile page's own data for our code vs a known public lead trader (which shows), to see why the page says not found."""
import json, re, sys, urllib.request, urllib.error
OURS = "899209660480528889"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "Accept-Language": "en"}
def get(url, n=600000):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.status, r.read(n).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")
    except Exception as e:
        return None, repr(e)
s, b = get("https://www.okx.com/api/v5/copytrading/public-lead-traders?instType=SWAP&sortType=overview&limit=3")
other = json.loads(b)["data"][0]["ranks"][0]["uniqueCode"]
print("known public lead trader", other)
for code in (OURS, other):
    s, html = get(f"https://www.okx.com/copy-trading/account/{code}?tab=swap")
    print("==", code, s, len(html))
    for k in ("notFound", "not-found", "404", "pageNotFound", "leaderNotExist", "isLeader", "leadTrader", "nickName", "copyState"):
        i = html.find(k)
        print("  ", k, i, html[max(0, i - 80):i + 120].replace("\n", " ") if i >= 0 else "")
    m = re.search(r'<script[^>]*id="appState"[^>]*>(.*?)</script>', html, re.S) or re.search(r'__INITIAL_STATE__\s*=\s*(\{.*?\});', html, re.S)
    if m: print("  state", m.group(1)[:1500].replace("\n", " "))
    print("  priapi", sorted(set(re.findall(r'/priapi/v\d/[A-Za-z0-9_/\-]*(?:copy|lead|trader|ecotrade)[A-Za-z0-9_/\-]*', html)))[:20])
