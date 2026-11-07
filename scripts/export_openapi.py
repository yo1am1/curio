"""Write the API's OpenAPI schema to a file without starting a server or loading any data.

Usage:
    uv run python -m scripts.export_openapi [--out web/openapi.json]

The React app (`web/`) generates its TypeScript API types from this file
(`npm run gen:api`). The schema comes from `create_app().openapi()`: building the app reads
no data, because services are only built when the app starts serving.
"""

import argparse
import json
from pathlib import Path

from curio.app.api import create_app

DEFAULT_OUT = Path("web/openapi.json")


def openapi_schema() -> dict:
    return create_app().openapi()


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output JSON file.")
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(openapi_schema(), indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.out}")
    return args.out


if __name__ == "__main__":
    main()
