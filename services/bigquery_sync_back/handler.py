"""Return one scoped BigQuery KPI to Academy. The previous bulk projection remains an explicit legacy mode."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import boto3
from af_shared import ddb, keys
from aws_lambda_powertools import Logger
from google.auth import aws
from google.cloud import bigquery

logger = Logger(service="bigquery-sync-back")

Fetch = Callable[[str], Iterable[dict[str, Any]]]

SELECTED_KPI_SQL = """
WITH scope AS (
  SELECT machineId AS machine_id, ANY_VALUE(model) AS model
  FROM `arquifusion.af_lake.telemetry`
  WHERE machineId = @machine_id AND DATE(timestamp) = @kpi_date
  GROUP BY machineId
  HAVING COUNTIF(operationId IS NULL OR operationId != @operation_id OR model IS NULL) = 0
    AND COUNT(DISTINCT model) = 1
)
SELECT k.*, @operation_id AS operation_id, c.cluster_id, c.cluster_label,
       t.trend_pct_per_week, t.progressive_increase
FROM `arquifusion.af_out.daily_kpis` AS k
JOIN scope USING (machine_id, model)
LEFT JOIN `arquifusion.af_out.machine_clusters` AS c USING (machine_id)
LEFT JOIN `arquifusion.af_out.fuel_trends` AS t USING (machine_id)
WHERE k.machine_id = @machine_id AND k.date = @kpi_date
LIMIT 2
"""

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
    if type(days) is not int or days < 0:
        raise ValueError("Expected a nonnegative lookback in days")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "kpis": write(table, kpi_items(fetch(KPI_SQL.format(dataset=dataset, days=days)), operation_id)),
        "baselines": write(table, baseline_items(fetch(BASELINE_SQL.format(dataset=dataset)), now)),
        "forecasts": write(table, forecast_items(fetch(FORECAST_SQL.format(dataset=dataset)))),
    }


def wif_client():
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path != "/opt/arquifusion/gcp-wif.json" or os.getenv("GCP_PROJECT_ID") != "arquifusion":
        raise ValueError("Configure the approved ArquiFusion AWS federation layer and project")
    credentials = aws.Credentials.from_file(path, scopes=["https://www.googleapis.com/auth/bigquery"])
    return bigquery.Client(project="arquifusion", credentials=credentials, location="US")


def bigquery_fetcher() -> Fetch:
    auth = os.getenv("AF_SYNC_AUTH_MODE", "wif")
    if auth == "wif":
        client = wif_client()
        return lambda sql: (dict(row.items()) for row in client.query(sql, location="US").result())
    if auth != "legacy_secret" or os.getenv("AF_SYNC_MODE") != "legacy":
        raise ValueError("Unknown or disallowed legacy authentication mode")
    from google.oauth2 import service_account

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["AF_GCP_SECRET_ID"])
    info = json.loads(secret["SecretString"])
    if info.get("project_id") != "arquifusion" or os.getenv("GCP_PROJECT_ID") != "arquifusion":
        raise ValueError("Unexpected service-account project")
    client = bigquery.Client(
        project="arquifusion",
        credentials=service_account.Credentials.from_service_account_info(info),
        location="US",
    )
    return lambda sql: (dict(row.items()) for row in client.query(sql).result())


def validate_scope(machine_id, operation_id, kpi_date):
    if type(kpi_date) is not date or not isinstance(machine_id, str) or not isinstance(operation_id, str):
        raise ValueError("Invalid KPI selection")
    if machine_id == "SYN-9001" and operation_id == "AF-PARQUET-SMOKE":
        return
    if re.fullmatch(r"ENS-[0-9]{4}", machine_id) and operation_id == machine_id.replace("ENS-", "ENSAYO-"):
        return
    if re.fullmatch(r"TL[A-Z]{3}-[0-9]{4}", machine_id) and re.fullmatch(r"AF-LAKE-TEST-[A-F0-9]{32}", operation_id):
        return
    raise ValueError("KPI selection is outside the synthetic ArquiFusion rehearsal")


def return_selected_kpi(client, table, *, machine_id: str, operation_id: str, kpi_date: date):
    validate_scope(machine_id, operation_id, kpi_date)
    scoped_table = table.name == "af-dev-arquifusion" or (
        table.name == os.getenv("AF_TABLE_ARN")
        and re.fullmatch(r"arn:aws:dynamodb:us-east-1:[0-9]{12}:table/af-dev-arquifusion", table.name)
    )
    if client.project != "arquifusion" or not scoped_table:
        raise ValueError("Unexpected BigQuery project or DynamoDB table")
    if table.meta.client.meta.region_name != "us-east-1":
        raise ValueError("Unexpected DynamoDB region")
    config = bigquery.QueryJobConfig(
        use_legacy_sql=False,
        query_parameters=[
            bigquery.ScalarQueryParameter("machine_id", "STRING", machine_id),
            bigquery.ScalarQueryParameter("operation_id", "STRING", operation_id),
            bigquery.ScalarQueryParameter("kpi_date", "DATE", kpi_date),
        ],
    )
    rows = list(client.query(SELECTED_KPI_SQL, job_config=config, location="US").result(timeout=45))
    if not rows:
        return {"kpis": 0, "status": "not_found"}
    if len(rows) != 1:
        raise ValueError("Ambiguous KPI for the selected machine and day")
    row = dict(rows[0].items())
    if (row.get("machine_id"), row.get("operation_id"), str(row.get("date"))) != (
        machine_id,
        operation_id,
        kpi_date.isoformat(),
    ) or not row.get("model"):
        raise ValueError("BigQuery result differs from the manual KPI selection")
    item = to_ddb(kpi_items([row], operation_id)[0] | {"operationId": operation_id})
    profile_key = keys.machine_profile(machine_id)
    try:
        ddb.transact_write(
            table,
            [
                {
                    "ConditionCheck": {
                        "Key": profile_key,
                        "ConditionExpression": "operationId = :op AND #model = :model",
                        "ExpressionAttributeNames": {"#model": "model"},
                        "ExpressionAttributeValues": {":op": operation_id, ":model": row["model"]},
                    }
                },
                {"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(PK)"}},
            ],
        )
    except table.meta.client.exceptions.TransactionCanceledException as exc:
        if not ddb.is_transaction_conditional_failure(exc):
            raise
        # An earlier successful transaction may have lost its response. Read both keys in one consistent snapshot.
        target_key = {"PK": item["PK"], "SK": item["SK"]}
        result = table.meta.client.transact_get_items(
            TransactItems=[{"Get": {"TableName": table.name, "Key": key}} for key in (profile_key, target_key)]
        )["Responses"]
        profile, previous = (response.get("Item", {}) for response in result)
        if profile.get("operationId") != operation_id or profile.get("model") != row["model"]:
            raise ValueError("Machine profile does not own the selected operation and model") from None
        if previous != item:
            raise ValueError("Existing KPI differs from the selected result") from None
        return {"kpis": 0, "status": "already_present"}
    return {"kpis": 1, "status": "written"}


def selected_handler(event, context):
    expected = {
        "machineId": os.environ["AF_SYNC_MACHINE_ID"],
        "operationId": os.environ["AF_SYNC_OPERATION_ID"],
        "date": os.environ["AF_SYNC_DATE"],
    }
    if event != expected:
        raise ValueError("Invocation differs from the configured manual scope")
    day = date.fromisoformat(expected["date"])
    if day.isoformat() != expected["date"]:
        raise ValueError("Expected an ISO date in the configured manual scope")
    validate_scope(expected["machineId"], expected["operationId"], day)
    function = re.fullmatch(
        r"arn:aws:lambda:us-east-1:(\d{12}):function:af-dev-bigquery-sync-back(?::[^:]+)?", context.invoked_function_arn
    )
    arn = os.environ["AF_TABLE_ARN"]
    if not function or arn != f"arn:aws:dynamodb:us-east-1:{function[1]}:table/af-dev-arquifusion":
        raise ValueError("Unexpected Academy account, function or table ARN")
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(arn)
    return return_selected_kpi(
        wif_client(), table, machine_id=expected["machineId"], operation_id=expected["operationId"], kpi_date=day
    )


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: object) -> dict:
    if os.getenv("AF_SYNC_MODE") != "legacy":
        return selected_handler(event, context)
    if not isinstance(event, dict) or event.keys() - {"days"}:
        raise ValueError("The legacy bulk invocation accepts only its days argument")
    if os.environ["AF_TABLE_NAME"] != "af-dev-arquifusion" or os.environ.get("GCP_PROJECT_ID") != "arquifusion":
        raise ValueError("Unexpected legacy sync destination")
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(os.environ["AF_TABLE_NAME"])
    counts = sync(
        bigquery_fetcher(),
        table,
        dataset="arquifusion.af_out",
        operation_id=os.environ.get("AF_OPERATION_ID", "MINA-NORTE"),
        days=int(event.get("days", os.environ.get("AF_SYNC_DAYS", "7"))),
    )
    logger.info("Sync finished", extra=counts)
    return counts
