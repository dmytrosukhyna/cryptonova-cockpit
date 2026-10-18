"""Tiny pytest-free runner (pytest works too: python -m pytest tests)."""
import sys, traceback, importlib, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0
for mod in ("test_all", "test_agents", "test_portfolio", "test_paper", "test_weekly", "test_calibration", "test_research", "test_whales", "test_channel", "test_botmail", "test_oi", "test_mirror", "test_funding", "test_arb", "test_memes", "test_macro_watch", "test_ivan", "test_outlook", "test_broadcast", "test_app_signals", "test_whale_study", "test_signal_feed", "test_capital", "test_spot_mirror", "test_owner"):
  m = importlib.import_module(mod)
  for name in sorted(n for n in dir(m) if n.startswith("test_")):
    try:
        getattr(m, name)(); ok += 1; print("PASS", name)
    except Exception:
        fail += 1; print("FAIL", name); traceback.print_exc()
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
