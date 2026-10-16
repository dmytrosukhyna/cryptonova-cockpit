"""Trading journal → web/journal.json for the panel: newest first, up to 90 daily entries from docs/journal/YYYY-MM-DD.md."""
import json, os, re

SRC, OUT = "docs/journal", "web/journal.json"
entries = []
if os.path.isdir(SRC):
    for f in sorted(os.listdir(SRC), reverse=True):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", f):
            entries.append({"date": f[:10], "md": open(os.path.join(SRC, f), encoding="utf-8").read()})
os.makedirs("web", exist_ok=True)
json.dump({"entries": entries[:90]}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print(f"journal: {len(entries)} entries")
