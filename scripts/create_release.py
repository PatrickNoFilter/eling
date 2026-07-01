"""Create GitHub release for eling v0.1.0."""
from __future__ import annotations

import json
import pathlib
import urllib.request
import urllib.error

token = pathlib.Path("/root/.github-token").read_text().strip()

with open("/root/eling/RELEASE_NOTES.md") as f:
    notes = f.read()

payload = json.dumps({
    "tag_name": "v0.1.0",
    "target_commitish": "main",
    "name": "v0.1.0 — Unified second brain for AI agents",
    "body": notes,
    "draft": False,
    "prerelease": False,
}).encode()

req = urllib.request.Request(
    "https://api.github.com/repos/PatrickNoFilter/eling/releases",
    data=payload,
    method="POST",
)
req.add_header("Authorization", f"token {token}")
req.add_header("Content-Type", "application/json")

try:
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    print("OK:", data.get("html_url"))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print("FAIL:", e.code, json.loads(body).get("message", ""))
