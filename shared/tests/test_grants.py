import sys
from pathlib import Path

import pytest
from af_shared import grants
from af_shared.fakes import FakeTable
from af_shared.http import ApiError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import seed_grants  # noqa: E402


def event(sub: str) -> dict:
    return {"requestContext": {"authorizer": {"jwt": {"claims": {"iss": "https://accounts.google.com", "sub": sub}}}}}


def test_roles_per_operation_and_revocation() -> None:
    table = FakeTable()
    rows = [
        {"sub": "u1", "operationId": "MINA-NORTE", "role": "MAINT_LEAD"},
        {"sub": "u1", "operationId": "MINA-SUR", "role": "OPERATOR"},
    ]
    for item in seed_grants.build_items(rows, {}):
        table.put_item(Item=item)
    p = grants.load(table, event("u1"))
    assert p.roles("MINA-NORTE") == {"MAINT_LEAD"} and p.roles("MINA-SUR") == {"OPERATOR"}
    grants.require(p, {"MAINT_LEAD"}, "MINA-NORTE")
    with pytest.raises(ApiError) as err:
        grants.require(p, {"MAINT_LEAD"}, "MINA-SUR")
    assert err.value.code == "FORBIDDEN"
    with pytest.raises(ApiError) as err:
        grants.require(grants.load(table, event("nobody")), {"MAINT_LEAD"})
    assert err.value.code == "NO_GRANT"
    with pytest.raises(ApiError) as err:
        grants.load(table, {"requestContext": {}})
    assert err.value.status == 401


def test_seed_bumps_version_only_when_role_changes() -> None:
    row = {"sub": "u2", "operationId": "MINA-NORTE", "role": "TECHNICIAN", "techId": "T-07"}
    (first,) = seed_grants.build_items([row], {})
    key = (first["PK"], first["SK"])
    assert seed_grants.build_items([row], {key: first})[0]["version"] == 1
    assert seed_grants.build_items([{**row, "techId": "T-03"}], {key: first})[0]["version"] == 2
    with pytest.raises(ValueError):
        seed_grants.build_items([{**row, "techId": None}], {})
