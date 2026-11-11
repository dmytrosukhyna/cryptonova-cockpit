"""Seal the owner's internal files before the pipeline commits (11.11.2026, cnq/sealed.py): web/<name>.json → web/sealed/<name>.json,
encrypted to the server's public key; the plaintext is removed from the commit. The key comes from the API (GET /v2/pubkey) or,
when the API does not answer, from the copy kept in web/sealed/pub.json. If there is no key or no `cryptography`, nothing changes
(the files stay as they were) — the pipeline never fails because of this step."""
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.getcwd())
from cnq import sealed  # noqa: E402

URL = os.environ.get("CNQ_PUBKEY_URL", "https://app.crypto-capital.app/v2/pubkey")


def pubkey(web: str = "web", fetch=None) -> str | None:
    cache = os.path.join(web, "sealed", "pub.json")
    try:
        if fetch is None:
            with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "cnq-ci-seal"}), timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
        else:
            d = fetch()
        k = str(d.get("key") or "")
        if len(k) == 44:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            json.dump({"alg": "x25519", "key": k}, open(cache, "w"))
            return k
    except Exception:  # noqa: BLE001
        pass
    try:
        k = str(json.load(open(cache)).get("key") or "")
        return k if len(k) == 44 else None
    except Exception:  # noqa: BLE001
        return None


def run(web: str = "web", fetch=None, git: bool = True) -> list[str]:
    if not sealed.available():
        print("ci_seal: no cryptography package — nothing sealed")
        return []
    k = pubkey(web, fetch)
    if not k:
        print("ci_seal: no server key yet — nothing sealed")
        return []
    done = []
    for name in sealed.NAMES:
        src = os.path.join(web, f"{name}.json")
        if not os.path.exists(src):
            continue
        blob = sealed.seal(k, open(src, "rb").read())
        dst = os.path.join(web, "sealed", f"{name}.json")
        json.dump(blob, open(dst + ".tmp", "w"))
        os.replace(dst + ".tmp", dst)
        os.remove(src)
        if git:
            subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", src], capture_output=True)
        done.append(name)
    print("ci_seal: sealed", done)
    return done


if __name__ == "__main__":
    run()
