import json
from zipfile import ZipFile

import pytest
import review_plan as plan


def test_configuration_snapshot_is_bound_to_current_root(tmp_path):
    root = tmp_path / "aws"
    root.mkdir()
    (root / "api.tf").write_bytes(b"# current\n")
    saved = tmp_path / "plan"
    with ZipFile(saved, "w") as archive:
        archive.writestr("tfconfig/m-/api.tf", b"# current\r\n")
    plan.check_plan_config(saved, root)

    (root / "api.tf").write_bytes(b"# changed\n")
    with pytest.raises(ValueError, match="predates current"):
        plan.check_plan_config(saved, root)
    (root / "bridge.tf").touch()
    with pytest.raises(ValueError, match="Different Terraform root"):
        plan.check_plan_config(saved, root)


def test_configuration_snapshot_includes_local_modules(tmp_path):
    root = tmp_path / "aws"
    child = root / "telemetry"
    child.mkdir(parents=True)
    (root / "main.tf").write_bytes(b"# root\n")
    (child / "main.tf").write_bytes(b"# module\n")
    saved = tmp_path / "plan"
    with ZipFile(saved, "w") as archive:
        archive.writestr(
            "tfconfig/modules.json", json.dumps([{"Key": "", "Dir": "."}, {"Key": "telemetry", "Dir": "telemetry"}])
        )
        archive.writestr("tfconfig/m-/main.tf", b"# root\n")
        archive.writestr("tfconfig/m-telemetry/main.tf", b"# module\n")
    plan.check_plan_config(saved, root)
    (child / "main.tf").write_bytes(b"# changed\n")
    with pytest.raises(ValueError, match="predates current"):
        plan.check_plan_config(saved, root)


def test_api_zip_must_match_owner_sources_and_linux_runtime(tmp_path):
    modules = {
        "handler.py": "services/api/handler.py",
        "routes.py": "services/api/routes.py",
        "af_shared/http.py": "shared/af_shared/http.py",
        "af_shared/py.typed": "shared/af_shared/py.typed",
    }
    for member, source in modules.items():
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(member.encode())
    bundle = tmp_path / "api.zip"
    with ZipFile(bundle, "w") as archive:
        for member in modules:
            archive.writestr(member, member.encode())
        archive.writestr("boto3/__init__.py", b"")
        archive.writestr("pydantic/__init__.py", b"")
    with pytest.raises(ValueError, match="Linux dependencies"):
        plan.check_package(tmp_path, bundle, "api")
    with ZipFile(bundle, "a") as archive:
        archive.writestr("pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so", b"")
    assert plan.check_package(tmp_path, bundle, "api") == plan.file_hash(bundle)
    (tmp_path / modules["handler.py"]).write_bytes(b"new handler")
    with pytest.raises(ValueError, match="Stale handler"):
        plan.check_package(tmp_path, bundle, "api")
    with ZipFile(bundle, "a") as archive:
        archive.writestr("ack_api.py", b"")
    with pytest.raises(ValueError, match="Historical ACK"):
        plan.check_package(tmp_path, bundle, "api")


def test_refresh_only_accepts_the_policy_of_the_separately_managed_dlq():
    policy = {"Version": "2012-10-17", "Statement": []}
    rows = {"aws_sqs_queue_policy.incident_delivery": {"after": {"policy": json.dumps(policy)}}}
    change = {"before": {"policy": None, "tags": None}, "after": {"policy": json.dumps(policy), "tags": {}}}
    address = "aws_sqs_queue.incident_delivery_dlq"
    data = {"resource_drift": [{"address": address, "change": change}]}
    assert plan.check_refresh(data, rows) == {address: ["policy", "tags"]}
    change["after"]["policy"] = "{}"
    with pytest.raises(ValueError, match="Unexpected DLQ policy"):
        plan.check_refresh(data, rows)


def test_historical_and_incomplete_plans_are_rejected_before_package_checks():
    with pytest.raises(ValueError, match="superseded"):
        plan.review({"complete": True, "variables": {}}, enabled=False)
    with pytest.raises(ValueError, match="complete plan"):
        plan.review({"complete": False}, enabled=False)


def test_expected_addresses_include_only_the_two_protected_routes():
    assert len(plan.BASE) == 27
    addresses = plan.api_addresses(plan.ROUTES)
    assert len(addresses) == 11
    assert {a for a in addresses if a.startswith("aws_apigatewayv2_route.")} == {
        'aws_apigatewayv2_route.ack["GET"]',
        'aws_apigatewayv2_route.ack["POST"]',
    }


def test_fleet_plan_adds_only_the_ten_dashboard_reads_and_their_permissions():
    assert len(plan.api_addresses(plan.ROUTES | plan.FLEET_ROUTES)) == 31
    assert set(plan.FLEET_ROUTES.values()) == {
        ("GET", "/operations/{op}/machines"),
        ("GET", "/operations/{op}/incidents"),
        ("GET", "/operations/{op}/technicians"),
        ("GET", "/operations/{op}/kpis"),
        ("GET", "/machines/{id}"),
        ("GET", "/machines/{id}/readings"),
        ("GET", "/machines/{id}/incidents"),
        ("GET", "/machines/{id}/maintenance"),
        ("GET", "/parts"),
        ("GET", "/parts/{partId}/stock"),
    }


def test_review_requires_explicit_intent_to_open_the_endpoint_or_add_routes():
    flags = {
        k: {"value": v}
        for k, v in {
            "enable_incident_api": True,
            "open_incident_api_endpoint": "true",
            "enable_fleet_read_routes": True,
        }.items()
    }
    with pytest.raises(ValueError, match="open_incident_api_endpoint"):
        plan.review({"complete": True, "variables": flags}, enabled=True)
    with pytest.raises(ValueError, match="enable_fleet_read_routes"):
        plan.review({"complete": True, "variables": flags}, enabled=True, endpoint_open=True)


def test_opening_an_existing_closed_endpoint_allows_only_reviewed_changes():
    key = "aws_apigatewayv2_api.ack[0]"
    updated = {
        "actions": ["update"],
        "before": {"disable_execute_api_endpoint": True},
        "after": {"disable_execute_api_endpoint": False},
    }
    rows = {key: updated}
    assert plan.review_api_changes(rows, {key}) == {key: ["disable_execute_api_endpoint"]}
    updated["after"]["target"] = "implicit route"
    with pytest.raises(ValueError, match="Unexpected API fields"):
        plan.review_api_changes(rows, {key})
    updated["actions"] = ["delete", "create"]
    with pytest.raises(ValueError, match="Unexpected API change"):
        plan.review_api_changes(rows, {key})
