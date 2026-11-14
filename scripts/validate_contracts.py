"""Validate every contracts/examples/*.json against its JSON Schema (prefix match)."""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1] / "contracts"
SCHEMAS = {
    "telemetry": "telemetry.schema.json",
    "anomaly-detected": "anomaly-detected.schema.json",
    "fleet-config": "fleet-config.schema.json",
    "ai-diagnosis-request": "ai-diagnosis.request.schema.json",
    "ai-diagnosis-response": "ai-diagnosis.response.schema.json",
}


def main() -> int:
    errors = 0
    for example in sorted((ROOT / "examples").glob("*.json")):
        prefix = next((p for p in SCHEMAS if example.name.startswith(p)), None)
        if prefix is None:
            continue
        schema = json.loads((ROOT / SCHEMAS[prefix]).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        issues = list(validator.iter_errors(json.loads(example.read_text(encoding="utf-8"))))
        status = "OK " if not issues else "ERR"
        print(f"[{status}] {example.name}")
        for issue in issues:
            print(f"      {'/'.join(map(str, issue.path))}: {issue.message}")
        errors += len(issues)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
