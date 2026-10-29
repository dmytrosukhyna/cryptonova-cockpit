"""Tiny pytest-free runner (pytest works too: python -m pytest tests). A test that takes `tmp_path` gets a fresh temp dir."""
import sys, traceback, importlib, os, inspect, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0
try:                                                       # pytest's own monkeypatch when pytest is installed
    from _pytest.monkeypatch import MonkeyPatch as _MonkeyPatch
except Exception:
    class _MonkeyPatch:
        """setattr / setenv / delenv / setitem with undo — enough for these tests without pytest."""
        _nil = object()
        def __init__(self): self._u = []
        def setattr(self, target, name, value=_nil, raising=True):
            if value is self._nil:                         # setattr("pkg.mod.attr", value)
                mod, _, attr = target.rpartition("."); value = name; target = importlib.import_module(mod); name = attr
            self._u.append(lambda t=target, n=name, o=getattr(target, name, self._nil): delattr(t, n) if o is self._nil else setattr(t, n, o))
            setattr(target, name, value)
        def setitem(self, d, k, v):
            self._u.append(lambda d=d, k=k, o=d.get(k, self._nil): d.pop(k, None) if o is self._nil else d.__setitem__(k, o)); d[k] = v
        def setenv(self, k, v): self.setitem(os.environ, k, str(v))
        def delenv(self, k, raising=True):
            if k in os.environ: self.setitem(os.environ, k, os.environ[k]); del os.environ[k]
        def undo(self):
            while self._u: self._u.pop()()
for mod in ("test_all", "test_agents", "test_portfolio", "test_paper", "test_weekly", "test_calibration", "test_research", "test_whales", "test_channel", "test_botmail", "test_oi", "test_mirror", "test_funding", "test_arb", "test_memes", "test_macro_watch", "test_ivan", "test_outlook", "test_broadcast", "test_app_signals", "test_whale_study", "test_signal_feed", "test_capital", "test_spot_mirror", "test_owner", "test_listing_desk", "test_spot_ideas", "test_channel_watch", "test_channel_opps", "test_turbo_desk", "test_conveyor", "test_risk_layer", "test_scorecard", "test_admin_api", "test_panel_ai", "test_myspot", "test_buy_now", "test_okx_autopilot", "test_okx_futures", "test_okx_lease", "test_myspot_sub", "test_okx_launch", "test_public_real", "test_demo_honesty", "test_access", "test_home_news", "test_public_home", "test_panel_fix_2210"):
  m = importlib.import_module(mod)
  for name in sorted(n for n in dir(m) if n.startswith("test_")):
    f = getattr(m, name)
    if not callable(f):
        continue
    params = inspect.signature(f).parameters
    cases = [{}]
    for mk in getattr(f, "pytestmark", []):              # @pytest.mark.parametrize("a,b", [...]) — one run per value
        if getattr(mk, "name", "") == "parametrize":
            names = [a.strip() for a in mk.args[0].split(",")] if isinstance(mk.args[0], str) else list(mk.args[0])
            vals = [v if len(names) > 1 else (v,) for v in mk.args[1]]
            cases = [{**c, **dict(zip(names, v))} for c in cases for v in vals]
    for case in cases:
        mp = None
        try:
            kw = dict(case)
            if "tmp_path" in params:
                kw["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
            if "monkeypatch" in params:
                mp = _MonkeyPatch(); kw["monkeypatch"] = mp
            f(**kw); ok += 1; print("PASS", name)
        except Exception:
            fail += 1; print("FAIL", name); traceback.print_exc()
        finally:
            if mp is not None:
                mp.undo()
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
