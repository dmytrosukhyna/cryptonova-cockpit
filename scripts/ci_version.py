"""Writes web/version.json (build time + commit) so the panel can show the build and offer a reload when a newer one is published."""
import json, subprocess, time
sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
json.dump({"time": int(time.time() * 1000), "sha": sha}, open("web/version.json", "w"))
print("version", sha)
