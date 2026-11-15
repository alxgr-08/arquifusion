"""Review a saved incident API plan locally. Write only addresses, counts and digests."""

import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[3]
AWS = ROOT / "infra" / "aws"
CLIENT = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
DEV_ORIGIN = "http://localhost:5173"
ROUTES = {"GET": ("GET", "/machines/{id}/incidents/{incId}"), "POST": ("POST", "/machines/{id}/incidents/{incId}/ack")}
FLEET_ROUTES = {key: ("GET", f"/operations/{{op}}/{key}") for key in ("machines", "incidents", "technicians", "kpis")}
FLEET_ROUTES.update(
    {
        "machine": ("GET", "/machines/{id}"),
        "readings": ("GET", "/machines/{id}/readings"),
        "machineIncidents": ("GET", "/machines/{id}/incidents"),
        "maintenance": ("GET", "/machines/{id}/maintenance"),
        "parts": ("GET", "/parts"),
        "partStock": ("GET", "/parts/{partId}/stock"),
    }
)
INCIDENT_FUNCTIONS = ("incident_tasks", "assign_technician", "ai_diagnosis", "notify")
BASE = {
    "aws_dynamodb_table.arquifusion",
    "aws_cloudwatch_event_bus.arquifusion",
    "aws_ssm_parameter.health_weights",
    "aws_ssm_parameter.health_thresholds",
    "aws_cloudwatch_log_group.health_evaluator",
    "aws_lambda_function.health_evaluator",
    "aws_sns_topic.incident_notifications",
    "aws_ssm_parameter.incident_topics",
    "aws_ssm_parameter.incident_settings",
    "aws_ssm_parameter.incident_fleet",
    "aws_sfn_state_machine.incident",
    "aws_sfn_state_machine.incident_entry",
    "aws_cloudwatch_event_rule.incident",
    "aws_sqs_queue.incident_delivery_dlq",
    "aws_sqs_queue_policy.incident_delivery",
    "aws_cloudwatch_event_target.incident",
    'aws_cloudwatch_metric_alarm.incident_execution_failures["entry"]',
    'aws_cloudwatch_metric_alarm.incident_execution_failures["workflow"]',
    "aws_cloudwatch_metric_alarm.incident_delivery_dlq",
    *(f'aws_cloudwatch_log_group.incident_lambda["{name}"]' for name in INCIDENT_FUNCTIONS),
    *(f'aws_lambda_function.incident["{name}"]' for name in INCIDENT_FUNCTIONS),
}
API_COMMON = {
    "aws_apigatewayv2_api.ack[0]",
    "aws_apigatewayv2_authorizer.ack_google[0]",
    "aws_apigatewayv2_integration.ack[0]",
    "aws_apigatewayv2_stage.ack[0]",
    "aws_cloudwatch_log_group.ack[0]",
    "aws_cloudwatch_log_group.ack_gateway[0]",
    "aws_lambda_function.ack[0]",
}


def api_addresses(routes):
    return API_COMMON | {
        f'{resource}["{key}"]'
        for key in routes
        for resource in ("aws_apigatewayv2_route.ack", "aws_lambda_permission.ack_gateway")
    }


def require(ok, message):
    if not ok:
        raise ValueError(message)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def has_unknown(value):
    """Terraform marks nested unknown attributes with true leaves in a partial object or list."""
    if isinstance(value, dict):
        return any(has_unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(has_unknown(v) for v in value)
    return value is True


def check_package(root, filename, service):
    """Ensure the ZIP includes the current handler, service modules and shared runtime."""
    shared = root / "shared" / "af_shared"
    source = {f.name: f for f in (root / "services" / service).glob("*.py")}
    source.update({f.relative_to(shared.parent).as_posix(): f for f in [*shared.rglob("*.py"), shared / "py.typed"]})
    if service == "incident_tasks":
        source["incident_catalog.py"] = root / "infra/aws/incident/incident_catalog.py"
    with ZipFile(filename) as package:
        names = set(package.namelist())
        require(len(names) == len(package.infolist()), "Duplicate entries in " + filename.name)
        require(source.keys() <= names and "handler.py" in source, "Missing service sources in " + filename.name)
        runtime = {
            "boto3/__init__.py",
            "pydantic/__init__.py",
            "pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so",
        }
        require(runtime <= names, "Missing CPython 3.12 Linux dependencies in " + filename.name)
        if service == "api":
            require({"ack_api.py", "ack_store.py", "identity_review.py"}.isdisjoint(names), "Historical ACK adapter")
        for name, path in source.items():
            require(package.read(name) == path.read_bytes().replace(b"\r\n", b"\n"), f"Stale {name} in {filename.name}")
    return file_hash(filename)


def check_plan_config(plan, aws=AWS):
    # show -json exposes references only for compound expressions such as source_arn.
    # The local plan also embeds the HCL used to produce those expressions.
    with ZipFile(plan) as archive:
        modules = (
            json.loads(archive.read("tfconfig/modules.json"))
            if "tfconfig/modules.json" in archive.namelist()
            else [{"Key": "", "Dir": "."}]
        )
        expected_files = set()
        for module in modules:
            directory = (aws / module["Dir"]).resolve()
            require(directory.is_relative_to(aws.resolve()), "Module path leaves the reviewed AWS root")
            prefix = f"tfconfig/m-{module['Key']}/"
            paths = [*directory.glob("*.tf"), *directory.glob("*.tf.json")]
            expected = {prefix + path.name for path in paths}
            saved_files = {n for n in archive.namelist() if n.startswith(prefix) and n.endswith((".tf", ".tf.json"))}
            require(saved_files == expected, "Different Terraform root or module configuration files")
            expected_files.update(expected)
            for path in paths:
                saved = archive.read(prefix + path.name).replace(b"\r\n", b"\n")
                require(saved == path.read_bytes().replace(b"\r\n", b"\n"), "Plan predates current " + str(path))
        require(
            expected_files
            == {n for n in archive.namelist() if n.startswith("tfconfig/m-") and n.endswith((".tf", ".tf.json"))},
            "Unreviewed module configuration in the plan",
        )


def check_existing(rows, variables, root):
    account = variables["expected_account_id"]
    table = rows["aws_dynamodb_table.arquifusion"]["after"]
    require(table["arn"] == f"arn:aws:dynamodb:us-east-1:{account}:table/af-dev-arquifusion", "Unexpected base table")
    require(rows["aws_cloudwatch_event_rule.incident"]["after"]["state"] == "DISABLED", "Incident rule is active")
    lambdas = {"aws_lambda_function.health_evaluator": ("health_evaluator", "health-evaluator/health-evaluator.zip")}
    asl = (root / "statemachines/incident.asl.json").read_text(encoding="utf-8")
    for service in INCIDENT_FUNCTIONS:
        suffix = service.replace("_", "-")
        package = "incident-tasks-catalog" if service == "incident_tasks" else suffix
        lambdas[f'aws_lambda_function.incident["{service}"]'] = (service, f"incident/{package}.zip")
        arn = f"arn:aws:lambda:us-east-1:{account}:function:af-dev-{suffix}"
        asl = asl.replace("$" + "{" + service + "_arn}", arn)
    workflow = "aws_sfn_state_machine.incident"
    require(json.loads(rows[workflow]["after"]["definition"]) == json.loads(asl), "Incident ASL differs from checkout")
    for address, (service, filename) in lambdas.items():
        digest = check_package(root, root / "build" / filename, service)
        expected_hash = base64.b64encode(digest).decode()
        require(expected_hash == rows[address]["after"]["source_code_hash"], "Stale ZIP: " + service)

    updates = {}
    for address in BASE:
        change = rows[address]
        if change["actions"] == ["no-op"]:
            continue
        require(change["actions"] == ["update"] and address in {*lambdas, workflow}, "Unexpected change to " + address)
        old, new = change["before"], change["after"]
        fields = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        unknown = {k for k, flag in change["after_unknown"].items() if has_unknown(flag)}
        if address == workflow:
            require(fields == {"definition"} and not unknown, "Unexpected state machine update")
        else:
            require(
                fields <= {"source_code_hash", "last_modified"}
                and "source_code_hash" in fields
                and unknown <= {"last_modified"},
                "Expected only a code update to " + address,
            )
        updates[address] = sorted(fields)
    return dict(sorted(updates.items()))


def check_api(rows, config, variables, origin, root, *, endpoint_open, routes):
    def after(name):
        return rows[name + "[0]"]["after"]

    def linked(name, field, reference):
        refs = config[name]["expressions"][field].get("references", [])
        require(
            any(r == reference or r.startswith(reference + ".") for r in refs),
            f"Unexpected reference in {name}.{field}",
        )

    account = variables["expected_account_id"]
    api = after("aws_apigatewayv2_api.ack")
    require(
        api["protocol_type"] == "HTTP"
        and api["disable_execute_api_endpoint"] is (not endpoint_open)
        and not any(api.get(k) for k in ("target", "body", "route_key")),
        "Unexpected endpoint exposure or implicit routes",
    )
    (cors,) = api["cors_configuration"]
    require(
        variables["ack_frontend_origin"] == origin
        and cors["allow_origins"] == [origin]
        and set(cors["allow_methods"]) == set(ROUTES)
        and {h.lower() for h in cors["allow_headers"]} == {"authorization", "content-type", "if-match"}
        and {h.lower() for h in cors["expose_headers"]} == {"etag", "retry-after"}
        and not cors.get("allow_credentials"),
        "CORS differs from the registered origin or v1.2 headers",
    )
    auth = after("aws_apigatewayv2_authorizer.ack_google")
    require(
        variables["ack_google_web_client_id"] == CLIENT
        and auth["authorizer_type"] == "JWT"
        and auth["identity_sources"] == ["$request.header.Authorization"]
        and auth["jwt_configuration"] == [{"issuer": "https://accounts.google.com", "audience": [CLIENT]}],
        "Expected the native Google JWT authorizer and public client from #41",
    )
    integration = after("aws_apigatewayv2_integration.ack")
    require(
        integration["integration_type"] == "AWS_PROXY"
        and integration["integration_method"] == "POST"
        and integration["payload_format_version"] == "2.0",
        "Wrong Lambda proxy integration",
    )
    for name in (
        "aws_apigatewayv2_authorizer.ack_google",
        "aws_apigatewayv2_integration.ack",
        "aws_apigatewayv2_stage.ack",
        "aws_apigatewayv2_route.ack",
    ):
        linked(name, "api_id", "aws_apigatewayv2_api.ack[0].id")
    linked("aws_apigatewayv2_route.ack", "authorizer_id", "aws_apigatewayv2_authorizer.ack_google[0].id")
    linked("aws_apigatewayv2_route.ack", "target", "aws_apigatewayv2_integration.ack[0].id")
    linked("aws_apigatewayv2_integration.ack", "integration_uri", "aws_lambda_function.ack[0].invoke_arn")
    permission = "aws_lambda_permission.ack_gateway"
    linked(permission, "function_name", "aws_lambda_function.ack[0].function_name")
    for ref in ("aws_apigatewayv2_api.ack[0].execution_arn", "var.env", "each.value"):
        linked(permission, "source_arn", ref)
    stage = after("aws_apigatewayv2_stage.ack")
    require(stage["name"] == "dev" and stage["auto_deploy"] is True, "Wrong stage")
    (settings,) = stage["default_route_settings"]
    require(
        settings["throttling_burst_limit"] == (4 if "machines" in routes else 2)
        and settings["throttling_rate_limit"] == 1,
        "Unexpected rehearsal throttling",
    )
    for key, (method, path) in routes.items():
        route = rows[f'aws_apigatewayv2_route.ack["{key}"]']["after"]
        permission_change = rows[f'{permission}["{key}"]']
        perm = permission_change["after"]
        require(route["route_key"] == f"{method} {path}" and route["authorization_type"] == "JWT", "Unexpected route")
        require(
            perm["principal"] == "apigateway.amazonaws.com"
            and perm["action"] == "lambda:InvokeFunction"
            and perm["source_account"] == account,
            "Unexpected Lambda permission",
        )
        if perm.get("source_arn"):
            suffix = path.replace("{id}", "*").replace("{incId}", "*").replace("{op}", "*").replace("{partId}", "*")
            require(perm["source_arn"] == f"{api['execution_arn']}/dev/{method}{suffix}", "Wrong invocation ARN")
        else:
            require(permission_change["after_unknown"].get("source_arn") is True, "Missing invocation ARN")
    fn = after("aws_lambda_function.ack")
    require(
        fn["function_name"] == "af-dev-api-ack"
        and fn["runtime"] == "python3.12"
        and fn["handler"] == "handler.handler"
        and fn["architectures"] == ["x86_64"]
        and fn["role"] == f"arn:aws:iam::{account}:role/LabRole"
        and fn["timeout"] == 15
        and fn["memory_size"] == 256
        and fn["environment"]
        == [{"variables": {"AF_TABLE_NAME": "af-dev-arquifusion", "AF_OPERATION_ID": variables["operation_id"]}}],
        "Wrong service, runtime, role or environment",
    )
    digest = check_package(root, root / "build/api/api-ack.zip", "api")
    require(base64.b64encode(digest).decode() == fn["source_code_hash"], "API ZIP differs from the planned hash")
    return digest.hex()


def check_refresh(data, rows):
    fields_by_address = {}
    for row in data.get("resource_drift", []):
        address = row["address"]
        old, new = row["change"]["before"], row["change"]["after"]
        require(
            (address in BASE or address in rows) and isinstance(old, dict) and isinstance(new, dict),
            "Unexpected refresh: " + address,
        )
        fields = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        for field in fields:
            if field == "policy" and address == "aws_sqs_queue.incident_delivery_dlq":
                policy = rows["aws_sqs_queue_policy.incident_delivery"]["after"]["policy"]
                require(not old.get(field) and json.loads(new[field]) == json.loads(policy), "Unexpected DLQ policy")
            else:
                empty = (None, [], {})
                require(
                    field in {"layers", "tags", "alarm_actions", "ok_actions", "insufficient_data_actions"}
                    and old.get(field) in empty
                    and new.get(field) in empty,
                    f"Unexpected refresh: {address}.{field}",
                )
        fields_by_address[address] = fields
    return fields_by_address


def review_api_changes(rows, addresses):
    allowed_updates = {
        "aws_apigatewayv2_api.ack[0]": {"disable_execute_api_endpoint", "cors_configuration"},
        "aws_apigatewayv2_stage.ack[0]": {"default_route_settings"},
        "aws_lambda_function.ack[0]": {"source_code_hash", "last_modified"},
    }
    updates = {}
    for address in addresses:
        change = rows[address]
        if change["actions"] in (["create"], ["no-op"]):
            continue
        require(change["actions"] == ["update"] and address in allowed_updates, "Unexpected API change: " + address)
        before, after = change["before"], change["after"]
        fields = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
        unknown = {k for k, v in change.get("after_unknown", {}).items() if has_unknown(v)}
        require(fields and (fields | unknown) <= allowed_updates[address], "Unexpected API fields: " + address)
        updates[address] = sorted(fields)
    return updates


def review(data, *, enabled, endpoint_open=False, fleet_reads=False, origin=DEV_ORIGIN, root=ROOT):
    require(not data.get("errored") and data.get("complete") is True, "Expected a complete plan")
    variables = {k: v["value"] for k, v in data["variables"].items()}
    # A value passed via -var can be serialized as a string before Terraform's type conversion.
    for key, expected in (
        ("enable_incident_api", enabled),
        ("open_incident_api_endpoint", endpoint_open),
        ("enable_fleet_read_routes", fleet_reads),
    ):
        value = variables.get(key)
        require(value is expected or value == str(expected).lower(), f"Wrong {key} setting or superseded plan")
    require(enabled or not (endpoint_open or fleet_reads), "Endpoint and fleet reads require the protected API")
    require(
        (variables["aws_profile"], variables["region"], variables["env"]) == ("default", "us-east-1", "dev")
        and variables.get("incident_test_operation_id") is None,
        "Wrong Academy environment or active rehearsal",
    )
    changes = [r for r in data["resource_changes"] if r["mode"] == "managed"]
    rows = {r["address"]: r["change"] for r in changes}
    require(len(rows) == len(changes), "Duplicate/deposed resource instances")
    routes = (ROUTES | FLEET_ROUTES) if fleet_reads else ROUTES
    api = api_addresses(routes) if enabled else set()
    expected = BASE | api
    require(set(rows) == expected, "Unexpected/missing managed addresses: " + ", ".join(sorted(set(rows) ^ expected)))
    updates = check_existing(rows, variables, root)
    summary = {"apiResourcesIncluded": enabled}
    if enabled:
        updates.update(review_api_changes(rows, api))
        config = {r["address"]: r for r in data["configuration"]["root_module"]["resources"]}
        digest = check_api(rows, config, variables, origin, root, endpoint_open=endpoint_open, routes=routes)
        summary.update(
            lambdaZipSha256=digest,
            endpointClosed=not endpoint_open,
            routes=sorted(f"{method} {path}" for method, path in routes.values()),
        )
    creates = sorted(address for address, change in rows.items() if change["actions"] == ["create"])
    summary.update(
        add=len(creates),
        change=len(updates),
        destroy=0,
        noOp=len(rows) - len(creates) - len(updates),
        creates=creates,
        updates=dict(sorted(updates.items())),
    )
    summary["refreshFields"] = check_refresh(data, rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--expect", choices=["disabled", "closed", "open"], required=True)
    parser.add_argument("--fleet-reads", action="store_true", help="Expect the ten authenticated dashboard GETs")
    parser.add_argument("--origin", default=DEV_ORIGIN, help="Exact registered origin expected in this plan")
    parser.add_argument("--terraform", default="terraform")
    args = parser.parse_args()
    plan = args.plan.resolve()
    digest = file_hash(plan).hex()
    check_plan_config(plan)
    data = json.loads(subprocess.check_output([args.terraform, f"-chdir={AWS}", "show", "-json", str(plan)]))
    summary = review(
        data,
        enabled=args.expect != "disabled",
        endpoint_open=args.expect == "open",
        fleet_reads=args.fleet_reads,
        origin=args.origin,
    )
    require(digest == file_hash(plan).hex(), "The saved plan changed during the review")
    summary.update(file=plan.name, sha256=digest)
    destination = plan.with_suffix(".review.json")
    destination.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{summary['add']} add, {summary['change']} change, {summary['destroy']} destroy, {summary['noOp']} no-op")
    print(destination)


if __name__ == "__main__":
    main()
