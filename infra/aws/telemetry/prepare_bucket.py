"""Preview the dedicated lake bucket; --check reads AWS, --create requires separate approval."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACCOUNT = "975050277936"
REGION = "us-east-1"
TAGS = {"project": "arquifusion", "env": "dev", "owner": "rol1", "purpose": "telemetry-lake"}


class AwsFailure(RuntimeError):
    def __init__(self, operation: str, code: str):
        self.code = code
        super().__init__(f"AWS {operation} failed ({code}); stop and reconcile before another attempt.")


class Cli:
    """Use argv, normal credential resolution and one attempt; never print raw CLI errors."""

    def __init__(self):
        self.executable = shutil.which("aws") or "C:/Program Files/Amazon/AWSCLIV2/aws.exe"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_ENDPOINT_URL")}
        self.env.update(
            AWS_PROFILE="default",
            AWS_REGION=REGION,
            AWS_DEFAULT_REGION=REGION,
            AWS_PAGER="",
            AWS_CLI_AUTO_PROMPT="off",
            AWS_MAX_ATTEMPTS="1",
            AWS_RETRY_MODE="standard",
            AWS_IGNORE_CONFIGURED_ENDPOINT_URLS="true",
        )

    def __call__(self, service: str, operation: str, *args: str) -> dict[str, Any]:
        command = [self.executable, service, operation, *args, "--profile", "default", "--region", REGION]
        command += [
            "--output",
            "json",
            "--no-cli-pager",
            "--no-cli-auto-prompt",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "15",
        ]
        try:
            result = subprocess.run(command, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=45)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AwsFailure(operation, type(error).__name__) from None
        if result.returncode:
            match = re.search(r"An error occurred \(([^)]+)\)", result.stderr)
            raise AwsFailure(operation, match[1] if match else "CLI_ERROR")
        return json.loads(result.stdout) if result.stdout.strip() else {}


def proposal(account: str) -> dict[str, Any]:
    if account != ACCOUNT:
        raise ValueError("Only the previously verified ArquiFusion Academy account is allowed.")
    bucket = f"af-dev-arquifusion-lake-{account}-{REGION}-an"
    return {
        "account": account,
        "profile": "default",
        "region": REGION,
        "bucket": bucket,
        "namespace": "account-regional",
        "writes": [
            {"service": "s3api", "operation": "create-bucket", "Bucket": bucket, "BucketNamespace": "account-regional"},
            {
                "service": "s3api",
                "operation": "put-bucket-tagging",
                "Bucket": bucket,
                "ExpectedBucketOwner": account,
                "Tagging": {"TagSet": [{"Key": k, "Value": v} for k, v in sorted(TAGS.items())]},
            },
        ],
        "requiredNewBucketDefaults": {
            "blockPublicAccess": True,
            "encryption": "AES256",
            "ownership": "BucketOwnerEnforced",
        },
        "terraformNext": "6 module.telemetry resources: 3 bucket controls, Glue database/table and Firehose",
    }


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest().upper()


def check_absent(cli, value: dict[str, Any]) -> None:
    identity = cli("sts", "get-caller-identity")
    if identity.get("Account") != value["account"] or ":assumed-role/voclabs/" not in identity.get("Arn", ""):
        raise ValueError("Unexpected account or Academy session role.")
    skeleton = cli("s3api", "create-bucket", "--generate-cli-skeleton", "input")
    if "BucketNamespace" not in skeleton:
        raise ValueError("This AWS CLI lacks the reviewed account-regional namespace option.")
    try:
        cli("s3api", "head-bucket", "--bucket", value["bucket"], "--expected-bucket-owner", value["account"])
    except AwsFailure as error:
        if error.code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    else:
        raise ValueError("The bucket already exists. Do not adopt, retag, recreate or modify it.")


def check_private(cli, value: dict[str, Any]) -> None:
    target = ["--bucket", value["bucket"], "--expected-bucket-owner", value["account"]]
    location = cli("s3api", "get-bucket-location", *target).get("LocationConstraint")
    if location not in (None, "us-east-1"):
        raise ValueError("Unexpected bucket region.")
    public = cli("s3api", "get-public-access-block", *target).get("PublicAccessBlockConfiguration", {})
    if public != {
        k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
    }:
        raise ValueError("New bucket public access controls differ from the reviewed defaults.")
    rules = cli("s3api", "get-bucket-encryption", *target).get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    if len(rules) != 1 or rules[0].get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") != "AES256":
        raise ValueError("New bucket encryption differs from the reviewed default.")
    ownership = cli("s3api", "get-bucket-ownership-controls", *target).get("OwnershipControls", {}).get("Rules")
    if ownership != [{"ObjectOwnership": "BucketOwnerEnforced"}]:
        raise ValueError("New bucket must keep ACLs disabled.")


def create(cli, value: dict[str, Any], approved_sha256: str, journal: Path) -> None:
    if approved_sha256.upper() != digest(value):
        raise ValueError("The approved proposal SHA-256 does not match.")
    if journal.exists():
        raise ValueError("A journal already exists. Reconcile it; this tool never automatically resumes a write.")
    check_absent(cli, value)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("x", encoding="utf-8") as stream:

        def note(stage):
            stream.write(
                json.dumps(
                    {
                        "stage": stage,
                        "bucket": value["bucket"],
                        "proposalSha256": digest(value),
                        "time": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n"
            )
            stream.flush()

        note("CREATE_STARTED")
        cli("s3api", "create-bucket", "--bucket", value["bucket"], "--bucket-namespace", "account-regional")
        note("CREATE_ACCEPTED")
        check_private(cli, value)
        note("PRIVATE_DEFAULTS_VERIFIED")
        tag_request = value["writes"][1]["Tagging"]
        cli(
            "s3api",
            "put-bucket-tagging",
            "--bucket",
            value["bucket"],
            "--expected-bucket-owner",
            value["account"],
            "--tagging",
            json.dumps(tag_request),
        )
        note("TAGGING_ACCEPTED")
        actual = cli(
            "s3api", "get-bucket-tagging", "--bucket", value["bucket"], "--expected-bucket-owner", value["account"]
        )
        if {t["Key"]: t["Value"] for t in actual.get("TagSet", [])} != TAGS:
            raise ValueError("Bucket tags need manual reconciliation.")
        mapped = cli(
            "resourcegroupstaggingapi", "get-resources", "--resource-arn-list", f"arn:aws:s3:::{value['bucket']}"
        )
        if not any(
            row.get("ResourceARN") == f"arn:aws:s3:::{value['bucket']}"
            and {tag["Key"]: tag["Value"] for tag in row.get("Tags", [])} == TAGS
            for row in mapped.get("ResourceTagMappingList", [])
        ):
            raise ValueError(
                "Terraform ownership lookup is not ready. Verify tag propagation and permissions; "
                "do not repeat creation."
            )
        note("TAGS_VERIFIED_READY_FOR_TERRAFORM_PLAN")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only account, CLI capability and absence check")
    mode.add_argument(
        "--create", action="store_true", help="WRITE: only after explicit approval of this bucket proposal"
    )
    parser.add_argument("--approved-sha256")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args(argv)
    value = proposal(args.account_id)
    result = {"proposal": value, "sha256": digest(value), "status": "PREVIEW_OFFLINE", "cloudWritesPerformed": False}
    if args.create:
        if not args.approved_sha256 or args.journal is None:
            parser.error("--create requires --approved-sha256 and --journal after separate approval")
        create(Cli(), value, args.approved_sha256, args.journal)
        result.update(status="CREATED_AND_VERIFIED", cloudWritesPerformed=True)
    elif args.check:
        check_absent(Cli(), value)
        result["status"] = "READ_ONLY_CHECK_PASSED"
    elif args.approved_sha256 or args.journal:
        parser.error("Approval and journal arguments are valid only with --create")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
