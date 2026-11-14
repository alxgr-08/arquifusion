import gzip
import json
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))
from af_shared import Telemetry  # noqa: E402
from generate import generate  # noqa: E402

END = datetime(2026, 11, 13, tzinfo=UTC)
DAYS = 30


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    counts = generate(DAYS, out, seed=7, end=END)
    with gzip.open(out / "telemetry.ndjson.gz", "rt", encoding="utf-8") as fh:
        telemetry = [json.loads(line) for line in fh]
    incidents = [json.loads(line) for line in (out / "incidents.ndjson").read_text(encoding="utf-8").splitlines()]
    movements = [json.loads(line) for line in (out / "parts_movements.ndjson").read_text(encoding="utf-8").splitlines()]
    return counts, telemetry, incidents, movements


def ts(row: dict) -> datetime:
    return datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))


def test_volume_and_contract(data) -> None:
    counts, telemetry, _, _ = data
    assert counts["telemetry"] == len(telemetry) == 12 * DAYS * 96
    for row in telemetry[:: len(telemetry) // 200]:
        Telemetry.model_validate(row)


def test_is_deterministic(tmp_path) -> None:
    a = generate(5, tmp_path / "a", seed=1, end=END)
    b = generate(5, tmp_path / "b", seed=1, end=END)
    assert a == b
    assert (tmp_path / "a" / "incidents.ndjson").read_text() == (tmp_path / "b" / "incidents.ndjson").read_text()


def test_failures_have_precursor_drift(data) -> None:
    _, telemetry, incidents, _ = data
    rows = {}
    for r in telemetry:
        rows.setdefault(r["machineId"], []).append(r)
    checked = 0
    for inc in (i for i in incidents if i["component"] in ("BEARING", "COOLING_SYSTEM")):
        failed = datetime.fromisoformat(inc["createdAt"].replace("Z", "+00:00"))
        own = [r for r in rows[inc["machineId"]] if r["status"] == "OPERATING"]
        before = [r["metrics"]["temperatureC"] for r in own if failed - timedelta(hours=6) <= ts(r) < failed]
        normal = [
            r["metrics"]["temperatureC"]
            for r in own
            if failed - timedelta(days=6) <= ts(r) < failed - timedelta(days=4)
        ]
        if len(before) >= 3 and len(normal) >= 10:
            assert statistics.mean(before) > statistics.mean(normal) + 5
            checked += 1
    assert checked >= 1


def test_zone_fuel_event_creates_zone_incident(data) -> None:
    _, telemetry, incidents, _ = data
    zone = [i for i in incidents if i["zoneIncidentId"]]
    assert len(zone) >= 3
    assert {i["zoneId"] for i in zone} == {"Z3"}
    assert {i["fuelBatchId"] for i in zone} == {"FB-1120-B"}
    assert any(r["context"]["fuelBatchId"] == "FB-1120-B" for r in telemetry)


def test_usage_profiles(data) -> None:
    _, telemetry, _, _ = data

    def utilization(mid: str) -> float:
        own = [r for r in telemetry if r["machineId"] == mid]
        return sum(r["status"] == "OPERATING" for r in own) / len(own)

    assert utilization("TRK-0104") < 0.35
    assert utilization("TRK-0101") > 0.8


def test_progressive_fuel_wear(data) -> None:
    _, telemetry, _, _ = data
    own = [r for r in telemetry if r["machineId"] == "EXC-0051" and r["status"] == "OPERATING"]
    first = statistics.mean(r["metrics"]["fuelRateLph"] for r in own[:200])
    last = statistics.mean(r["metrics"]["fuelRateLph"] for r in own[-200:])
    assert last > first * 1.05


def test_parts_movements_cover_a_year(data) -> None:
    _, _, incidents, movements = data
    days = {m["date"] for m in movements}
    assert len(days) > 200
    corrective = [m for m in movements if m["reason"] == "CORRECTIVE"]
    assert len(corrective) == sum(len(i["partsReplaced"]) for i in incidents)
