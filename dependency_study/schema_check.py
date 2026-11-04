"""Check the executed artifact schema (semantic checks remain in audit.py)."""
import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft7Validator


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--schema", type=Path, default=Path("schemas/dependency_study_fixture_v2.json"))
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text())
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema)
    files = sorted((args.directory / "fixtures").glob("*.json"))
    for path in files:
        validator.validate(json.loads(path.read_text()))
    report = dict(status="passed", fixtures=len(files), schema_sha256=hashlib.sha256(args.schema.read_bytes()).hexdigest(),
                  scope="Draft-07 structural validation; raw-byte and design checks are separate")
    target = args.directory / "analysis/schema_verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
