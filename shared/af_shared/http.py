"""API Gateway (HTTP API, payload v2) helpers: responses, errors, headers and JWT claims."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from af_shared.ddb import from_ddb

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type,If-Match",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Expose-Headers": "ETag,Retry-After",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}


class Reply:
    """A route result with its own status and/or headers (e.g. ETag)."""

    def __init__(self, body: Any, status: int | None = None, headers: dict[str, str] | None = None) -> None:
        self.body, self.status, self.headers = body, status, headers or {}


def respond(status: int, body: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "statusCode": status,
        # In Lambda the native HTTP API configuration owns CORS for the registered origin.
        "headers": {
            "Content-Type": "application/json",
            **({} if os.getenv("AWS_LAMBDA_FUNCTION_NAME") else CORS),
            **(headers or {}),
        },
        "body": json.dumps(from_ddb(body), ensure_ascii=False),
    }


def error(exc: ApiError) -> dict[str, Any]:
    return respond(exc.status, {"error": {"code": exc.code, "message": exc.message}}, exc.headers)


def header(event: dict[str, Any], name: str) -> str | None:
    """Request header, case-insensitive (HTTP API v2 lowercases them, REST v1 does not)."""
    wanted = name.lower()
    for key, value in (event.get("headers") or {}).items():
        if key.lower() == wanted:
            return value
    return None


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
