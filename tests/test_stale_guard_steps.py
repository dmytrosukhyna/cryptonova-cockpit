"""04.11 incident: after the 19:28 UTC cache loss every site.yml run worked on a 15-day-old agents.sqlite.
Steps whose state lives in that database must not run (or publish / post) on a stale state."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _step(site: str, marker: str) -> str:
    i = site.index(marker)
    j = site.find("      - name:", i + 1)
    return site[i:j]


def test_stateful_steps_skip_on_stale_database():
    site = open(os.path.join(ROOT, ".github", "workflows", "site.yml"), encoding="utf-8").read()
    for marker in ("→ web/capital.json", "→ web/channel_opps.json", "→ web/calibration.json",
                   "Channel editor → @crypto_nova_news", "Spot portfolio department", "Top traders study"):
        assert "steps.state.outputs.stale != 'true'" in _step(site, marker), marker
