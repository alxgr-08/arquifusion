"""Test identities: a Google `iss`/`sub` plus the grant row the API looks up (af_shared.grants)."""

from typing import Any

from af_shared import grants

ISS = "https://accounts.google.com"


def as_user(table: Any, role: str | None, operation: str | None = "MINA-NORTE", tech: str | None = None) -> dict:
    """Claims of a user whose only grant is `role` in `operation` (no role/operation = signed in, no grant)."""
    sub = f"{role or 'none'}-{operation or 'none'}-{tech or 'none'}"
    if role and operation:
        row = {**grants.grant_key("accounts.google.com", sub, operation), "operationId": operation, "role": role}
        row["version"] = 1
        if tech:
            row["techId"] = tech
        table.put_item(Item=row)
    return {"iss": ISS, "sub": sub}
