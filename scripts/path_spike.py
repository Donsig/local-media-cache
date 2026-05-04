#!/usr/bin/env python3
"""
Data path spike — proves the Plex→ffmpeg→Range pipeline before Stage 1.

Usage:
    PLEX_URL=http://10.20.0.5:32400 \
    PLEX_TOKEN=<token> \
    PLEX_SEARCH="Bluey" \
    PLEX_PATH_PREFIX=/media \
    LOCAL_PATH_PREFIX=/mnt/media \
    python scripts/path_spike.py
"""
import hashlib
import os
import subprocess
import sys

from plexapi.server import PlexServer

plex_url = os.environ["PLEX_URL"]
plex_token = os.environ["PLEX_TOKEN"]
search_term = os.environ.get("PLEX_SEARCH", "Bluey")
plex_prefix = os.environ.get("PLEX_PATH_PREFIX", "")
local_prefix = os.environ.get("LOCAL_PATH_PREFIX", "")
output_path = "/tmp/spike_output.mkv"

print(f"Connecting to Plex at {plex_url}...")
plex = PlexServer(plex_url, plex_token)

print(f"Searching for '{search_term}'...")
results = plex.search(search_term)
if not results:
    print("No results found. Check PLEX_SEARCH.")
    sys.exit(1)

item = None
for r in results:
    if r.TYPE == "episode":
        item = r
        break
    if r.TYPE == "show":
        seasons = r.seasons()
        if seasons:
            episodes = seasons[0].episodes()
            if episodes:
                item = episodes[0]
                break
    if r.TYPE == "movie":
        item = r
        break

if not item:
    print("Couldn't find an episode or movie in results.")
    sys.exit(1)

title = f"{getattr(item, 'grandparentTitle', '')} {item.title}".strip()
print(f"Found: {title}")

media_parts = item.media[0].parts
if not media_parts:
    print("No media parts found.")
    sys.exit(1)

plex_path = media_parts[0].file
print(f"Plex reports path: {plex_path}")

if plex_prefix and plex_path.startswith(plex_prefix):
    local_path = local_prefix + plex_path[len(plex_prefix):]
else:
    local_path = plex_path
print(f"Local path (after rewrite): {local_path}")

if not os.path.exists(local_path):
    print(f"ERROR: File not accessible at {local_path}")
    print("Check PLEX_PATH_PREFIX / LOCAL_PATH_PREFIX env vars.")
    sys.exit(1)

print(f"File exists. Size: {os.path.getsize(local_path):,} bytes")

print(f"Running ffmpeg (30s clip → {output_path})...")
result = subprocess.run([
    "ffmpeg", "-y",
    "-i", local_path,
    "-t", "30",
    "-c:v", "libx265", "-crf", "28", "-preset", "fast",
    "-c:a", "aac", "-b:a", "96k",
    output_path,
], capture_output=True, text=True)

if result.returncode != 0:
    print("ffmpeg failed:")
    print(result.stderr[-2000:])
    sys.exit(1)

size = os.path.getsize(output_path)
sha256 = hashlib.sha256(open(output_path, "rb").read()).hexdigest()
print(f"ffmpeg succeeded. Output: {size:,} bytes, sha256: {sha256[:16]}...")

print("\nTesting HTTP Range serving...")
server = subprocess.Popen(
    ["python3", "-m", "http.server", "8765", "--directory", "/tmp"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
import time; time.sleep(1)

try:
    r1 = subprocess.run(
        ["curl", "-s", "--range", "0-1048575", "http://localhost:8765/spike_output.mkv"],
        capture_output=True,
    )
    r2 = subprocess.run(
        ["curl", "-s", "--range", "-102400", "http://localhost:8765/spike_output.mkv"],
        capture_output=True,
    )
    range1_ok = len(r1.stdout) == 1048576
    range2_ok = len(r2.stdout) == 102400
    print(f"Range 0-1MB:  {'OK' if range1_ok else 'FAIL'} ({len(r1.stdout):,} bytes, expected 1,048,576)")
    print(f"Range last 100KB: {'OK' if range2_ok else 'FAIL'} ({len(r2.stdout):,} bytes, expected 102,400)")
finally:
    server.terminate()

print("\n=== SPIKE RESULT ===")
print(f"Plex path:          {plex_path}")
print(f"Rewritten path:     {local_path}")
print(f"File accessible:    YES")
print(f"ffmpeg transcode:   OK ({size:,} bytes for 30s clip)")
print(f"Estimated full ep:  ~{size * 24 / 1024**3:.1f} GB (extrapolated from 30s)")
print(f"SHA256 (partial):   {sha256[:16]}...")
print(f"Range requests:     {'OK' if range1_ok and range2_ok else 'FAIL'}")
print(f"\nOutput file: {output_path}")
