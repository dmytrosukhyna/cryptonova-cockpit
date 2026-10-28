"""Tiny pytest-free runner (pytest works too: python -m pytest tests). A test that takes `tmp_path` gets a fresh temp dir."""
import sys, traceback, importlib, os, inspect, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0
for mod in ("test_all", "test_agents", "test_portfolio", "test_paper", "test_weekly", "test_calibration", "test_research", "test_whales", "test_channel", "test_botmail", "test_oi", "test_mirror", "test_funding", "test_arb", "test_memes", "test_macro_watch", "test_ivan", "test_outlook", "test_broadcast", "test_app_signals", "test_whale_study", "test_signal_feed", "test_capital", "test_spot_mirror", "test_owner", "test_listing_desk", "test_spot_ideas", "test_channel_watch", "test_turbo_desk", "test_conveyor", "test_risk_layer", "test_scorecard", "test_admin_api", "test_panel_ai", "test_myspot", "test_buy_now", "test_okx_autopilot", "test_okx_futures", "test_okx_lease", "test_myspot_sub", "test_okx_launch", "test_public_real", "test_demo_honesty", "test_access", "test_home_news", "test_public_home", "test_panel_fix_2210", "test_tts"):
  m = importlib.import_module(mod)
  for name in sorted(n for n in dir(m) if n.startswith("test_")):
    f = getattr(m, name)
    if not callable(f):
        continue
    try:
        kw = {"tmp_path": pathlib.Path(tempfile.mkdtemp())} if "tmp_path" in inspect.signature(f).parameters else {}
        f(**kw); ok += 1; print("PASS", name)
    except Exception:
        fail += 1; print("FAIL", name); traceback.print_exc()
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
