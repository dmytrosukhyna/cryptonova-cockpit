"""GitHub runs for the owner's «Состояние системы» card (09.11.2026) → web/gh_runs.json: the last runs of the data pipeline
(site.yml), the site publishing (publish.yml) and the NOVA probe, with the failed step's name, and the last site publish (gh-pages).
Read-only (GITHUB_TOKEN with actions: read); never fails the pipeline."""
import calendar
import json
import os
import time
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "dmytrosukhyna/cryptonova-cockpit")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
SELF = os.environ.get("GITHUB_RUN_ID")
WORKFLOWS = {"site": "site.yml", "publish": "publish.yml", "nova": "nova-probe.yml", "tests": "tests.yml"}


def api(path: str):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "cnq-ci-runs",
                                          **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) * 1000


def failed_step(run_id: int) -> str | None:
    try:
        for j in api(f"/actions/runs/{run_id}/jobs").get("jobs") or []:
            for s in j.get("steps") or []:
                if s.get("conclusion") == "failure":
                    return s.get("name")
            if j.get("conclusion") == "failure":
                return j.get("name")
    except Exception:  # noqa: BLE001
        return None
    return None


out = {"asof": int(time.time() * 1000), "repo": REPO, "workflows": {}, "errors": []}
for key, wf in WORKFLOWS.items():
    try:
        runs = api(f"/actions/workflows/{wf}/runs?per_page=8").get("workflow_runs") or []
        rows = []
        for r in runs:
            if str(r.get("id")) == SELF:
                continue
            row = {"id": r.get("id"), "status": r.get("status"), "conclusion": r.get("conclusion"), "event": r.get("event"),
                   "t": ms(r.get("run_started_at") or r.get("created_at")), "t_end": ms(r.get("updated_at"))}
            if row["conclusion"] == "failure" and len([x for x in rows if x["conclusion"] == "failure"]) < 2:
                row["failed_step"] = failed_step(r["id"])
            rows.append(row)
        out["workflows"][key] = rows[:6]
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"{wf}: {str(e)[:100]}")
try:
    c = api("/commits?sha=gh-pages&per_page=1")
    out["pages_t"] = ms(((c[0].get("commit") or {}).get("committer") or {}).get("date")) if c else None
except Exception as e:  # noqa: BLE001
    out["errors"].append(f"gh-pages: {str(e)[:100]}")
os.makedirs("web", exist_ok=True)
json.dump(out, open("web/gh_runs.json", "w"), ensure_ascii=False)
print("gh_runs:", {k: [r["conclusion"] for r in v] for k, v in out["workflows"].items()}, out["errors"])
