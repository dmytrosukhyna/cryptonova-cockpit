"""prints a slice of owner_calls_prices.json as job annotations (hourly closes + the lowest low / highest high with their hour) —
the only way the research reads a run's numbers back. Usage: owner_calls_dump.py FILE FROM TO"""
import json, sys
d = json.load(open(sys.argv[1]))
ks = sorted(d["k1h"])[int(sys.argv[2]):int(sys.argv[3])]
for c in ks:
    k = d["k1h"][c]
    lo = min(k, key=lambda r: r[3]); hi = max(k, key=lambda r: r[2])
    print(f"::notice title={c}|{d['src'][c]}|{k[0][0]}|{lo[3]}@{lo[0]}|{hi[2]}@{hi[0]}::" + ",".join(f"{r[4]:.6g}" for r in k))
