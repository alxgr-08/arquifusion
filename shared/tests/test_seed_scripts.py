import json
import sys
from pathlib import Path

import pytest
from af_shared.fakes import FakeTable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import seed_demo  # noqa: E402
import seed_grants  # noqa: E402
from conditional_seed import put_new  # noqa: E402


def test_demo_seed_creates_only_absent_rows_and_reports_conflicts() -> None:
    raw = json.loads((ROOT / "contracts/examples/fleet-config.json").read_text(encoding="utf-8"))
    items = seed_demo.build_items(raw, "2026-11-15T12:00:00Z")
    table = FakeTable()
    taken = next(i for i in items if i["SK"] == "PROFILE" and i["PK"].startswith("MACHINE#"))
    table.put_item(Item={**taken, "operationId": "OTRA-OP"})
    assert {s for s, _ in put_new(table, items, apply=False)} == {"WOULD_CREATE", "CONFLICT"}
    assert len(table.items) == 1  # dry run writes nothing
    report = put_new(table, items, apply=True)
    assert [s for s, _ in report].count("CONFLICT") == 1
    assert table.get_item(Key={"PK": taken["PK"], "SK": "PROFILE"})["Item"]["operationId"] == "OTRA-OP"
    assert {s for s, _ in put_new(table, seed_demo.build_items(raw, "2026-11-16T00:00:00Z"), apply=True)} == {
        "SAME",
        "CONFLICT",
    }


def test_grant_template_must_be_filled_in() -> None:
    with pytest.raises(ValueError, match="Google sub"):
        seed_grants.build_items([{"sub": "<sub de Google>", "operationId": "X", "role": "MAINT_LEAD"}], {})
