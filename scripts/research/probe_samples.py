"""Global trader discovery, step 2 (22.11): print a sample of the leaderboards that answered from the runner (MEXC, Aevo) so a
parser can be written against their real shape. Research only. Usage: probe_samples.py"""
import json, sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import global_traders as G  # noqa: E402

for k in ("mexc", "aevo", "bingx", "gate"):
    st, b = G.http(G.PROBES[k], None, 20)
    print(f"::notice title=sample {k} {st}::" + (b[:3800].decode("utf-8", "replace") if b else ""))
