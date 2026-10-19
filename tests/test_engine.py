import json
import os

from cnq import engine as E


def _setup(tmp_path, monkeypatch):
    repo, run = tmp_path / "repo", tmp_path / "run"
    for d in ("cnq", "scripts", "config", "data", "web"):
        (repo / d).mkdir(parents=True)
    (repo / "data" / "agents.sqlite").write_bytes(b"repo-db")
    (repo / "web" / "decision.json").write_text('{"v": 1}')
    (repo / "web" / "paper.json").write_text('{"from": "repo"}')
    monkeypatch.setattr(E, "REPO", str(repo))
    monkeypatch.setattr(E, "RUN", str(run))
    return repo, run


def test_prepare_links_code_and_copies_state_once(tmp_path, monkeypatch):
    repo, run = _setup(tmp_path, monkeypatch)
    E.prepare()
    assert os.path.islink(run / "cnq") and os.path.realpath(run / "cnq") == os.path.realpath(repo / "cnq")
    assert (run / "data" / "agents.sqlite").read_bytes() == b"repo-db"
    (run / "data" / "agents.sqlite").write_bytes(b"engine-db")
    E.prepare()                                                   # a restart never overwrites the engine's own state
    assert (run / "data" / "agents.sqlite").read_bytes() == b"engine-db"


def test_pull_inputs_refreshes_github_files_but_never_the_engines_own(tmp_path, monkeypatch):
    repo, run = _setup(tmp_path, monkeypatch)
    E.prepare()
    (run / "web" / "paper.json").write_text('{"from": "engine"}')
    (run / "data" / "agents.sqlite").write_bytes(b"engine-db")
    (repo / "web" / "decision.json").write_text('{"v": 2, "news": true}')
    (repo / "web" / "paper.json").write_text('{"from": "repo, newer"}')
    (repo / "data" / "agents.sqlite").write_bytes(b"repo-db-newer")
    E.pull_inputs()
    assert json.loads((run / "web" / "decision.json").read_text())["v"] == 2
    assert json.loads((run / "web" / "paper.json").read_text())["from"] == "engine"
    assert (run / "data" / "agents.sqlite").read_bytes() == b"engine-db"


def test_status_file(tmp_path, monkeypatch):
    _repo, run = _setup(tmp_path, monkeypatch)
    E.prepare()
    E.write_status(3, {"paper": {"ok": True, "sec": 4.2}, "own": {"ok": False, "sec": 1.0, "err": "x"}}, 12.5, None, 1)
    d = json.loads((run / "web" / "engine.json").read_text())
    assert d["cycle"] == 3 and d["errors"] == ["own"] and "engine.json" in E.OWNED
