"""Create rows only if they are absent; never overwrite shared data. Owner: Rol 2.

Every row is written with `attribute_not_exists(PK)`. An existing row is reported as SAME (identical apart from
volatile fields) or CONFLICT (someone else's data) and left untouched.
"""

from __future__ import annotations

from typing import Any

VOLATILE = {"lastSeen", "createdAt", "generatedAt", "updatedAt"}


def _comparable(item: dict[str, Any]) -> dict[str, Any]:
    from af_shared.ddb import from_ddb, to_ddb

    return {k: v for k, v in from_ddb(to_ddb(item)).items() if k not in VOLATILE}


def put_new(table: Any, items: list[dict[str, Any]], apply: bool) -> list[tuple[str, dict[str, Any]]]:
    """Returns [(status, item)] with status CREATED | WOULD_CREATE | SAME | CONFLICT."""
    from af_shared.ddb import is_conditional_failure, to_ddb

    report = []
    for item in items:
        key = {"PK": item["PK"], "SK": item["SK"]}
        existing = table.get_item(Key=key, ConsistentRead=True).get("Item")
        if existing is not None:
            report.append(("SAME" if _comparable(existing) == _comparable(item) else "CONFLICT", item))
            continue
        if not apply:
            report.append(("WOULD_CREATE", item))
            continue
        try:
            table.put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")
            report.append(("CREATED", item))
        except Exception as exc:  # created by someone else between the read and the write
            if not is_conditional_failure(exc):
                raise
            report.append(("CONFLICT", item))
    return report


def print_report(report: list[tuple[str, dict[str, Any]]]) -> int:
    counts: dict[str, int] = {}
    for status, item in report:
        counts[status] = counts.get(status, 0) + 1
        if status != "SAME":
            print(f"{status:<12} {item['PK']} / {item['SK']}")
    print("Total: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("CONFLICT") else 0
