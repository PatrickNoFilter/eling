"""Quick env debug."""

import os
from pathlib import Path

_env_path = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / ".env"
print(f"env_path exists: {_env_path.exists()}")

if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

key = os.environ.get("OPENCODE_ZEN_API_KEY", "")
print(f"key starts: {key[:10] if key else 'EMPTY'}")
print(f"key length: {len(key)}")
if len(key) > 10:
    print(f"key ends: ...{key[-5:]}")
