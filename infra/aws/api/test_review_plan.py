import json
from copy import deepcopy
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


@pytest.fixture
def hosting_rows():
    app_id = "d2ygcm34t8aku9"
    arn = f"arn:aws:amplify:us-east-1:975050277936:apps/{app_id}"
    values = {
        "aws_amplify_app.web[0]": {
            "id": app_id,
            "arn": arn,
            "name": "af-dev-arquifusion-web",
            "default_domain": f"{app_id}.amplifyapp.com",
            "platform": "WEB",
            "repository": None,
            "iam_service_role_arn": None,
            "enable_branch_auto_build": False,
            "enable_auto_branch_creation": False,
            "enable_branch_auto_deletion": False,
            "tags_all": {"project": "arquifusion", "env": "dev", "owner": "rol1"},
        },
        "aws_amplify_branch.demo[0]": {
            "app_id": app_id,
            "arn": f"{arn}/branches/demo",
            "branch_name": "demo",
            "stage": "DEVELOPMENT",
            "enable_auto_build": False,
            "enable_pull_request_preview": False,
        },
    }
    return {
        key: {"actions": ["no-op"], "before": deepcopy(value), "after": value, "after_unknown": {}}
        for key, value in values.items()
    }


def test_preserved_hosting_matches_the_reviewed_owner_and_origin(hosting_rows):
    variables = {"expected_account_id": "975050277936"}
    plan.check_hosting(hosting_rows, variables, origin="https://demo.d2ygcm34t8aku9.amplifyapp.com")
    with pytest.raises(ValueError, match="origin differs"):
        plan.check_hosting(hosting_rows, variables, origin="http://localhost:5173")
    with pytest.raises(ValueError, match="Unexpected existing Amplify app"):
        plan.check_hosting(hosting_rows, {"expected_account_id": "111111111111"})


def test_hosting_refresh_accepts_only_empty_post_creation_normalization(hosting_rows):
    normalizations = {
        "aws_amplify_app.web[0]": {"auto_branch_creation_patterns": [], "environment_variables": {}, "tags": {}},
        "aws_amplify_branch.demo[0]": {"environment_variables": {}, "tags": {}},
    }
    data = {
        "resource_drift": [
            {
                "address": address,
                "change": {"before": dict.fromkeys(fields), "after": fields},
            }
            for address, fields in normalizations.items()
        ]
    }
    assert plan.check_refresh(data, hosting_rows) == {
        address: sorted(fields) for address, fields in normalizations.items()
    }


@pytest.mark.parametrize(
    ("address", "field", "value"),
    [
        ("aws_amplify_app.web[0]", "environment_variables", {"VITE_API_MODE": "live"}),
        ("aws_amplify_branch.demo[0]", "environment_variables", {"VITE_API_MODE": "live"}),
        ("aws_amplify_app.web[0]", "auto_branch_creation_patterns", ["*"]),
        ("aws_amplify_branch.demo[0]", "auto_branch_creation_patterns", []),
        ("aws_lambda_function.health_evaluator", "environment_variables", {}),
        ("aws_lambda_function.health_evaluator", "auto_branch_creation_patterns", []),
    ],
)
def test_hosting_refresh_rejects_real_values_and_other_resource_exceptions(hosting_rows, address, field, value):
    rows = hosting_rows | {"aws_lambda_function.health_evaluator": {"actions": ["no-op"]}}
    data = {"resource_drift": [{"address": address, "change": {"before": {field: None}, "after": {field: value}}}]}
    with pytest.raises(ValueError, match="Unexpected refresh"):
        plan.check_refresh(data, rows)


@pytest.mark.parametrize("actions", [["create"], ["update"], ["delete"], ["delete", "create"]])
def test_preserved_hosting_rejects_any_mutation(hosting_rows, actions):
    hosting_rows["aws_amplify_app.web[0]"]["actions"] = actions
    with pytest.raises(ValueError, match="hosting must remain no-op"):
        plan.check_hosting(hosting_rows, {"expected_account_id": "975050277936"})


@pytest.mark.parametrize(
    ("resource", "field", "value"),
    [
        ("aws_amplify_app.web[0]", "name", "other-project"),
        ("aws_amplify_app.web[0]", "repository", "https://github.com/other/project"),
        ("aws_amplify_app.web[0]", "enable_auto_branch_creation", True),
        ("aws_amplify_app.web[0]", "tags_all", {"project": "other"}),
        ("aws_amplify_branch.demo[0]", "app_id", "other-app"),
        ("aws_amplify_branch.demo[0]", "enable_auto_build", True),
        ("aws_amplify_branch.demo[0]", "enable_pull_request_preview", True),
    ],
)
def test_preserved_hosting_rejects_other_resources_and_automation(hosting_rows, resource, field, value):
    hosting_rows[resource]["after"][field] = value
    with pytest.raises(ValueError, match="Unexpected existing Amplify"):
        plan.check_hosting(hosting_rows, {"expected_account_id": "975050277936"})


def base_plan(rows, *, hosting):
    variables = {
        "enable_incident_api": False,
        "open_incident_api_endpoint": False,
        "enable_fleet_read_routes": False,
        "prepare_manual_web_hosting": hosting,
        "aws_profile": "default",
        "region": "us-east-1",
        "env": "dev",
        "expected_account_id": "975050277936",
    }
    return {
        "complete": True,
        "variables": {key: {"value": value} for key, value in variables.items()},
        "resource_changes": [{"address": key, "mode": "managed", "change": value} for key, value in rows.items()],
    }


def test_29_resource_mode_is_explicit_and_cannot_modify_the_original_base(monkeypatch, hosting_rows):
    monkeypatch.setattr(plan, "check_existing", lambda rows, variables, root: {})
    rows = {key: {"actions": ["no-op"]} for key in plan.BASE} | hosting_rows
    data = base_plan(rows, hosting=True)
    with pytest.raises(ValueError, match="prepare_manual_web_hosting"):
        plan.review(data, enabled=False)
    summary = plan.review(data, enabled=False, existing_hosting=True)
    assert (summary["add"], summary["change"], summary["destroy"], summary["noOp"]) == (0, 0, 0, 29)
    rows["aws_lambda_function.health_evaluator"]["actions"] = ["update"]
    with pytest.raises(ValueError, match="Existing base must remain no-op"):
        plan.review(data, enabled=False, existing_hosting=True)


def test_29_resource_mode_rejects_missing_and_unreviewed_addresses(hosting_rows):
    rows = {key: {"actions": ["no-op"]} for key in plan.BASE} | hosting_rows
    del rows["aws_amplify_branch.demo[0]"]
    with pytest.raises(ValueError, match="Unexpected/missing managed addresses"):
        plan.review(base_plan(rows, hosting=True), enabled=False, existing_hosting=True)
    rows["aws_s3_bucket.other_project"] = {"actions": ["no-op"]}
    with pytest.raises(ValueError, match="Unexpected/missing managed addresses"):
        plan.review(base_plan(rows, hosting=True), enabled=False, existing_hosting=True)


def test_27_resource_mode_preserves_historical_review(monkeypatch):
    monkeypatch.setattr(plan, "check_existing", lambda rows, variables, root: {})
    rows = {key: {"actions": ["no-op"]} for key in plan.BASE}
    summary = plan.review(base_plan(rows, hosting=False), enabled=False)
    assert summary["noOp"] == 27
    assert summary["existingHostingPreserved"] is False


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
