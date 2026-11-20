"""prints a JSON file as job annotations in 3 900-character chunks (the way research runs hand numbers back: artifacts are not
reachable from the research sessions). Usage: notice_dump.py FILE FROM TO [TITLE]"""
import json, sys
s = json.dumps(json.load(open(sys.argv[1])), ensure_ascii=False, separators=(",", ":"))
ch = [s[i:i + 3900] for i in range(0, len(s), 3900)]
for k in range(int(sys.argv[2]), min(int(sys.argv[3]), len(ch))):
    print(f"::notice title={sys.argv[4] if len(sys.argv) > 4 else 'dump'} {k + 1}/{len(ch)}::{ch[k]}")
