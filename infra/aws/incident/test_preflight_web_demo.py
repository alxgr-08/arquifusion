import copy
import json
from datetime import UTC, datetime

import pytest

from infra.aws.incident import preflight_web_demo as demo


@pytest.fixture(autouse=True)
def no_aws_clients(monkeypatch):
    import boto3

    def fail(*_args, **_kwargs):
        pytest.fail("Offline preparation must not create AWS sessions, clients or resources")

    for name in ("Session", "client", "resource"):
        monkeypatch.setattr(boto3, name, fail)


@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    path = tmp_path / "build/ensayo-0001"
    demo.prepare(path, now=datetime(2026, 11, 15, tzinfo=UTC))
    return path


def real_grants():
    return [
        {"sub": "123456789012345678901", "operationId": demo.OPERATION, "role": "MAINT_LEAD"},
        {
            "sub": "123456789012345678902",
            "operationId": demo.OPERATION,
            "role": "TECHNICIAN",
            "techId": demo.TECHNICIAN,
        },
    ]


def test_preparation_uses_the_existing_kit_and_never_constructs_an_aws_session(kit, monkeypatch):
    import boto3

    monkeypatch.setattr(boto3, "Session", lambda *_a, **_kw: pytest.fail("Offline checker must not create sessions"))
    result = demo.inspect(kit)
    assert result["cloudCalls"] == result["ssmWrites"] == []
    assert len(result["rows"]) == 7
    assert result["remoteCollisionsChecked"] is False
    assert result["participantIdentityVerified"] is False
    assert result["realParticipantGrants"] == "NOT_CHECKED"
    assert all("ensayo-operador" not in row["PK"] for row in result["rows"])


def test_existing_kit_is_not_rewritten_or_retimestamped(kit):
    before = (kit / "anomaly-caso1-vibracion.json").read_bytes()
    with pytest.raises(demo.PreflightError, match="Kit already exists"):
        demo.prepare(kit)
    assert (kit / "anomaly-caso1-vibracion.json").read_bytes() == before


def test_missing_or_cross_operation_metadata_is_rejected(kit):
    path = kit / "item-op-meta.json"
    value = json.loads(path.read_text())
    value["warehouses"] = {"L": [{"S": "WH-SHARED"}]}
    path.write_text(json.dumps(value))
    with pytest.raises(demo.PreflightError, match="Operation metadata"):
        demo.inspect(kit)


def test_shared_stock_reservation_is_rejected(kit):
    path = kit / "item-op-settings.json"
    value = json.loads(path.read_text())
    value["settings"]["M"]["partsFromOtherWarehouses"] = {"BOOL": True}
    path.write_text(json.dumps(value))
    with pytest.raises(demo.PreflightError, match="shared warehouses"):
        demo.inspect(kit)


def test_placeholder_templates_are_not_real_participants(kit):
    with pytest.raises(demo.PreflightError, match="real Google subject"):
        demo.inspect(kit, require_real_grants=True)


@pytest.mark.parametrize("subject", ["<sub de Google>", "ensayo-operador-0001", "", None])
def test_fictitious_or_missing_subject_is_rejected(subject):
    rows = real_grants()
    rows[0]["sub"] = subject
    with pytest.raises(demo.PreflightError, match="real Google subject"):
        demo.validate_real_grants(rows)


def test_real_grants_are_validated_without_returning_personal_ids():
    rows = real_grants()
    result = demo.validate_real_grants(rows)
    assert result == {"participants": 2, "roles": ["MAINT_LEAD", "TECHNICIAN"]}
    assert all(row["sub"] not in json.dumps(result) for row in rows)


def test_duplicate_identity_cannot_silently_replace_the_other_demo_role():
    rows = real_grants()
    rows[1]["sub"] = rows[0]["sub"]
    with pytest.raises(demo.PreflightError, match="Duplicate participant"):
        demo.validate_real_grants(rows)


@pytest.mark.parametrize(
    "change", [{"operationId": "MINA-NORTE"}, {"techId": "T-OTHER"}, {"iss": "https://untrusted.example"}]
)
def test_wrong_operation_technician_or_issuer_is_rejected(change):
    rows = copy.deepcopy(real_grants())
    rows[1].update(change)
    with pytest.raises(demo.PreflightError):
        demo.validate_real_grants(rows)
