"""Generate contract-valid acceptance fixtures locally. Does not call AWS."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import ascii_uppercase
from uuid import uuid4

from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "contracts"


def prepare_case(parent: Path) -> Path:
    run = uuid4()
    prefix = "HE" + "".join(ascii_uppercase[b % 26] for b in run.bytes[:3])
    machine = f"{prefix}-{run.int % 10000:04d}"
    op = f"AF-TEST-{run.hex[:12].upper()}"
    model = f"AF-TEST-{run.hex.upper()}"
    now = datetime.now(UTC).replace(microsecond=0)
    case = parent / run.hex
    case.mkdir(parents=True)

    def save(name: str, payload: dict) -> None:
        (case / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    validator = Draft202012Validator(
        json.loads((CONTRACTS / "telemetry.schema.json").read_text(encoding="utf-8")),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    pk = {"S": f"MACHINE#{machine}"}
    for label, example, ts in [("normal", "ok", now - timedelta(seconds=1)), ("anomaly", "warning", now)]:
        msg = json.loads((CONTRACTS / "examples" / f"telemetry.{example}.json").read_text(encoding="utf-8"))
        timestamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        msg.update(machineId=machine, operationId=op, model=model, messageId=str(uuid4()), timestamp=timestamp)
        validator.validate(msg)
        save(label, msg)
        reading_at = ts.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        save(f"{label}-key", {"PK": pk, "SK": {"S": f"READING#{reading_at}"}})
        if label == "normal":
            mean = str(msg["metrics"]["vibrationMmS"])
            save(
                "baseline",
                {
                    "PK": {"S": f"MODEL#{model}"},
                    "SK": {"S": "BASELINE"},
                    "metrics": {"M": {"vibrationMmS": {"M": {"mean": {"N": mean}, "std": {"N": "0.1"}}}}},
                },
            )
    save("profile-key", {"PK": pk, "SK": {"S": "PROFILE"}})
    save(
        "event-pattern",
        {
            "source": ["arquifusion.health"],
            "detail-type": ["AnomalyDetected"],
            "detail": {"machineId": [machine], "operationId": [op], "severity": ["CRITICAL"]},
        },
    )
    return case


if __name__ == "__main__":
    print(prepare_case(REPO / "build" / "health-evaluator" / "cases"))
