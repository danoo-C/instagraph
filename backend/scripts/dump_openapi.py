"""Write FastAPI's OpenAPI schema to a JSON file (used by ``make gen-types``).

Usage: ``python scripts/dump_openapi.py [out.json]`` (default: ``openapi.json``).
Uses a throwaway data directory so no real data is touched.
"""

import json
import sys
import tempfile
from pathlib import Path

from instagraph.config import Settings
from instagraph.main import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=Path(tmp), cors_origins=[]))
        schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
