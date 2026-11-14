"""API Gateway (HTTP API, payload v2) helpers: responses, errors and Cognito groups."""

from __future__ import annotations

import base64
import json
from typing import Any

from af_shared.ddb import from_ddb

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def respond(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(from_ddb(body), ensure_ascii=False),
    }


def error(exc: ApiError) -> dict[str, Any]:
    return respond(exc.status, {"error": {"code": exc.code, "message": exc.message}})


def json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "INVALID_JSON", "Body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_BODY", "Body must be a JSON object")
    return body


def claims(event: dict[str, Any]) -> dict[str, Any]:
    authorizer = event.get("requestContext", {}).get("authorizer", {}) or {}
    return (authorizer.get("jwt") or {}).get("claims") or authorizer.get("claims") or {}


def groups(event: dict[str, Any]) -> set[str]:
    """Cognito groups from the JWT. HTTP API serialises lists as '[A B]'."""
    raw = claims(event).get("cognito:groups", [])
    if isinstance(raw, str):
        raw = raw.strip("[]").replace(",", " ").split()
    return {g.strip() for g in raw if g and g.strip()}


def require_group(event: dict[str, Any], allowed: set[str]) -> set[str]:
    user_groups = groups(event)
    if not user_groups & allowed:
        raise ApiError(403, "FORBIDDEN", f"Requires one of: {', '.join(sorted(allowed))}")
    return user_groups


def encode_token(key: dict[str, Any] | None) -> str | None:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(from_ddb(key)).encode()).decode()


def decode_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(400, "INVALID_NEXT_TOKEN", "nextToken is not valid") from exc
