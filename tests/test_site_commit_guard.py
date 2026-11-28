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


def test_rebase_replays_only_our_commit():
    """10.11: shallow checkout + `rebase origin/main` replayed the checked-out tip as a whole tree and rolled web files back."""
    step = _commit_step()
    assert re.search(r'base=\$\(git rev-parse HEAD\)\n\s*git commit', step)
    assert '--onto origin/main "$base"' in step
    al = (WF.parent / "autolab.yml").read_text(encoding="utf-8")
    assert '--onto origin/main "$base"' in al and "base=$(git rev-parse HEAD)" in al


def test_engine_sync_retry_replays_only_its_commit():
    """14.11: the server engine's push retry («pull --rebase -X theirs») rolled a file pushed seconds earlier back (21a8413).
    26.11: the «rebase --onto origin/main base» retry re-pushed an already-pushed tree (51f7a63b deleted a watch_log line).
    The retry must not rebase: reset to origin/main, re-copy and re-commit the engine's own files, and push only a commit
    whose changed paths are all the engine's own."""
    src = (WF.parents[2] / "cnq" / "engine.py").read_text(encoding="utf-8")
    i = src.index("def sync(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert '"-X", "theirs"' not in body and '"--onto"' not in body
    assert '["reset", "-q", "--hard", "origin/main"]' in body
    assert '["diff", "--name-only", "HEAD~1", "HEAD"]' in body
    assert body.index('["diff", "--name-only", "HEAD~1", "HEAD"]') < body.index('["push", "-q", "origin", "HEAD:main"]')


def test_engine_sync_never_pushes_foreign_paths(tmp_path, monkeypatch):
    """end to end on throw-away repos: a push that «failed» after landing, then someone else's doc commit — the retry must
    keep the other commit's file and push nothing stale"""
    import subprocess
    import sys
    sys.path.insert(0, str(WF.parents[2]))
    from cnq import engine

    def g(*a, cwd):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "web").mkdir()
    (seed / "docs").mkdir()
    (seed / "web" / "paper.json").write_text("{}")
    (seed / "docs" / "log.md").write_text("a\n")
    g("add", "-A", cwd=seed)
    g("commit", "-qm", "seed", cwd=seed)
    g("push", "-q", "origin", "HEAD:main", cwd=seed)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    g("config", "user.name", "e", cwd=repo)
    g("config", "user.email", "e@e", cwd=repo)
    run = tmp_path / "run"
    (run / "web").mkdir(parents=True)
    (run / "data").mkdir()
    (run / "web" / "paper.json").write_text('{"x": 1}')
    monkeypatch.setattr(engine, "REPO", str(repo))
    monkeypatch.setattr(engine, "RUN", str(run))
    monkeypatch.setattr(engine, "OWNED", ["paper.json"])
    monkeypatch.setattr(engine, "DATA_OWNED", [])
    monkeypatch.setattr(engine, "pull_inputs", lambda: None)
    real_sh = engine.sh
    calls = {"push": 0}

    def fake_sh(args, cwd, timeout=120, env=None):
        if "push" in args:
            calls["push"] += 1
            if calls["push"] == 1:                # the push lands, but the step reports a failure …
                r = real_sh(args, cwd, timeout, env)
                g("pull", "-q", cwd=seed)          # … and a doc commit lands on top right after it
                (seed / "docs" / "log.md").write_text("a\nb\n")
                g("commit", "-qam", "watch", cwd=seed)
                g("push", "-q", "origin", "HEAD:main", cwd=seed)
                return False, r[1], "timeout"
        return real_sh(args, cwd, timeout, env)

    monkeypatch.setattr(engine, "sh", fake_sh)
    engine.sync(push=True)
    # a stale foreign file staged in the engine's checkout (how a whole-tree replay looks to the push) never reaches main
    (repo / "docs" / "log.md").write_text("stale\n")
    g("add", "docs/log.md", cwd=repo)
    monkeypatch.setattr(engine, "pull_inputs", lambda: (repo / "docs" / "log.md").write_text("stale\n")
                        or subprocess.run(["git", "add", "docs/log.md"], cwd=repo, check=True))
    (run / "web" / "paper.json").write_text('{"x": 2}')
    engine.sync(push=True)
    chk = tmp_path / "chk"
    subprocess.run(["git", "clone", "-q", str(origin), str(chk)], check=True)
    assert (chk / "docs" / "log.md").read_text() == "a\nb\n"
    assert (chk / "web" / "paper.json").read_text() == '{"x": 2}'


def _rebase_cmd(path: pathlib.Path) -> str:
    """the «git rebase …» part of a workflow's push-retry loop"""
    loop = next(l for l in path.read_text(encoding="utf-8").splitlines() if "git rebase" in l and "for i in" in l)
    return re.search(r"(git rebase [^&]*?)\s*&&", loop).group(1)


def test_workflow_retry_rebases_only_our_commit(tmp_path):
    """28.11: site.yml attempt 1 rebased fine but its push was rejected; attempt 2 rebased base..HEAD, which by then held the
    engine commit fetched in attempt 1, and -X theirs replayed it over newer main (02ad618 rolled docs/research/backlog.md and
    watch_log.md back to before a691cb3). End to end on a shallow clone, with the exact commands from the workflows."""
    import subprocess
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": __import__("os").environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}

    def sh(cmd, cwd):
        return subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout

    for wf in ("site.yml", "autolab.yml", "macro-flag.yml"):
        d = tmp_path / wf
        d.mkdir()
        sh("git init -q --bare -b main o && git clone -q o w && cd w && echo 1 > X && echo 1 > D && git add . && "
           "git commit -qm init && git push -q origin HEAD:main", d)
        sh(f"git clone -q --depth 1 -b main file://{d}/o a", d)
        a, w = d / "a", d / "w"
        sh("echo ours > D && git commit -qam ours", a)
        base, mine = sh("git rev-parse HEAD~1", a).strip(), sh("git rev-parse HEAD", a).strip()
        cmd = _rebase_cmd(WF.parent / wf)
        run = f'base={base}; mine={mine}; git fetch -q origin main && {cmd}'
        sh("echo E > X && git commit -qam engine && git push -q origin HEAD:main", w)   # the engine pushes …
        sh(run, a)                                                                      # attempt 1 (its push «rejected»)
        sh("echo R > X && git commit -qam research && git push -q origin HEAD:main", w)  # … a research commit lands
        sh(run, a)                                                                      # attempt 2
        assert (a / "X").read_text() == "R\n", wf
        assert (a / "D").read_text() == "ours\n", wf
        assert sh("git rev-list --count origin/main..HEAD", a).strip() == "1", wf
