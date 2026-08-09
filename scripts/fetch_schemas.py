"""Refresh the vendored Zerops JSON Schemas.

Run this when Zerops publishes new service versions, then re-run the tests: a
new or removed service type shows up as a validation failure rather than a
silent wrong mapping.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

BASE = "https://api.app-prg1.zerops.io/api/rest/public/settings"
SCHEMAS = {
    "import-schema.json": f"{BASE}/import-project-yml-json-schema.json",
    "zerops-yml-schema.json": f"{BASE}/zerops-yml-json-schema.json",
}
TARGET = Path(__file__).resolve().parent.parent / "schemas"


def main() -> int:
    TARGET.mkdir(exist_ok=True)
    for name, url in SCHEMAS.items():
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read()
        except OSError as exc:
            print(f"failed to fetch {name}: {exc}")
            return 1
        (TARGET / name).write_bytes(body)
        print(f"wrote {name} ({len(body):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
