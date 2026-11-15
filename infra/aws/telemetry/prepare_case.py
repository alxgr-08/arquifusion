"""Prepare one isolated simulator-direct reading and capture both outputs locally."""

import json
import sys
from pathlib import Path
from string import ascii_uppercase
from unittest.mock import patch
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
for source in (ROOT, ROOT / "shared", ROOT / "services" / "simulator"):
    sys.path.insert(0, str(source))

from af_shared import config  # noqa: E402
from af_shared.fakes import FakeSsm, Recorder  # noqa: E402

from services.simulator.handler import Publisher, run  # noqa: E402

FUNCTION = "af-dev-health-evaluator"
STREAM = "af-dev-telemetry"


def single_fleet(run_id: UUID) -> dict:
    template = json.loads((ROOT / "contracts/examples/fleet-config.json").read_text(encoding="utf-8"))
    letters = "".join(ascii_uppercase[b % 26] for b in run_id.bytes[:3])
    machine_id = f"TL{letters}-{run_id.int % 10000:04d}"
    operation_id = f"AF-LAKE-TEST-{run_id.hex.upper()}"
    sample = template["machines"][0]
    model_id = f"AF-LAKE-MODEL-{run_id.hex.upper()}"
    return {
        "schemaVersion": template["schemaVersion"],
        "operationId": operation_id,
        "machines": [{**sample, "machineId": machine_id, "zoneId": "ZT", "model": model_id}],
        "models": {model_id: template["models"][sample["model"]]},
        "zones": {"ZT": template["zones"][sample["zoneId"]]},
    }


def validate_reading(payload: dict) -> None:
    schema = json.loads((ROOT / "contracts/telemetry.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(payload)


def prepare(parent: Path) -> Path:
    run_id = uuid4()
    fleet = single_fleet(run_id)
    store = config.ParamStore(client=FakeSsm({config.FLEET_CONFIG: fleet, config.SIM_FAULTS: {"faults": []}}))
    evaluator = Recorder({"invoke": {"StatusCode": 202}})
    firehose = Recorder({"put_record_batch": {"FailedPutCount": 0, "RequestResponses": [{"RecordId": "offline"}]}})
    targets = {"AF_HEALTH_EVALUATOR_FUNCTION": FUNCTION, "AF_FIREHOSE_STREAM": STREAM}
    with patch.dict("os.environ", targets):
        run(store, Publisher("direct", {"lambda": evaluator, "firehose": firehose}), fast_interval=0)

    (receipt,) = evaluator.of("invoke")
    (batch,) = firehose.of("put_record_batch")
    (record,) = batch["Records"]
    payload = json.loads(receipt["Payload"])
    if receipt["InvocationType"] != "Event" or json.loads(record["Data"]) != payload:
        raise ValueError("The two simulator destinations must receive the same reading.")
    validate_reading(payload)
    catalog = json.dumps(fleet, ensure_ascii=False, separators=(",", ":")).encode()
    if len(catalog) > 4096:
        raise ValueError("The isolated fixture exceeds the SSM Standard byte limit.")
    case = parent / run_id.hex
    case.mkdir(parents=True, exist_ok=False)
    (case / "fleet.json").write_bytes(catalog)
    (case / "reading.json").write_bytes(receipt["Payload"])
    (case / "firehose-record.ndjson").write_bytes(record["Data"])
    print(f"{payload['machineId']} | {payload['operationId']} | {payload['messageId']} | {len(catalog)} bytes of fleet")
    return case


if __name__ == "__main__":
    print(prepare(ROOT / "build" / "telemetry-cases"))
