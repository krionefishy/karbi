"""Сгенерировать TypeScript-контракт DBS и привязать его к точной OpenAPI-схеме.

Первая строка результата содержит SHA256 JSON-схемы. Backend-тест проверяет этот отпечаток без
Node.js, поэтому изменение Pydantic/FastAPI-контракта с забытым `just screenshot-types` краснит CI.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from backend.commands.export_openapi import build_spec, serialize_spec, spec_sha256

TAG = "wb-screenshot-control"
ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
OUTPUT = FRONTEND / "src/features/screenshotControl/schema.d.ts"
FINGERPRINT_PREFIX = f"// {TAG} OpenAPI SHA256: "


def generated_schema() -> str:
    spec = build_spec(TAG)
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        spec_path = temp / "openapi.json"
        output_path = temp / "schema.d.ts"
        spec_path.write_text(serialize_spec(spec), encoding="utf-8")
        subprocess.run(
            ["npx", "--no-install", "openapi-typescript", str(spec_path), "-o", str(output_path)],
            cwd=FRONTEND,
            check=True,
        )
        types = output_path.read_text(encoding="utf-8")
    return f"{FINGERPRINT_PREFIX}{spec_sha256(spec)}\n{types}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate or check the screenshot-control TypeScript contract")
    parser.add_argument("--check", action="store_true", help="fail instead of rewriting when schema.d.ts is stale")
    args = parser.parse_args()
    expected = generated_schema()
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != expected:
            sys.stderr.write("schema.d.ts устарел; выполните `just screenshot-types`\n")
            raise SystemExit(1)
        return
    OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
