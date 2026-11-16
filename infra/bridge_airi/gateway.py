"""Generate narrow native AWS service integrations. The authorizer receives only the bearer token."""

import json
from pathlib import Path

ACCOUNT = "975050277936"
REGION = "us-east-1"
PREFIX = f"arn:aws:apigateway:{REGION}:"
BUCKET = f"af-dev-arquifusion-lake-{ACCOUNT}-{REGION}-an"
TABLE = "af-dev-arquifusion"
QUEUE = "af-dev-airi-delivery"
OP = "AF-BRIDGE-PERU"
BINARY = "application/octet-stream"
OUT = Path(__file__).resolve().parents[2] / "build/bridge-permanent/gateway.json"
METRICS = (
    "utilizationPct",
    "operatingHours",
    "idleHours",
    "downHours",
    "overuseHours",
    "tonsMoved",
    "fuelLiters",
    "fuelLitersPerTon",
    "fuelLitersPerTonModel",
    "costPerHour",
)


def schema(props):
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def svc(uri, *, action="POST", template=None, mime=None, parameters=None):
    params = dict(parameters or {})
    if mime:
        params["integration.request.header.Content-Type"] = "'" + mime + "'"
    integration = {
        "type": "aws",
        "httpMethod": action,
        "uri": PREFIX + uri,
        "credentials": f"arn:aws:iam::{ACCOUNT}:role/LabRole",
        "passthroughBehavior": "never" if template else "when_no_match",
        "requestParameters": params,
        "responses": {
            str(code): {"statusCode": str(code)} for code in (206, 400, 403, 404, 409, 412, 413, 416, 429, 500, 503)
        },
    }
    integration["responses"]["2\\d{2}"] = {"statusCode": "200"}
    if action == "GET":
        # Keep partial and full S3 binary responses distinct.
        integration["responses"].pop("2\\d{2}")
        integration["responses"]["200"] = {"statusCode": "200"}
        for code in ("200", "206"):
            integration["responses"][code]["responseParameters"] = {
                "method.response.header.Content-Type": "'application/octet-stream'",
                "method.response.header.Content-Range": "integration.response.header.Content-Range",
            }
    integration["responses"]["default"] = {"statusCode": "502"}
    if template is not None:
        integration["requestTemplates"] = {"application/json": template}
    return integration


def method(integration, body=None, params=()):
    headers = {k: {"type": "string"} for k in ("Content-Type", "Content-Range")}
    value = {
        "security": [{"googleMachine": []}],
        "produces": ["application/json", BINARY],
        "parameters": list(params),
        "x-amazon-apigateway-request-validator": "validate",
        "responses": {
            str(n): {"description": str(n)} for n in (200, 206, 400, 403, 404, 409, 412, 413, 416, 429, 500, 502, 503)
        },
        "x-amazon-apigateway-integration": integration,
    }
    if integration["httpMethod"] == "GET":
        for code in ("200", "206"):
            value["responses"][code]["headers"] = headers
    if body:
        value["parameters"].append(
            {"name": "body", "in": "body", "required": True, "schema": {"$ref": "#/definitions/" + body}}
        )
    return value


def ddb(action, template):
    return svc("dynamodb:action/" + action, mime="application/x-amz-json-1.0", template=template)


def spec():
    definitions = {"Receipt": schema({"receipt": {"type": "string", "minLength": 1, "maxLength": 2048}})}
    ids = {
        "machineId": {"type": "string", "pattern": "^BRG-[0-9]{4}$"},
        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
    }
    definitions["Key"] = schema(ids)
    props = dict(
        ids,
        model={"type": "string", "pattern": "^[A-Za-z0-9 _.-]{1,80}$"},
        messageId={"type": "string", "pattern": "^[0-9a-f-]{36}$"},
        sourceReadings={"type": "integer", "minimum": 1},
        dayReadings={"type": "integer", "minimum": 1},
        fuelAnomaly={"type": "boolean"},
    )
    props.update({k: {"type": "number", "minimum": 0} for k in METRICS})
    # Nullable metrics are omitted in the JSON request and stored only when present.
    definitions["Kpi"] = schema(props)
    for optional in ("tonsMoved", "fuelLiters", "fuelLitersPerTon", "fuelLitersPerTonModel", "costPerHour"):
        definitions["Kpi"]["required"].remove(optional)

    operations = {}
    queue = f"sqs:path/{ACCOUNT}/{QUEUE}"
    for path, form, model in (
        ("lease", "Action=ReceiveMessage&Version=2012-11-05&MaxNumberOfMessages=1&WaitTimeSeconds=1", None),
        (
            "complete",
            "Action=DeleteMessage&Version=2012-11-05&ReceiptHandle=$util.urlEncode($input.path('$.receipt'))",
            "Receipt",
        ),
    ):
        operations["/" + path] = {
            "post": method(svc(queue, mime="application/x-www-form-urlencoded", template=form), model)
        }
    segments = ("year", "month", "day", "hour", "name")
    suffix = "/".join("{" + p + "}" for p in segments)
    params = [{"name": p, "in": "path", "required": True, "type": "string"} for p in segments]
    for head in ("Range", "If-Match"):
        params.append({"name": head, "in": "header", "required": True, "type": "string"})
    mappings = {"integration.request.path." + p: "method.request.path." + p for p in segments}
    mappings.update({"integration.request.header." + h: "method.request.header." + h for h in ("Range", "If-Match")})
    operations["/files/" + suffix] = {
        "get": method(svc(f"s3:path/{BUCKET}/records/{suffix}", action="GET", parameters=mappings), params=params)
    }
    pk = {"S": "MACHINE#$m"}
    status_key = {"PK": pk, "SK": {"S": "KPI#$d"}}
    prep = "#set($m=$input.path('$.machineId'))\n#set($d=$input.path('$.date'))\n"
    status = {
        "TableName": TABLE,
        "Key": status_key,
        "ConsistentRead": True,
        "ProjectionExpression": "operationId,sourceDayReadings,sourceProject,sourceMessageId",
    }
    operations["/status"] = {"post": method(ddb("GetItem", prep + json.dumps(status)), "Key")}
    item = dict(
        status_key,
        GSI1PK={"S": "OP#" + OP + "#KPI#$d"},
        GSI1SK={"S": "$m"},
        machineId={"S": "$m"},
        operationId={"S": OP},
        model={"S": "$input.path('$.model')"},
        date={"S": "$d"},
        source={"S": "bigquery"},
        sourceProject={"S": "airi-erp"},
        sourceMessageId={"S": "$input.path('$.messageId')"},
        sourceReadings={"N": "$input.path('$.sourceReadings')"},
        sourceDayReadings={"N": "$input.path('$.dayReadings')"},
    )
    check = {
        "TableName": TABLE,
        "Key": {"PK": pk, "SK": {"S": "PROFILE"}},
        "ConditionExpression": "operationId = :op",
        "ExpressionAttributeValues": {":op": {"S": OP}},
    }
    put = {
        "TableName": TABLE,
        "Item": item,
        "ConditionExpression": (
            "attribute_not_exists(PK) OR (operationId = :op AND sourceProject = :pr AND sourceDayReadings < :n)"
        ),
        "ExpressionAttributeValues": {
            ":op": {"S": OP},
            ":pr": {"S": "airi-erp"},
            ":n": {"N": "$input.path('$.dayReadings')"},
        },
    }
    item["__fields__"] = "MARKER"
    result = json.dumps({"TransactItems": [{"ConditionCheck": check}, {"Put": put}]}, separators=(",", ":"))
    extra = ['"fuelAnomaly":{"BOOL":$input.json(\'$.fuelAnomaly\')}']
    for k in METRICS:
        extra.append(f'#if($inputRoot.containsKey("{k}")), "{k}":{{"N":"$input.path(\'$.{k}\')"}}#end')
    result = result.replace('"__fields__":"MARKER"', "\n".join(extra))
    operations["/kpi"] = {
        "post": method(ddb("TransactWriteItems", prep + "#set($inputRoot=$input.path('$'))\n" + result), "Kpi")
    }
    function = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:af-dev-airi-auth"
    authorizer = f"{PREFIX}lambda:path/2015-03-31/functions/{function}/invocations"
    return {
        "swagger": "2.0",
        "info": {"title": "af-dev-airi-bridge", "version": "1.0"},
        "schemes": ["https"],
        "consumes": ["application/json"],
        "x-amazon-apigateway-binary-media-types": [BINARY],
        "definitions": definitions,
        "x-amazon-apigateway-request-validators": {
            "validate": {"validateRequestBody": True, "validateRequestParameters": True}
        },
        "securityDefinitions": {
            "googleMachine": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "x-amazon-apigateway-authtype": "custom",
                "x-amazon-apigateway-authorizer": {
                    "type": "token",
                    "authorizerUri": authorizer,
                    "authorizerResultTtlInSeconds": 60,
                },
            }
        },
        "paths": operations,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the deployed OpenAPI artifact's byte hash on both Windows and Linux.
    OUT.write_text(json.dumps(spec(), indent=2), encoding="utf-8", newline="\r\n")
