"""Wires the real Lambdas of Rol 2 to the ASL runner with in-memory AWS fakes."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from af_shared import config
from af_shared.ddb import iso
from af_shared.fakes import FakeSsm, FakeTable, Recorder, demo_fleet_raw
from asl_runner import Runner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import seed_demo  # noqa: E402

TOPICS = {
    role: f"arn:aws:sns:us-east-1:123456789012:af-dev-{role.lower()}"
    for role in ("OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER")
}
EXAMPLES = ROOT / "contracts" / "examples"


def load(service: str) -> Any:
    name = f"{service}_handler"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "services" / service / "handler.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def definition(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "statemachines" / f"{name}.asl.json").read_text(encoding="utf-8"))


def anomaly_event(
    machine_id: str = "EXC-0042",
    severity: str = "WARNING",
    zone: str = "Z3",
    model: str = "EXC-320",
    signals: list[dict] | None = None,
    detected_at: str = "2026-11-13T14:06:10Z",
) -> dict[str, Any]:
    event = json.loads((EXAMPLES / "anomaly-detected.json").read_text(encoding="utf-8"))
    event["detail"].update(
        {"machineId": machine_id, "severity": severity, "zoneId": zone, "model": model, "detectedAt": detected_at}
    )
    if signals:
        event["detail"]["signals"] = signals
    return {
        "version": "0",
        "id": "evt-1",
        "account": "123456789012",
        "region": "us-east-1",
        "time": detected_at,
        "resources": [],
        **event,
    }


def ai_example() -> dict[str, Any]:
    return json.loads((EXAMPLES / "ai-diagnosis-response.json").read_text(encoding="utf-8"))


def alexis_rules(payload: dict[str, Any]) -> dict[str, Any]:
    """The real af-dev-ai-diagnosis rule engine (Rol 3), fed with the request our Step Function builds.

    DiagnosisRequest forbids extra fields, so this also checks our request against the contract."""
    import rules  # services/ai_diagnosis/rules.py
    from af_shared import DiagnosisRequest

    return rules.diagnose(DiagnosisRequest.model_validate(payload)).model_dump(by_alias=True, mode="json")


class Clock:
    def __init__(self, start: str = "2026-11-13T14:06:30Z") -> None:
        self.now = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC)

    def __call__(self) -> str:
        self.now += timedelta(seconds=5)
        return iso(self.now)

    def advance(self, **delta: float) -> str:
        self.now += timedelta(**delta)
        return iso(self.now)


class Harness:
    def __init__(self, ai: Any = "rules") -> None:
        raw = demo_fleet_raw()
        self.table = FakeTable()
        for item in seed_demo.build_items(raw):
            self.table.put_item(Item=item)
        params = {**seed_demo.build_parameters(raw, demo=True), config.NOTIFY_TOPICS: TOPICS}
        self.ssm = FakeSsm(params)
        self.store = config.ParamStore(client=self.ssm, ttl_seconds=0)
        self.sns = Recorder(
            {"publish": lambda **kw: {"MessageId": f"m-{kw['MessageAttributes']['role']['StringValue']}"}}
        )
        self.clock = Clock()
        self.ai_calls: list[dict[str, Any]] = []
        self.ai = ai
        self.tasks = load("incident_tasks")
        self.assign = sys.modules.get("assignment") or __import__("assignment")
        self.notify = load("notify")

    def ai_lambda(self, payload: dict[str, Any]) -> Any:
        self.ai_calls.append(payload)
        if self.ai == "rules":
            return alexis_rules(payload)
        if self.ai == "example":
            return ai_example()
        if callable(self.ai):
            return self.ai(payload)
        return copy.deepcopy(self.ai)

    def functions(self) -> dict[str, Any]:
        def tasks(p: dict[str, Any]) -> Any:
            return self.tasks.dispatch(p["action"], p, self.table, self.store, self.clock())

        def assign(p: dict[str, Any]) -> Any:
            if p.get("action") == "release":
                return self.assign.release(self.table, p, self.clock())
            return self.assign.assign(self.table, p, self.clock())

        def notify(p: dict[str, Any]) -> Any:
            return self.notify.run(p, self.sns, self.table, self.store, self.clock())

        return {
            "${incident_tasks_arn}": tasks,
            "${assign_technician_arn}": assign,
            "${notify_arn}": notify,
            "${ai_diagnosis_arn}": self.ai_lambda,
        }

    def run_incident(self, event: dict[str, Any], on_wait: Any) -> tuple[Runner, Any]:
        runner = Runner(definition("incident"), self.functions(), on_wait)
        return runner, runner.run(event)

    def incident(self, machine_id: str) -> dict[str, Any]:
        rows = [
            i
            for (pk, sk), i in self.table.items.items()
            if pk == f"MACHINE#{machine_id}" and sk.startswith("INCIDENT#")
        ]
        assert rows, f"no incident for {machine_id}"
        return sorted(rows, key=lambda r: r["SK"])[-1]

    def published(self) -> list[tuple[str, str, str]]:
        out = []
        for call in self.sns.of("publish"):
            attrs = call["MessageAttributes"]
            out.append((attrs["kind"]["StringValue"], attrs["role"]["StringValue"], attrs["channel"]["StringValue"]))
        return out


def ack_all(arrived_after: float = 20, resolved_after: float = 95, harness: Harness | None = None):
    """on_wait handler: technician arrives and resolves after N minutes (simulated)."""

    def on_wait(info: dict[str, Any]) -> Any:
        stage = info["payload"]["stage"]
        if harness is not None:
            at = harness.clock.advance(minutes=arrived_after if stage == "ARRIVAL" else resolved_after)
        else:
            at = "2026-11-13T15:50:00Z"
        return {"event": "ARRIVED" if stage == "ARRIVAL" else "RESOLVED", "at": at}

    return on_wait
