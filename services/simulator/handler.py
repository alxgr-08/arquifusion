"""af-dev-simulator: publishes fleet telemetry every minute; machines with an injected fault every ~10 s.

Trigger: EventBridge Scheduler (rate 1 minute). Lambda timeout: 60 s.
Env: AF_SIM_RAMP_SECONDS (default 300: a fault reaches full strength in 5 min), AF_SIM_FAST_INTERVAL_SECONDS
(default 15: machines with an active fault report every 15 s inside the minute; 0 = only once per minute).
Ingest modes (env AF_INGEST_MODE):
  iot     (default) publish to AWS IoT Core topic arquifusion/{op}/{machineId}/telemetry
  direct  plan B if IoT Core is blocked: invoke health-evaluator async + put to Firehose
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from af_shared import config
from af_shared.fleet import FleetConfig
from af_shared.log import get_logger
from botocore.exceptions import BotoCoreError, ClientError
from fleet_sim import active_faults, simulate, topic

logger = get_logger("simulator")
_store = config.ParamStore()
_clients: dict[str, Any] = {}


class FirehosePublishError(RuntimeError):
    """Individual records could not be acknowledged by Firehose."""


def _failed_records(response: dict, pending: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    results = response.get("RequestResponses") if isinstance(response, dict) else None
    if isinstance(results, list) and len(results) == len(pending):
        failed = []
        for record, result in zip(pending, results, strict=True):
            if not isinstance(result, dict):
                break
            record_id, error = result.get("RecordId"), result.get("ErrorCode")
            if isinstance(record_id, str) and record_id and error is None and result.get("ErrorMessage") is None:
                continue
            if isinstance(error, str) and error and record_id is None:
                failed.append(record)
                continue
            break
        else:
            count = response.get("FailedPutCount")
            if type(count) is int and count == len(failed):
                return failed
    ids = ", ".join(message_id for message_id, _ in pending)
    raise FirehosePublishError(f"Ambiguous Firehose acknowledgement. Reconcile messageIds: {ids}")


def _publish_firehose(client: Any, stream: str, messages: list[dict[str, Any]]) -> None:
    pending = [(m["messageId"], (json.dumps(m) + "\n").encode()) for m in messages]
    for delay in (0, 0.25, 0.5):
        if delay:
            time.sleep(delay)
        records = [{"Data": data} for _, data in pending]
        try:
            response = client.put_record_batch(DeliveryStreamName=stream, Records=records)
        except (BotoCoreError, ClientError, TimeoutError, ConnectionError) as exc:
            ids = ", ".join(message_id for message_id, _ in pending)
            raise FirehosePublishError(f"Firehose request unconfirmed. Reconcile messageIds: {ids}") from exc
        pending = _failed_records(response, pending)
        if not pending:
            return
    ids = ", ".join(message_id for message_id, _ in pending)
    raise FirehosePublishError(f"Firehose rejected these messageIds after three attempts: {ids}")


class Publisher:
    def __init__(self, mode: str, clients: dict[str, Any]) -> None:
        self.mode = mode
        self.clients = clients

    def publish(self, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        if self.mode == "direct":
            fn = os.environ["AF_HEALTH_EVALUATOR_FUNCTION"]
            for m in messages:
                self.clients["lambda"].invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps(m).encode())
            stream = os.environ.get("AF_FIREHOSE_STREAM")
            if stream:
                for i in range(0, len(messages), 500):
                    try:
                        _publish_firehose(self.clients["firehose"], stream, messages[i : i + 500])
                    except FirehosePublishError as exc:
                        unsent = [m["messageId"] for m in messages[i + 500 :]]
                        if unsent:
                            raise FirehosePublishError(f"{exc}. Never sent to Firehose: {', '.join(unsent)}") from exc
                        raise
            return
        for m in messages:
            self.clients["iot-data"].publish(topic=topic(m), qos=1, payload=json.dumps(m).encode())


def _aws_clients() -> dict[str, Any]:
    if not _clients:
        import boto3
        from botocore.config import Config

        endpoint = os.environ.get("AF_IOT_ENDPOINT")
        if not endpoint and os.environ.get("AF_INGEST_MODE", "iot") == "iot":
            endpoint = boto3.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        if endpoint:
            _clients["iot-data"] = boto3.client("iot-data", endpoint_url=f"https://{endpoint}")
        # An unacknowledged POST can already have succeeded. Only retry explicit per-record failures.
        direct_config = Config(retries={"mode": "standard", "total_max_attempts": 1})
        _clients["lambda"] = boto3.client("lambda", config=direct_config)
        _clients["firehose"] = boto3.client("firehose", config=direct_config)
    return _clients


def run(
    store: config.ParamStore,
    publisher: Publisher,
    *,
    fast_only: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    budget_seconds: float = 50.0,
    ramp_seconds: float = 300,
    fast_interval: int = 15,
) -> dict[str, int]:
    cfg = FleetConfig.model_validate(store.get_json(config.FLEET_CONFIG))
    faults = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
    start = clock()
    published = 0

    if not fast_only:
        batch = [simulate(cfg, m, start, faults, ramp_seconds) for m in cfg.machines]
        publisher.publish(batch)
        published += len(batch)

    interval = fast_interval
    rounds = 0
    while interval and (clock() - start).total_seconds() + interval <= budget_seconds:
        sleep(interval)
        now = clock()
        faults = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
        hot = [m for m in cfg.machines if active_faults(faults, m.machine_id, now)]
        if not hot:
            break
        batch = [simulate(cfg, m, now, faults, ramp_seconds) for m in hot]
        publisher.publish(batch)
        published += len(batch)
        rounds += 1
    logger.info("telemetry published", published=published, fastRounds=rounds, machines=len(cfg.machines))
    return {"published": published, "fastRounds": rounds}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    remaining = getattr(context, "get_remaining_time_in_millis", lambda: 60_000)() / 1000
    publisher = Publisher(os.environ.get("AF_INGEST_MODE", "iot"), _aws_clients())
    return run(
        _store,
        publisher,
        fast_only=bool((event or {}).get("fastOnly")),
        budget_seconds=max(remaining - 8, 0),
        ramp_seconds=float(os.environ.get("AF_SIM_RAMP_SECONDS", "300")),
        fast_interval=int(os.environ.get("AF_SIM_FAST_INTERVAL_SECONDS", "15")),
    )
