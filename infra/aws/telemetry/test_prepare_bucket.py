import json

import pytest

from infra.aws.telemetry import prepare_bucket as bucket


class FakeCli:
    def __init__(self, *, head_code="404"):
        self.calls = []
        self.head_code = head_code
        self.results = {
            "get-caller-identity": {
                "Account": bucket.ACCOUNT,
                "Arn": f"arn:aws:sts::{bucket.ACCOUNT}:assumed-role/voclabs/session",
            },
            "get-bucket-location": {"LocationConstraint": None},
            "get-public-access-block": {
                "PublicAccessBlockConfiguration": {
                    k: True
                    for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
                }
            },
            "get-bucket-encryption": {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                }
            },
            "get-bucket-ownership-controls": {
                "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
            },
            "get-bucket-tagging": {"TagSet": [{"Key": k, "Value": v} for k, v in bucket.TAGS.items()]},
            "get-resources": {
                "ResourceTagMappingList": [
                    {
                        "ResourceARN": f"arn:aws:s3:::{bucket.proposal(bucket.ACCOUNT)['bucket']}",
                        "Tags": [{"Key": k, "Value": v} for k, v in bucket.TAGS.items()],
                    }
                ]
            },
        }

    def __call__(self, service, operation, *args):
        self.calls.append((service, operation, args))
        if operation == "head-bucket":
            if self.head_code:
                raise bucket.AwsFailure(operation, self.head_code)
            return {}
        if operation == "create-bucket" and "--generate-cli-skeleton" in args:
            return {"BucketNamespace": "account-regional"}
        return self.results.get(operation, {})


def writes(cli):
    return [
        (op, args)
        for _, op, args in cli.calls
        if op == "put-bucket-tagging" or (op == "create-bucket" and "--generate-cli-skeleton" not in args)
    ]


def test_offline_preview_never_constructs_a_cloud_client(monkeypatch, capsys):
    monkeypatch.setattr(bucket, "Cli", lambda: pytest.fail("Preview must not access AWS"))
    bucket.main(["--account-id", bucket.ACCOUNT])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PREVIEW_OFFLINE" and result["cloudWritesPerformed"] is False
    assert result["proposal"]["bucket"] != f"af-dev-tfstate-{bucket.ACCOUNT}"
    assert len(result["proposal"]["writes"]) == 2


def test_other_account_is_rejected():
    with pytest.raises(ValueError, match="previously verified"):
        bucket.proposal("123456789012")


def test_check_is_read_only():
    cli = FakeCli()
    bucket.check_absent(cli, bucket.proposal(bucket.ACCOUNT))
    assert writes(cli) == []


@pytest.mark.parametrize("head_code", [None, "403", "AccessDenied", "CLI_ERROR"])
def test_existing_or_ambiguous_bucket_stops_before_writes(tmp_path, head_code):
    cli = FakeCli(head_code=head_code)
    proposal = bucket.proposal(bucket.ACCOUNT)
    with pytest.raises((ValueError, bucket.AwsFailure)):
        bucket.create(cli, proposal, bucket.digest(proposal), tmp_path / "journal")
    assert not writes(cli)
    assert not (tmp_path / "journal").exists()


def test_wrong_session_stops_before_bucket_queries():
    cli = FakeCli()
    cli.results["get-caller-identity"]["Account"] = "123456789012"
    with pytest.raises(ValueError, match="Unexpected account"):
        bucket.check_absent(cli, bucket.proposal(bucket.ACCOUNT))
    assert len(cli.calls) == 1


def test_wrong_approval_and_existing_journal_never_call_cloud(tmp_path):
    cli = FakeCli()
    proposal = bucket.proposal(bucket.ACCOUNT)
    path = tmp_path / "journal"
    with pytest.raises(ValueError, match="SHA-256"):
        bucket.create(cli, proposal, "0" * 64, path)
    path.touch()
    with pytest.raises(ValueError, match="journal already exists"):
        bucket.create(cli, proposal, bucket.digest(proposal), path)
    assert cli.calls == []


def test_create_performs_exact_two_writes_and_verifies_private_defaults(tmp_path):
    cli = FakeCli()
    proposal = bucket.proposal(bucket.ACCOUNT)
    path = tmp_path / "journal"
    bucket.create(cli, proposal, bucket.digest(proposal), path)
    assert [op for op, _ in writes(cli)] == ["create-bucket", "put-bucket-tagging"]
    assert writes(cli)[0][1][-2:] == ("--bucket-namespace", "account-regional")
    assert "--acl" not in writes(cli)[0][1] and "--create-bucket-configuration" not in writes(cli)[0][1]
    assert json.loads(path.read_text().splitlines()[-1])["stage"] == "TAGS_VERIFIED_READY_FOR_TERRAFORM_PLAN"


def test_private_default_failure_never_retags_or_retries(tmp_path):
    cli = FakeCli()
    cli.results["get-public-access-block"]["PublicAccessBlockConfiguration"]["BlockPublicAcls"] = False
    proposal = bucket.proposal(bucket.ACCOUNT)
    path = tmp_path / "journal"
    with pytest.raises(ValueError, match="public access"):
        bucket.create(cli, proposal, bucket.digest(proposal), path)
    assert [op for op, _ in writes(cli)] == ["create-bucket"]
    assert json.loads(path.read_text().splitlines()[-1])["stage"] == "CREATE_ACCEPTED"
    with pytest.raises(ValueError, match="journal already exists"):
        bucket.create(cli, proposal, bucket.digest(proposal), path)


def test_ambiguous_create_is_not_retried_or_followed_by_tagging(tmp_path):
    class Timeout(FakeCli):
        def __call__(self, service, operation, *args):
            result = super().__call__(service, operation, *args)
            if operation == "create-bucket" and "--generate-cli-skeleton" not in args:
                raise bucket.AwsFailure(operation, "TimeoutExpired")
            return result

    cli = Timeout()
    proposal = bucket.proposal(bucket.ACCOUNT)
    path = tmp_path / "journal"
    with pytest.raises(bucket.AwsFailure, match="TimeoutExpired"):
        bucket.create(cli, proposal, bucket.digest(proposal), path)
    assert [op for op, _ in writes(cli)] == ["create-bucket"]
    assert json.loads(path.read_text().splitlines()[-1])["stage"] == "CREATE_STARTED"


def test_missing_terraform_tag_lookup_stops_after_recording_tag_write(tmp_path):
    cli = FakeCli()
    cli.results["get-resources"] = {"ResourceTagMappingList": []}
    proposal = bucket.proposal(bucket.ACCOUNT)
    path = tmp_path / "journal"
    with pytest.raises(ValueError, match="ownership lookup is not ready"):
        bucket.create(cli, proposal, bucket.digest(proposal), path)
    assert [op for op, _ in writes(cli)] == ["create-bucket", "put-bucket-tagging"]
    assert json.loads(path.read_text().splitlines()[-1])["stage"] == "TAGGING_ACCEPTED"
