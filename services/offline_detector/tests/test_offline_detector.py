from datetime import UTC, datetime

from af_shared import keys
from af_shared.fakes import FakeTable


def machine(table: FakeTable, mid: str, status: str, last_seen: str | None) -> None:
    item = {**keys.machine_profile(mid), **keys.machine_gsi1("MINA-NORTE", status, mid), "status": status}
    if last_seen:
        item["lastSeen"] = last_seen
    table.put_item(Item=item)


def test_marks_only_stale_machines(load_handler) -> None:
    h = load_handler("offline_detector")
    t = FakeTable()
    machine(t, "EXC-0001", "OPERATING", "2026-11-14T14:59:00Z")  # fresh
    machine(t, "EXC-0002", "OPERATING", "2026-11-14T14:40:00Z")  # stale
    machine(t, "EXC-0003", "IDLE", None)  # never reported
    machine(t, "EXC-0004", "OFFLINE", "2026-11-14T10:00:00Z")  # already offline
    marked = h.mark_offline(t, "MINA-NORTE", datetime(2026, 11, 14, 15, 0, tzinfo=UTC), 5)
    assert sorted(marked) == ["EXC-0002", "EXC-0003"]
    stale = t.get("MACHINE#EXC-0002", "PROFILE")
    assert stale["status"] == "OFFLINE" and stale["GSI1SK"] == "OFFLINE#EXC-0002"
    assert t.get("MACHINE#EXC-0001", "PROFILE")["status"] == "OPERATING"
