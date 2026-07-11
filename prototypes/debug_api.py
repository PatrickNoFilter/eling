"""Test _api_embed directly."""

import os
import sys
from pathlib import Path

# Load .env
_env_path = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

sys.path.insert(0, str(Path(__file__).parent))
from vector_search import _api_embed  # noqa: E402

key = os.environ.get("OPENCODE_ZEN_API_KEY", "")
print(f"API key: {'set' if key else 'MISSING'} ({len(key)} chars)")

result = _api_embed(["test connection"])
if result is None:
    print("FAILED: _api_embed returned None")
    # Try direct curl
    import subprocess

    r = subprocess.run(
        [
            "curl",
            "-s",
            "--max-time",
            "15",
            "-w",
            "\nHTTP:%{http_code}",
            "https://opencode.ai/zen/v1/embeddings",
            "-H",
            f"Authorization: Bearer {key}",
            "-H",
            "Content-Type: application/json",
            "-d",
            '{"model":"text-embedding-3-small","input":["test"]}',
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    print(f"curl stdout: {r.stdout[-200:]}")
    print(f"curl stderr: {r.stderr[-200:]}")
    print(f"curl rc: {r.returncode}")
else:
    vec = result[0]
    print(f"OK: {len(vec)}-dim vector returned, first 5: {vec[:5]}")
