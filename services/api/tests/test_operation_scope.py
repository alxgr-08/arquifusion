"""Verify that every returned row belongs to the caller's authorized operation."""

import json

import pytest
from af_shared import grants
from auth import as_user
from test_ack import paused_at_arrival
from test_api import NOW, req


def test_incident_detail_checks_machine_and_incident_ownership(load_handler):
    api = load_handler("api")
    h, incident = paused_at_arrival()
    path = f"/machines/{incident['machineId']}/incidents/{incident['incidentId']}"
    key = {"PK": "MACHINE#" + incident["machineId"], "SK": "PROFILE"}
    original = h.table.get_item(Key=key)["Item"]
    h.table.put_item(Item={**original, "operationId": "MINA-SUR"})
    claims = as_user(h.table, "SUPERVISOR", "MINA-SUR")
    denied = api.route(req("GET", path, claims), h.table, None, NOW)
    assert denied["statusCode"] == 403 and "ETag" not in denied["headers"]
    h.table.delete_item(Key=key)
    assert api.route(req("GET", path, claims), h.table, None, NOW)["statusCode"] == 404


def test_stock_and_forecast_are_restricted_to_the_selected_operations_warehouses(load_handler):
    import json

    api = load_handler("api")
    h, _ = paused_at_arrival()
    pk = "PART#BRG-SPH-22"
    source = h.table.get_item(Key={"PK": pk, "SK": "STOCK#WH-NORTE"})["Item"]
    h.table.put_item(Item={**source, "SK": "STOCK#WH-PRIVATE", "GSI1PK": "WH#WH-PRIVATE", "warehouseId": "WH-PRIVATE"})
    h.table.put_item(Item={"PK": "OP#MINA-SUR", "SK": "META", "warehouses": ["WH-PRIVATE"]})
    claims = as_user(h.table, "MAINT_LEAD", "MINA-NORTE")
    # The principal may read the second operation's fleet, but the stock role applies only in the first.
    h.table.put_item(Item=grants.item(claims["iss"], claims["sub"], "MINA-SUR", "OPERATOR"))
    for path in ("/parts", "/parts/BRG-SPH-22/stock"):
        denied = api.route(req("GET", path, claims, query={"operationId": "MINA-SUR"}), h.table, None, NOW)
        assert denied["statusCode"] == 403
        response = api.route(req("GET", path, claims, query={"operationId": "MINA-NORTE"}), h.table, None, NOW)
        assert response["statusCode"] == 200
        assert "WH-PRIVATE" not in response["body"] and "WH-NORTE" in response["body"]
        assert json.loads(response["body"])


def test_moved_machine_does_not_return_unattributed_or_foreign_history(load_handler):
    import json

    api = load_handler("api")
    h, incident = paused_at_arrival()
    machine = incident["machineId"]
    pk = "MACHINE#" + machine
    h.table.update_item(
        Key={"PK": pk, "SK": "PROFILE"},
        UpdateExpression="SET operationId = :op",
        ExpressionAttributeValues={":op": "MINA-SUR"},
    )
    for minute, op in enumerate((None, "MINA-NORTE", "MINA-SUR")):
        row = {"PK": pk, "SK": f"READING#2026-11-13T15:0{minute}:00Z", "metrics": {}}
        if op:
            row["operationId"] = op
        h.table.put_item(Item=row)
    claims = as_user(h.table, "SUPERVISOR", "MINA-SUR")
    incident_list = api.route(req("GET", f"/machines/{machine}/incidents", claims), h.table, None, NOW)
    assert json.loads(incident_list["body"]) == []
    readings = api.route(req("GET", f"/machines/{machine}/readings", claims), h.table, None, NOW)
    assert [r["timestamp"] for r in json.loads(readings["body"])] == ["2026-11-13T15:02:00Z"]


def test_lambda_requires_authentication_even_if_debug_flag_is_misconfigured(load_handler, monkeypatch):
    h, _ = paused_at_arrival()
    monkeypatch.setenv("AF_AUTH_DISABLED", "true")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "af-test-api")
    result = load_handler("api").route(req("GET", "/operations/MINA-NORTE/machines", None), h.table, None, NOW)
    assert result["statusCode"] == 401


@pytest.mark.parametrize("resource", ["machines", "technicians", "incidents", "kpis"])
@pytest.mark.parametrize("change", ["move", "remove"])
def test_operation_lists_revalidate_eventual_index_rows(load_handler, monkeypatch, resource, change):
    api = load_handler("api")
    h, incident = paused_at_arrival()
    paths = {
        "machines": ("MACHINE#EXC-0042", "PROFILE"),
        "technicians": ("TECH#T-07", "PROFILE"),
        "incidents": ("MACHINE#EXC-0042", incident["incidentSK"]),
        "kpis": ("MACHINE#EXC-0042", "KPI#2026-11-12"),
    }
    pk, sk = paths[resource]
    if resource == "kpis":
        h.table.put_item(
            Item={
                "PK": pk, "SK": sk, "operationId": "MINA-NORTE",
                "GSI1PK": "OP#MINA-NORTE#KPI#2026-11-12", "GSI1SK": "EXC-0042",
            }
        )
    key = {"PK": pk, "SK": sk}
    projection = h.table.get_item(Key=key)["Item"]
    if change == "move":
        h.table.put_item(Item={**projection, "operationId": "MINA-SUR"})
    else:
        h.table.delete_item(Key=key)
    query = h.table.query

    def stale_index(**kwargs):
        return {"Items": [projection]} if kwargs.get("IndexName") == "GSI1" else query(**kwargs)

    monkeypatch.setattr(h.table, "query", stale_index)
    claims = as_user(h.table, "MAINT_LEAD", "MINA-NORTE")
    response = api.route(req("GET", f"/operations/MINA-NORTE/{resource}", claims), h.table, None, NOW)
    assert response["statusCode"] == 200 and json.loads(response["body"]) == []


def test_reading_ranges_order_mixed_timestamp_precision_and_keep_exact_boundaries(load_handler):
    api = load_handler("api")
    h, incident = paused_at_arrival()
    times = [
        "2026-11-13T15:00:00Z",
        "2026-11-13T15:00:00.100000Z",
        "2026-11-13T15:00:00.500000Z",
        "2026-11-13T15:00:00.900000Z",
        "2026-11-13T15:00:01Z",
    ]
    for timestamp in reversed(times):
        h.table.put_item(
            Item={
                "PK": "MACHINE#" + incident["machineId"], "SK": "READING#" + timestamp,
                "operationId": "MINA-NORTE", "metrics": {},
            }
        )
    claims = as_user(h.table, "SUPERVISOR", "MINA-NORTE")
    path = f"/machines/{incident['machineId']}/readings"
    for start, end, expected in [
        ("2026-11-13T15:00:00Z", "2026-11-13T15:00:01Z", times),
        ("2026-11-13T15:00:00.100000Z", "2026-11-13T15:00:00.900000Z", times[1:4]),
        ("2026-11-13T15:00:00Z", "2026-11-13T15:00:00Z", times[:1]),
    ]:
        response = api.route(req("GET", path, claims, query={"from": start, "to": end}), h.table, None, NOW)
        assert response["statusCode"] == 200
        assert [r["timestamp"] for r in json.loads(response["body"])] == expected
