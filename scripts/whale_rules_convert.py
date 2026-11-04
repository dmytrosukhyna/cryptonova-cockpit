"""whale_rules_data.json.gz (≈ 0.9 GB of JSON) → a small whales.json.gz (hl / okx / fund) + one .npz per coin (15m, funding,
OI), so the study does not need the whole file in memory. Usage: whale_rules_convert.py DATA OUTDIR"""
import gzip, json, os, sys
import numpy as np

src, out = sys.argv[1], sys.argv[2]
os.makedirs(os.path.join(out, "mkt"), exist_ok=True)
s = gzip.open(src, "rt").read()
dec = json.JSONDecoder()
i = s.index('"mkt":{') + len('"mkt":{')
meta = {}
while s[i] != "}":
    if s[i] == ",":
        i += 1
    key, i = dec.raw_decode(s, i)
    assert s[i] == ":"; i += 1
    val, i = dec.raw_decode(s, i)
    k15 = np.array(val.get("k15") or [], dtype=float).reshape(-1, 6)
    k1h = np.array(val.get("k1h_hl") or [], dtype=float).reshape(-1, 6)
    np.savez(os.path.join(out, "mkt", f"{key}.npz"), k15=k15, k1h_hl=k1h, fund=np.array(val.get("fund") or [], dtype=float).reshape(-1, 2),
             fund_hl=np.array(val.get("fund_hl") or [], dtype=float).reshape(-1, 2), oi=np.array(val.get("oi") or [], dtype=float).reshape(-1, 2))
    meta[key] = {"sym": val.get("sym"), "src": val.get("src")}
mkt_end = i + 1
rest = s[:s.index('"mkt":{')] + '"mkt":' + json.dumps(meta) + s[mkt_end:]
del s
d = json.loads(rest)
with gzip.open(os.path.join(out, "whales.json.gz"), "wt") as g:
    json.dump(d, g)
print("coins", len(meta), "keys", list(d.keys()))
