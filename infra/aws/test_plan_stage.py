import copy
import json

import pytest

from infra.aws import plan_stage as stage


def sample(monkeypatch):
    monkeypatch.setattr(stage.review, "check_existing", lambda *args: {})
    monkeypatch.setattr(stage.review, "check_hosting", lambda *args: None)
    rows = {a: {"actions": ["no-op"], "after": {}} for a in stage.review.BASE | stage.review.HOSTING}
    rows["aws_lambda_function.health_evaluator"]["after"]["arn"] = "health-arn"
    rows["aws_dynamodb_table.arquifusion"]["after"]["stream_arn"] = "table-stream"
    rows["aws_lambda_event_source_mapping.anomaly_outbox[0]"] = {
        "actions": ["create"],
        "after": {
            "enabled": False,
            "parallelization_factor": 1,
            "function_response_types": ["ReportBatchItemFailures"],
            "function_name": "health-arn",
            "event_source_arn": "table-stream",
            "filter_criteria": [
                {
                    "filter": [
                        {
                            "pattern": json.dumps(
                                {
                                    "eventName": ["INSERT"],
                                    "dynamodb": {"Keys": {"SK": {"S": [{"prefix": "OUTBOX#ANOMALY#"}]}}},
                                }
                            )
                        }
                    ]
                }
            ],
        },
    }
    return {
        "complete": True,
        "variables": {k: {"value": v} for k, v in stage.options("975050277936", "outbox-disabled").items()},
        "resource_changes": [{"address": a, "mode": "managed", "change": c} for a, c in rows.items()],
    }


def test_stages_never_open_http_or_disable_existing_hosting():
    for name in stage.STAGES:
        flags = stage.options("975050277936", name)
        assert flags["prepare_manual_web_hosting"] is True
        assert flags["open_incident_api_endpoint"] is False
        assert flags["bridge_wif"] is None
        assert flags["incident_test_operation_id"] is None
    assert stage.options("975050277936", "api-closed")["enable_fleet_read_routes"] is True


@pytest.mark.parametrize("account", ["1", "abcdefghijkl", "٩٧٥٠٥٠٢٧٧٩٣٦"])
def test_invalid_accounts_fail_before_commands(account):
    with pytest.raises(ValueError, match="Invalid stage or account"):
        stage.options(account, "base")


def test_only_one_disabled_outbox_is_allowed(monkeypatch):
    result = stage.check_additions(sample(monkeypatch), "outbox-disabled")
    assert (result["add"], result["change"], result["destroy"], result["noOp"]) == (1, 0, 0, 29)


@pytest.mark.parametrize("actions", [["update"], ["delete"], ["delete", "create"]])
def test_every_existing_resource_is_preserved(monkeypatch, actions):
    data = sample(monkeypatch)
    data["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(ValueError, match="Existing resources"):
        stage.check_additions(data, "outbox-disabled")


def test_extra_resource_is_rejected(monkeypatch):
    data = sample(monkeypatch)
    data["resource_changes"].append(
        {"address": "aws_iam_role.unapproved", "mode": "managed", "change": {"actions": ["create"]}}
    )
    with pytest.raises(ValueError, match="Unexpected managed addresses"):
        stage.check_additions(data, "outbox-disabled")


@pytest.mark.parametrize(
    "key,value", [("enabled", True), ("function_name", "another-project"), ("event_source_arn", "another-table")]
)
def test_active_or_unrelated_stream_mapping_is_rejected(monkeypatch, key, value):
    data = sample(monkeypatch)
    data["resource_changes"][-1]["change"]["after"][key] = value
    with pytest.raises(ValueError):
        stage.check_additions(data, "outbox-disabled")


def test_widened_filter_is_rejected(monkeypatch):
    data = copy.deepcopy(sample(monkeypatch))
    data["resource_changes"][-1]["change"]["after"]["filter_criteria"][0]["filter"][0]["pattern"] = "{}"
    with pytest.raises(ValueError, match="Wrong anomaly filter"):
        stage.check_additions(data, "outbox-disabled")
