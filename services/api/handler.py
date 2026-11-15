"""af-dev-api: REST endpoints of contracts/api.md (P4, P5, P7, P10). Owner: Rol 2.

Trigger: API Gateway HTTP API (payload v2) with a JWT authorizer for Google (issuer https://accounts.google.com,
audience = OAuth Client ID). Roles come from GRANT# items in DynamoDB (af_shared.grants), not from the token.
Env: AF_TABLE_NAME, AF_AUTH_DISABLED=true only for local tests without a token.
POST /simulator/faults is served by af-dev-fault-injector, not here.
"""

from __future__ import annotations

from typing import Any

import routes
from af_shared import grants
from af_shared.ddb import get_table, now_iso
from af_shared.http import ApiError, Reply, error, respond
from af_shared.log import get_logger

logger = get_logger("api")
_clients: dict[str, Any] = {}


def route(event: dict[str, Any], table: Any, sfn: Any, now: str) -> dict[str, Any]:
    http = event.get("requestContext", {}).get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    stage = event.get("requestContext", {}).get("stage")
    if stage and stage != "$default" and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1 :]
    if method == "OPTIONS":
        return respond(204, {})
    try:
        (_, _, fn, allowed, status), params = routes.match(method, path)
        ctx = routes.Ctx(table, sfn, now, event, params)
        if grants.auth_required():
            ctx.principal = grants.load(table, event)
            if not ctx.principal.grants:
                raise ApiError(403, "NO_GRANT", "This account has no access to ArquiFusion")
            grants.require(ctx.principal, allowed, routes.operation_of(ctx))
        result = fn(ctx)
        if isinstance(result, Reply):
            return respond(result.status or status, result.body, result.headers)
        return respond(status, result)
    except ApiError as exc:
        if exc.status >= 500:
            logger.exception("api error", path=path)
        return error(exc)
    except Exception:
        logger.exception("unhandled api error", path=path, method=method)
        return error(ApiError(500, "INTERNAL", "Unexpected error"))


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    if not _clients:
        import boto3

        _clients["table"] = get_table()
        _clients["sfn"] = boto3.client("stepfunctions")
    return route(event, _clients["table"], _clients["sfn"], now_iso())
