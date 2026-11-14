"""af-dev-simulator: publishes fleet telemetry every minute; machines with an injected fault every ~10 s.

Trigger: EventBridge Scheduler (rate 1 minute). Lambda timeout: 60 s.
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
from fleet_sim import active_faults, simulate, topic

logger = get_logger("simulator")
_store = config.ParamStore()
_clients: dict[str, Any] = {}


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
                    records = [{"Data": (json.dumps(m) + "\n").encode()} for m in messages[i : i + 500]]
                    self.clients["firehose"].put_record_batch(DeliveryStreamName=stream, Records=records)
            return
        for m in messages:
            self.clients["iot-data"].publish(topic=topic(m), qos=1, payload=json.dumps(m).encode())


def _aws_clients() -> dict[str, Any]:
    if not _clients:
        import boto3

        endpoint = os.environ.get("AF_IOT_ENDPOINT")
        if not endpoint and os.environ.get("AF_INGEST_MODE", "iot") == "iot":
            endpoint = boto3.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        if endpoint:
            _clients["iot-data"] = boto3.client("iot-data", endpoint_url=f"https://{endpoint}")
        _clients["lambda"] = boto3.client("lambda")
        _clients["firehose"] = boto3.client("firehose")
    return _clients


def run(
    store: config.ParamStore,
    publisher: Publisher,
    *,
    fast_only: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    budget_seconds: float = 50.0,
) -> dict[str, int]:
    cfg = FleetConfig.model_validate(store.get_json(config.FLEET_CONFIG))
    faults = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
    start = clock()
    published = 0

    if not fast_only:
        batch = [simulate(cfg, m, start, faults) for m in cfg.machines]
        publisher.publish(batch)
        published += len(batch)

    interval = cfg.demo.fast_interval_seconds if cfg.demo else 0
    rounds = 0
    while interval and (clock() - start).total_seconds() + interval <= budget_seconds:
        sleep(interval)
        now = clock()
        faults = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
        hot = [m for m in cfg.machines if active_faults(faults, m.machine_id, now)]
        if not hot:
            break
        batch = [simulate(cfg, m, now, faults) for m in hot]
        publisher.publish(batch)
        published += len(batch)
        rounds += 1
    logger.info("telemetry published", published=published, fastRounds=rounds, machines=len(cfg.machines))
    return {"published": published, "fastRounds": rounds}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    remaining = getattr(context, "get_remaining_time_in_millis", lambda: 60_000)() / 1000
    publisher = Publisher(os.environ.get("AF_INGEST_MODE", "iot"), _aws_clients())
    return run(_store, publisher, fast_only=bool((event or {}).get("fastOnly")), budget_seconds=max(remaining - 8, 0))
