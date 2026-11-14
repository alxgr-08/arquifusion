"""statemachines/preventive.asl.json end to end, plus the usage factor rules (P2)."""

from decimal import Decimal

from asl_runner import Runner
from flow_harness import Harness, definition
from preventive import usage_factor


def kpi(machine: str, day: str, **fields: float) -> dict:
    return {
        "PK": f"MACHINE#{machine}",
        "SK": f"KPI#{day}",
        "GSI1PK": f"OP#MINA-NORTE#KPI#{day}",
        "GSI1SK": machine,
        **{k: Decimal(str(v)) for k, v in fields.items()},
    }


def run(h: Harness) -> tuple[Runner, dict]:
    runner = Runner(definition("preventive"), h.functions())
    return runner, runner.run({"operationId": "MINA-NORTE"})


def test_usage_factor_bounds_and_terms() -> None:
    assert usage_factor([])[0] == 1.0
    assert usage_factor([], "WARNING")[0] == 1.1
    heavy = [{"operatingHours": 20, "loadRatio": 1.3, "overuseHours": 5, "watchHours": 3}] * 7
    assert usage_factor(heavy)[0] == 1.5
    light = [{"operatingHours": 20, "loadRatio": 0.9, "overuseHours": 0, "watchHours": 0}] * 7
    assert usage_factor(light)[0] == 1.0


def test_same_model_gets_maintenance_at_different_times() -> None:
    h = Harness()
    # EXC-0042 has worked overloaded all week; EXC-0017 (same model) has not.
    for day in range(7, 14):
        date = f"2026-11-{day:02d}"
        h.table.put_item(Item=kpi("EXC-0042", date, operatingHours=20, loadRatio=1.25, overuseHours=4, watchHours=2))
        h.table.put_item(Item=kpi("EXC-0017", date, operatingHours=18, loadRatio=0.95, overuseHours=0, watchHours=0))
    runner, out = run(h)
    assert runner.visited[-1] == "SendDailySummary"
    by_machine = {r["machineId"]: r for r in out["results"]}
    assert "order" in by_machine["EXC-0042"]  # 450 h used of an effective ~333 h interval: due
    assert "order" not in by_machine["EXC-0017"] and by_machine["EXC-0017"]["remainingHours"] > 24
    order = by_machine["EXC-0042"]["order"]
    assert order["usageFactor"] == 1.5 and order["created"] and order["partsOk"]
    assert "Z3: EXC-0042" in out["plan"]["summary"]
    assert any(call["MessageAttributes"]["kind"]["StringValue"] == "DAILY_SUMMARY" for call in h.sns.of("publish"))


def test_orders_are_idempotent_per_day_and_restock_uses_forecast() -> None:
    h = Harness()
    week = "2026-W47"
    h.table.put_item(Item={"PK": "PART#FLT-DSL-05", "SK": f"FORECAST#WH-SUR#{week}", "demand": 9})
    _, first = run(h)
    _, second = run(h)
    due = [r for r in second["results"] if "order" in r]
    assert due and not any(r["order"]["created"] for r in due)
    assert first["plan"]["forecastWeek"] == week
    assert any(r["partId"] == "FLT-DSL-05" and r["warehouseId"] == "WH-SUR" for r in second["plan"]["reorders"])
