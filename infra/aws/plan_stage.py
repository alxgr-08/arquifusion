"""Prepare a read-only review of one additive stage over ArquiFusion's 29 resources."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infra.aws.api import review_plan as review  # noqa: E402

AWS = ROOT / "infra/aws"
ORIGIN = "https://demo.d2ygcm34t8aku9.amplifyapp.com"
STAGES = {
    "base": {},
    "api-closed": {
        "enable_incident_api": True,
        "enable_fleet_read_routes": True,
        "ack_google_web_client_id": review.CLIENT,
        "ack_frontend_origin": ORIGIN,
    },
    "outbox-disabled": {"prepare_anomaly_outbox_consumer": True},
    "lake-archive": {"enable_telemetry_lake": True, "enable_stream_exporter": True},
}
LAKE = {
    f"module.telemetry[0].{address}"
    for address in (
        "aws_s3_bucket_public_access_block.lake",
        "aws_s3_bucket_server_side_encryption_configuration.lake",
        "aws_s3_bucket_versioning.lake",
        "aws_glue_catalog_database.telemetry",
        "aws_glue_catalog_table.telemetry",
        "aws_kinesis_firehose_delivery_stream.telemetry",
    )
}
EXPORTER = {
    'aws_cloudwatch_log_group.bridge["stream_exporter"]',
    'aws_lambda_function.bridge["stream_exporter"]',
    "aws_lambda_event_source_mapping.archive[0]",
}


def options(account, stage):
    review.require(
        stage in STAGES and len(account) == 12 and account.isascii() and account.isdecimal(), "Invalid stage or account"
    )
    return {
        "expected_account_id": account,
        "aws_profile": "default",
        "region": "us-east-1",
        "env": "dev",
        "prepare_manual_web_hosting": True,
        "enable_incident_api": False,
        "enable_fleet_read_routes": False,
        "open_incident_api_endpoint": False,
        "enable_telemetry_lake": False,
        "enable_stream_exporter": False,
        "prepare_anomaly_outbox_consumer": False,
        "bridge_wif": None,
        "sync_back_trial": None,
        "maintenance_trial_operation": None,
        "incident_test_operation_id": None,
        **STAGES[stage],
    }


def check_additions(data, stage):
    if stage in ("base", "api-closed"):
        enabled = stage == "api-closed"
        result = review.review(data, enabled=enabled, fleet_reads=enabled, origin=ORIGIN, existing_hosting=True)
        review.require(result["add"] == (31 if enabled else 0) and result["change"] == 0, "Unexpected API/base changes")
        return result
    review.require(data.get("complete") is True and not data.get("errored"), "Incomplete plan")
    variables = {key: value["value"] for key, value in data["variables"].items()}
    expected_options = options(variables["expected_account_id"], stage)
    for key, expected in expected_options.items():
        review.require(variables.get(key) == expected, "Unexpected configuration: " + key)
    entries = [r for r in data["resource_changes"] if r["mode"] == "managed"]
    rows = {r["address"]: r["change"] for r in entries}
    additions = {"aws_lambda_event_source_mapping.anomaly_outbox[0]"} if stage == "outbox-disabled" else LAKE | EXPORTER
    base = review.BASE | review.HOSTING
    review.require(len(rows) == len(entries) and set(rows) == base | additions, "Unexpected managed addresses")
    review.require(all(rows[a]["actions"] == ["no-op"] for a in base), "Existing resources must remain no-op")
    review.require(all(rows[a]["actions"] == ["create"] for a in additions), "Stage must contain only new resources")
    review.check_existing(rows, variables, ROOT)
    review.check_hosting(rows, variables)
    for address in additions:
        after = rows[address]["after"]
        if "aws_lambda_event_source_mapping" in address:
            review.require(after["enabled"] is False, "A stream consumer must start disabled")
            review.require(after["parallelization_factor"] == 1, "Unexpected consumer concurrency")
            review.require(after["function_response_types"] == ["ReportBatchItemFailures"], "Partial failures required")
    if stage == "outbox-disabled":
        mapping = rows["aws_lambda_event_source_mapping.anomaly_outbox[0]"]["after"]
        review.require(
            mapping["function_name"] == rows["aws_lambda_function.health_evaluator"]["after"]["arn"],
            "Wrong outbox target",
        )
        review.require(
            mapping["event_source_arn"] == rows["aws_dynamodb_table.arquifusion"]["after"]["stream_arn"],
            "Wrong outbox source",
        )
        filters = mapping["filter_criteria"]
        expected_filter = {
            "eventName": ["INSERT"],
            "dynamodb": {"Keys": {"SK": {"S": [{"prefix": "OUTBOX#ANOMALY#"}]}}},
        }
        review.require(
            len(filters) == 1
            and len(filters[0]["filter"]) == 1
            and json.loads(filters[0]["filter"][0]["pattern"]) == expected_filter,
            "Wrong anomaly filter",
        )
    return {
        "add": len(additions),
        "change": 0,
        "destroy": 0,
        "noOp": len(base),
        "creates": sorted(additions),
        "updates": {},
        "refreshFields": review.check_refresh(data, rows),
    }


def backend_metadata():
    backend = json.loads((AWS / ".terraform/terraform.tfstate").read_text())["backend"]
    cfg = backend["config"]
    review.require(backend["type"] == "s3", "Expected the existing S3 backend")
    return {key: cfg[key] for key in ("bucket", "key", "profile", "region", "use_lockfile", "allowed_account_ids")}


def state_summary(raw):
    state = json.loads(raw)
    return {
        "serial": state["serial"],
        "lineage": state["lineage"],
        "managed": sum(len(r["instances"]) for r in state["resources"] if r["mode"] == "managed"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--account", required=True)
    parser.add_argument("--output", type=Path, required=True, help="A new directory under build/")
    parser.add_argument("--terraform", default=shutil.which("terraform"))
    parser.add_argument("--aws", default=shutil.which("aws"))
    args = parser.parse_args()
    flags = options(args.account, args.stage)
    review.require(args.terraform and args.aws, "Provide the installed Terraform and AWS CLI executables")
    output = args.output.resolve()
    review.require(
        output.is_relative_to((ROOT / "build").resolve()) and not output.exists(), "Use a new build/ directory"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TF_VAR_", "TF_CLI_ARGS"))}
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "TF_DATA_DIR"):
        env.pop(key, None)
    env.update(
        AWS_PROFILE="default",
        AWS_REGION="us-east-1",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_PAGER="",
        TF_WORKSPACE="default",
        TF_IN_AUTOMATION="1",
    )

    def command(executable, *arguments, log=None):
        result = subprocess.run(
            [str(executable), *map(str, arguments)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if log:
            log.write_text(result.stdout + result.stderr, encoding="utf-8")
        review.require(result.returncode == 0, "Command failed; inspect its local log or active session")
        return result.stdout

    def terraform(*arguments, **kwargs):
        return command(args.terraform, f"-chdir={AWS}", *arguments, **kwargs)

    identity = json.loads(
        command(
            args.aws,
            "sts",
            "get-caller-identity",
            "--profile",
            "default",
            "--region",
            "us-east-1",
            "--output",
            "json",
            "--no-cli-pager",
        )
    )
    review.require(
        identity["Account"] == args.account and ":assumed-role/voclabs/" in identity["Arn"],
        "Unexpected Academy identity",
    )
    backend = backend_metadata()
    review.require(
        backend
        == {
            "bucket": f"af-dev-tfstate-{args.account}",
            "key": "aws/terraform.tfstate",
            "profile": "default",
            "region": "us-east-1",
            "use_lockfile": True,
            "allowed_account_ids": [args.account],
        },
        "Unexpected remote backend",
    )
    review.require(terraform("workspace", "show").strip() == "default", "Unexpected workspace")
    before = state_summary(terraform("state", "pull"))
    review.require(before["managed"] == 29, "Base changed: reconcile and regenerate the stage for the new inventory")
    output.mkdir(parents=True, exist_ok=False)
    variables = output / "review.tfvars.json"
    variables.write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
    candidate = output / "UNREVIEWED.tfplan"
    terraform(
        "plan",
        "-input=false",
        "-lock=false",
        "-no-color",
        f"-var-file={variables}",
        f"-out={candidate}",
        log=output / "plan.log",
    )
    data = json.loads(terraform("show", "-json", candidate))
    review.check_plan_config(candidate)
    result = check_additions(data, args.stage)
    review.require(
        result["noOp"] == 29 and result["change"] == result["destroy"] == 0, "Existing resources are not preserved"
    )
    with ZipFile(candidate) as archive:
        saved = json.loads(archive.read("tfstate"))
        review.require(
            (saved["serial"], saved["lineage"]) == (before["serial"], before["lineage"]), "Saved state snapshot differs"
        )
    review.require(state_summary(terraform("state", "pull")) == before, "Remote state changed during planning")
    plan = candidate.with_name(args.stage + ".tfplan")
    candidate.rename(plan)
    result.update(
        stage=args.stage,
        sha256=hashlib.sha256(plan.read_bytes()).hexdigest().upper(),
        backend=backend,
        state=before,
        applyExecuted=False,
    )
    plan.with_suffix(".review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Plan: {result['add']} to add, 0 to change, 0 to destroy; 29 no-op")
    print(result["sha256"])
    print(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
