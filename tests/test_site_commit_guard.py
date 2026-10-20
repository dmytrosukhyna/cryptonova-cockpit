"""20.10.2026: data/agents.sqlite passed GitHub's 100 MB file limit and every data push from the pipeline was rejected for hours.
The commit step must add the database only below the limit and rebase with --autostash (the uncommitted database stays dirty)."""
import pathlib
import re

WF = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "site.yml"


def _commit_step() -> str:
    s = WF.read_text(encoding="utf-8")
    i = s.index("- name: Commit refreshed data")
    return s[i:s.index("- name:", i + 10)]


def test_agents_sqlite_not_in_unconditional_add_list():
    step = _commit_step()
    loop = re.search(r"for f in (.*?); do", step).group(1)
    assert "data/agents.sqlite" not in loop.split()
    assert re.search(r'-lt 9\d000000 \]; then git add data/agents.sqlite', step)


def test_rebase_autostash():
    assert "git rebase --autostash" in _commit_step()
