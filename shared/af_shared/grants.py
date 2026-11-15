"""Who may do what: roles live in DynamoDB, not in the token (decision #42: Google login, no Cognito).

API Gateway (HTTP API, JWT authorizer, issuer https://accounts.google.com, audience = Google OAuth Client ID)
only proves identity. The Lambda resolves `iss` + `sub` from the verified claims against grant items:

    PK = GRANT#{iss}#{sub}   SK = OP#{operationId}
    operationId, role (OPERATOR | TECHNICIAN | MAINT_LEAD | SUPERVISOR | MANAGER), techId (technicians),
    version (int, bump it to invalidate in-flight actions), email/name (only to recognise the row)

One row per operation; no row = no access. Revoking = deleting the row (or bumping `version`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from boto3.dynamodb.conditions import Key

from af_shared.http import ApiError, claims

ROLES = {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}


def auth_required() -> bool:
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME")) or os.getenv("AF_AUTH_DISABLED") != "true"


def grant_pk(iss: str, sub: str) -> str:
    return f"GRANT#{iss}#{sub}"


def grant_key(iss: str, sub: str, operation_id: str) -> dict[str, str]:
    return {"PK": grant_pk(iss, sub), "SK": f"OP#{operation_id}"}


def item(
    iss: str,
    sub: str,
    operation_id: str,
    role: str,
    tech_id: str | None = None,
    version: int = 1,
    label: str | None = None,
) -> dict[str, Any]:
    """Grant row as stored in DynamoDB (seed scripts and tests)."""
    if role not in ROLES:
        raise ValueError(f"Unknown role {role}")
    if role == "TECHNICIAN" and not tech_id:
        raise ValueError("A TECHNICIAN grant needs techId")
    row: dict[str, Any] = {
        **grant_key(iss.removeprefix("https://"), sub, operation_id),
        "operationId": operation_id,
        "role": role,
        "version": version,
    }
    if tech_id:
        row["techId"] = tech_id
    if label:
        row["label"] = label
    return row


@dataclass(frozen=True)
class Grant:
    iss: str
    sub: str
    operation_id: str
    role: str
    tech_id: str | None
    version: int

    @property
    def key(self) -> dict[str, str]:
        return grant_key(self.iss, self.sub, self.operation_id)


@dataclass
class Principal:
    iss: str
    sub: str
    grants: list[Grant]

    def roles(self, operation_id: str | None = None) -> set[str]:
        return {g.role for g in self.grants if operation_id is None or g.operation_id == operation_id}

    def operations(self) -> set[str]:
        return {g.operation_id for g in self.grants}

    def grant_for(self, operation_id: str) -> Grant | None:
        return next((g for g in self.grants if g.operation_id == operation_id), None)


def identity(event: dict[str, Any]) -> tuple[str, str]:
    c = claims(event)
    iss, sub = str(c.get("iss") or ""), str(c.get("sub") or "")
    if iss not in {"accounts.google.com", "https://accounts.google.com"} or not sub:
        raise ApiError(401, "UNAUTHORIZED", "Missing verified identity (iss/sub) in the token")
    return iss.removeprefix("https://"), sub


def load(table: Any, event: dict[str, Any]) -> Principal:
    """Grants of the caller, read with ConsistentRead so a revocation applies to the very next request."""
    iss, sub = identity(event)
    res = table.query(
        KeyConditionExpression=Key("PK").eq(grant_pk(iss, sub)) & Key("SK").begins_with("OP#"),
        ConsistentRead=True,
    )
    grants = [
        Grant(
            iss=iss,
            sub=sub,
            operation_id=str(row["operationId"]),
            role=str(row["role"]),
            tech_id=str(row["techId"]) if row.get("techId") else None,
            version=int(row.get("version", 1)),
        )
        for row in res.get("Items", [])
        if row.get("role") in ROLES and row.get("operationId")
    ]
    return Principal(iss=iss, sub=sub, grants=grants)


def require(principal: Principal, allowed: set[str], operation_id: str | None = None) -> None:
    if not principal.grants:
        raise ApiError(403, "NO_GRANT", "This account has no access to ArquiFusion")
    if operation_id is not None and operation_id not in principal.operations():
        raise ApiError(403, "OTHER_OPERATION", "No access to this operation")
    if not principal.roles(operation_id) & allowed:
        raise ApiError(403, "FORBIDDEN", f"Requires one of: {', '.join(sorted(allowed))}")


def condition_check(grant: Grant) -> dict[str, Any]:
    """TransactWriteItems ConditionCheck: the grant still exists with the same role, techId and version."""
    expr = "#role = :role AND #ver = :ver"
    values: dict[str, Any] = {":role": grant.role, ":ver": grant.version}
    if grant.tech_id:
        expr += " AND techId = :tech"
        values[":tech"] = grant.tech_id
    return {
        "ConditionCheck": {
            "Key": grant.key,
            "ConditionExpression": expr,
            "ExpressionAttributeNames": {"#role": "role", "#ver": "version"},
            "ExpressionAttributeValues": values,
        }
    }
