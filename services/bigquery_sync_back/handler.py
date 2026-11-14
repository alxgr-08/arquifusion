"""af-{env}-bigquery-sync-back: BigQuery af_out.* -> DynamoDB (KPI#, BASELINE, FORECAST#). Daily, EventBridge Scheduler.

GCP -> AWS direction of the multi-cloud flow. The GCP service-account key is read from Secrets Manager.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import boto3
from af_shared import keys
from aws_lambda_powertools import Logger

logger = Logger(service="bigquery-sync-back")

Fetch = Callable[[str], Iterable[dict[str, Any]]]

KPI_SQL = """
SELECT k.*, c.cluster_id, c.cluster_label, t.trend_pct_per_week, t.progressive_increase
FROM `{dataset}.daily_kpis` AS k
LEFT JOIN `{dataset}.machine_clusters` AS c USING (machine_id)
LEFT JOIN `{dataset}.fuel_trends` AS t USING (machine_id)
WHERE k.date >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)
"""
BASELINE_SQL = "SELECT model, metric, mean, std, n FROM `{dataset}.model_baselines`"
FORECAST_SQL = """
SELECT f.part_id, f.warehouse_id, f.iso_week, f.forecast_qty, f.forecast_qty_upper,
       i.irregular, i.coefficient_of_variation
FROM `{dataset}.parts_forecast` AS f
LEFT JOIN `{dataset}.parts_irregular` AS i USING (part_id, warehouse_id)
"""


def to_ddb(value: Any) -> Any:
    """JSON-safe -> DynamoDB-safe (floats as Decimal, dates as ISO strings, None dropped)."""
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [to_ddb(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float | int | Decimal):
        return Decimal(str(value))
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def kpi_items(rows: Iterable[dict[str, Any]], operation_id: str) -> list[dict[str, Any]]:
    items = []
    for r in rows:
        day = r["date"].isoformat() if isinstance(r["date"], date) else str(r["date"])
        items.append(
            keys.kpi_item(r["machine_id"], operation_id, day)
            | {
                "machineId": r["machine_id"],
                "model": r["model"],
                "date": day,
                "utilizationPct": r["utilization_pct"],
                "operatingHours": r["operating_hours"],
                "idleHours": r["idle_hours"],
                "downHours": r.get("down_hours"),
                "overuseHours": r["overuse_hours"],
                "tonsMoved": r["tons_moved"],
                "fuelLiters": r.get("fuel_liters"),
                "fuelLitersPerTon": r.get("fuel_liters_per_ton"),
                "fuelLitersPerTonModel": r.get("fuel_liters_per_ton_model"),
                "costPerHour": r["cost_per_hour"],
                "fuelAnomaly": bool(r.get("fuel_anomaly")),
                "clusterId": r.get("cluster_id"),
                "clusterLabel": r.get("cluster_label"),
                "fuelTrendPctPerWeek": r.get("trend_pct_per_week"),
                "progressiveFuelIncrease": r.get("progressive_increase"),
                "source": "bigquery",
            }
        )
    return items


def baseline_items(rows: Iterable[dict[str, Any]], computed_at: str) -> list[dict[str, Any]]:
    """One MODEL#{model} BASELINE item with metrics: {metric: {mean, std, n}} (read by health-evaluator)."""
    by_model: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_model.setdefault(r["model"], {})[r["metric"]] = {"mean": r["mean"], "std": r["std"], "n": r["n"]}
    return [
        keys.model_baseline_keys(model) | {"model": model, "metrics": metrics, "computedAt": computed_at}
        for model, metrics in by_model.items()
    ]


def forecast_items(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        keys.forecast_keys(r["part_id"], r["warehouse_id"], r["iso_week"])
        | {
            "partId": r["part_id"],
            "warehouseId": r["warehouse_id"],
            "isoWeek": r["iso_week"],
            "forecastQty": r["forecast_qty"],
            "forecastQtyUpper": r.get("forecast_qty_upper"),
            "irregular": bool(r.get("irregular")),
            "coefficientOfVariation": r.get("coefficient_of_variation"),
        }
        for r in rows
    ]


def write(table: Any, items: list[dict[str, Any]]) -> int:
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
        for item in items:
            batch.put_item(Item=to_ddb(item))
    return len(items)


def sync(fetch: Fetch, table: Any, dataset: str, operation_id: str, days: int) -> dict[str, int]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "kpis": write(table, kpi_items(fetch(KPI_SQL.format(dataset=dataset, days=days)), operation_id)),
        "baselines": write(table, baseline_items(fetch(BASELINE_SQL.format(dataset=dataset)), now)),
        "forecasts": write(table, forecast_items(fetch(FORECAST_SQL.format(dataset=dataset)))),
    }


def bigquery_fetcher() -> Fetch:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    secret = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ.get("AF_GCP_SECRET_ID", "af/gcp/service-account")
    )
    info = json.loads(secret["SecretString"])
    client = bigquery.Client(
        project=os.environ.get("GCP_PROJECT_ID") or info["project_id"],
        credentials=service_account.Credentials.from_service_account_info(info),
        location=os.environ.get("BQ_LOCATION", "US"),
    )
    return lambda sql: (dict(row.items()) for row in client.query(sql).result())


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    project = os.environ.get("GCP_PROJECT_ID")
    dataset = f"{project}.af_out" if project else "af_out"
    table = boto3.resource("dynamodb").Table(os.environ["AF_TABLE_NAME"])
    counts = sync(
        bigquery_fetcher(),
        table,
        dataset=dataset,
        operation_id=os.environ.get("AF_OPERATION_ID", "MINA-NORTE"),
        days=int(event.get("days", os.environ.get("AF_SYNC_DAYS", "7"))),
    )
    logger.info("Sync finished", extra=counts)
    return counts
