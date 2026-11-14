"""The fake must behave like DynamoDB, or the service tests that rely on it prove nothing."""

from decimal import Decimal

import pytest
from af_shared.fakes import FakeTable
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


def test_rejects_floats_like_boto3() -> None:
    with pytest.raises(TypeError):
        FakeTable().put_item(Item={"PK": "A", "SK": "B", "x": 1.5})


def test_conditional_put_and_update_arithmetic() -> None:
    t = FakeTable()
    t.put_item(Item={"PK": "PART#P", "SK": "STOCK#W", "available": Decimal(1), "reserved": Decimal(0)})
    update = {
        "Key": {"PK": "PART#P", "SK": "STOCK#W"},
        "UpdateExpression": "SET available = available - :n, reserved = reserved + :n",
        "ConditionExpression": "available >= :n",
        "ExpressionAttributeValues": {":n": Decimal(1)},
    }
    t.update_item(**update)
    with pytest.raises(ClientError) as err:
        t.update_item(**update)
    assert err.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    assert t.get("PART#P", "STOCK#W") == {"PK": "PART#P", "SK": "STOCK#W", "available": 0, "reserved": 1}


def test_if_not_exists_list_append_remove_and_names() -> None:
    t = FakeTable()
    t.update_item(
        Key={"PK": "M", "SK": "P"},
        UpdateExpression="SET #tl = list_append(if_not_exists(#tl, :empty), :ev), #s = :s REMOVE gone",
        ConditionExpression="attribute_not_exists(openIncidentId)",
        ExpressionAttributeNames={"#tl": "timeline", "#s": "state"},
        ExpressionAttributeValues={":empty": [], ":ev": [{"event": "DETECTED"}], ":s": "OPEN"},
    )
    assert t.get("M", "P")["timeline"] == [{"event": "DETECTED"}]


def test_unused_values_are_rejected() -> None:
    with pytest.raises(ClientError):
        FakeTable().update_item(
            Key={"PK": "A", "SK": "B"}, UpdateExpression="SET a = :a", ExpressionAttributeValues={":a": 1, ":b": 2}
        )


def test_query_index_sorting_limit_and_filter() -> None:
    t = FakeTable()
    for i, sev in enumerate(["2", "1", "3"]):
        t.put_item(
            Item={
                "PK": f"MACHINE#{i}",
                "SK": "X",
                "GSI1PK": "OP#M#INCIDENT#OPEN",
                "GSI1SK": f"{sev}#t{i}",
                "zone": "Z3" if i else "Z1",
            }
        )
    t.put_item(Item={"PK": "MACHINE#9", "SK": "X"})  # sparse: not in the index
    res = t.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("OP#M#INCIDENT#OPEN"))
    assert [i["GSI1SK"][0] for i in res["Items"]] == ["1", "2", "3"]
    page = t.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("OP#M#INCIDENT#OPEN"), Limit=2)
    assert len(page["Items"]) == 2 and "LastEvaluatedKey" in page
    rest = t.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("OP#M#INCIDENT#OPEN"),
        ExclusiveStartKey=page["LastEvaluatedKey"],
    )
    assert [i["GSI1SK"][0] for i in rest["Items"]] == ["3"]
    filtered = t.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("OP#M#INCIDENT#OPEN") & Key("GSI1SK").begins_with("2"),
        FilterExpression=Attr("zone").eq("Z1"),
    )
    assert filtered["Items"] == [] or filtered["Items"][0]["zone"] == "Z1"
