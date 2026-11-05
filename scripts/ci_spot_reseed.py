"""Pipeline step (before the state guard): apply the one-time spot rebuild (cnq/agents/spot_reseed.py, config/spot_reseed.json) to
data/agents.sqlite and publish the rebuilt «CRYPTO NOVA портфель» into web/portfolio.json at once. Outputs reseeded=true when it ran
(site.yml then skips the spot step in this run: the autopilot's first pass must see exactly this model). Prints the plan check."""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, ".")
from cnq.agents import spot_reseed as R  # noqa: E402


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    out = os.environ.get("GITHUB_OUTPUT")
    pub, real, mk = _load("web/portfolio.json"), _load("web/public_real.json"), _load("web/markets.json")
    px = {str(r.get("symbol")).upper(): r.get("price") for r in mk.get("top") or [] if r.get("price")}
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect("data/agents.sqlite")
    try:
        r = R.apply(conn, pub, real, px, int(time.time() * 1000))
    except Exception as e:  # noqa: BLE001
        print(f"::error title=spot reseed::{e}")
        return 0
    if not r:
        print("spot reseed: nothing to do (applied or not requested)")
        return 0
    pub.setdefault("portfolios", {})["ideas"] = r["ideas"]
    with open("web/portfolio.json", "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False)
    print(f"::notice title=spot reseed::{r['id']}: account {json.dumps({k: round(v, 4) for k, v in r['account'].items()})} total ${r['total']:.2f}; "
          f"model {json.dumps({k: round(v, 4) for k, v in r['model'].items()})} cash {r['cash']:.4f}; plan check orders={len(r['gaps'])}; "
          f"display {r['display']}")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("reseeded=true\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
