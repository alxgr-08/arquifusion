"""Load who can use the web and with which role (decision #42: Google login, roles in DynamoDB). Owner: Rol 2.

Usage (lab credentials in ~/.aws/credentials):
    python scripts/seed_grants.py grants.json             # write the grants of the file
    python scripts/seed_grants.py grants.json --dry-run   # print the items, write nothing
    python scripts/seed_grants.py grants.json --revoke    # delete those grants

grants.json (keep it out of git: it has the team's Google account ids):
    [{"sub": "1098...", "label": "Sebastián", "operationId": "MINA-NORTE", "role": "MAINT_LEAD"},
     {"sub": "1123...", "label": "Técnico demo", "operationId": "MINA-NORTE", "role": "TECHNICIAN", "techId": "T-07"}]

`sub` is the Google account id (claim `sub` of the ID token; a 403 NO_GRANT means the account has no row yet).
Re-running with a changed role/techId bumps `version`, which also invalidates confirmations in flight.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from af_shared import grants  # noqa: E402
from af_shared.ddb import get_table  # noqa: E402

GOOGLE_ISS = "accounts.google.com"


def build_items(rows: list[dict[str, Any]], current: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        item = grants.item(
            row.get("iss", GOOGLE_ISS),
            str(row["sub"]),
            row["operationId"],
            row["role"],
            tech_id=row.get("techId"),
            label=row.get("label"),
        )
        old = current.get((item["PK"], item["SK"]))
        if old:
            same = old.get("role") == item["role"] and old.get("techId") == item.get("techId")
            item["version"] = int(old.get("version", 1)) + (0 if same else 1)
        items.append(item)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
    table = None if args.dry_run else get_table()
    current: dict[tuple[str, str], dict[str, Any]] = {}
    if table is not None:
        for row in rows:
            key = grants.grant_key(row.get("iss", GOOGLE_ISS), str(row["sub"]), row["operationId"])
            found = table.get_item(Key=key, ConsistentRead=True).get("Item")
            if found:
                current[(key["PK"], key["SK"])] = found
    items = build_items(rows, current)
    for item in items:
        print(("REVOKE " if args.revoke else "PUT    ") + json.dumps(item, ensure_ascii=False, default=str))
        if table is not None:
            if args.revoke:
                table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            else:
                table.put_item(Item=item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
