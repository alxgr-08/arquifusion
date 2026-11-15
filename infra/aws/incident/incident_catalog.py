"""Incident deployment adapter. Rol 2 still receives the full FleetConfig through its store."""

import base64
import gzip
import hashlib
import json
import os
from contextlib import closing
from functools import cache
from typing import Any

import boto3
from af_shared import config
from af_shared.ddb import get_table, now_iso
from af_shared.log import get_logger

logger = get_logger("incident-tasks")
CATALOG_KEY = "catalogs/fleet-config.json"


class IncidentParameters(config.ParamStore):
    def __init__(self, parameter: str, *, client=None, s3=None, bucket=None, owner=None) -> None:
        super().__init__(client=client)
        self.parameter = parameter
        self.bucket = bucket
        self.owner = owner
        self.s3 = s3
        self._manifest = None
        self._fleet = None

    def get_json(self, name: str, default: Any = None, *, fresh: bool = False) -> Any:
        if name not in (self.parameter, config.FLEET_CONFIG):
            return super().get_json(name, default, fresh=fresh)
        manifest = super().get_json(self.parameter, fresh=fresh)
        if not isinstance(manifest, dict):
            raise ValueError("Invalid incident catalog manifest")
        if not fresh and manifest is self._manifest:
            return self._fleet

        if manifest["format"] == "gzip+base64":
            raw = gzip.decompress(base64.b64decode(manifest["data"], validate=True))
        elif manifest["format"] == "s3":
            version = manifest["versionId"]
            if (
                not self.bucket
                or not self.owner
                or manifest["bucket"] != self.bucket
                or manifest["key"] != CATALOG_KEY
                or not isinstance(version, str)
                or version.strip() in ("", "null")
            ):
                raise ValueError("Unexpected catalog bucket, object or version")
            if self.s3 is None:
                self.s3 = boto3.client("s3")
            response = self.s3.get_object(
                Bucket=self.bucket, Key=CATALOG_KEY, VersionId=version, ExpectedBucketOwner=self.owner
            )
            with closing(response["Body"]) as body:
                raw = body.read()
        else:
            raise ValueError("Unsupported incident catalog format")

        if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
            raise ValueError("Fleet catalog SHA-256 mismatch")
        self._fleet = json.loads(raw.decode("utf-8"))
        self._manifest = manifest
        return self._fleet


@cache
def _runtime():
    return get_table(), IncidentParameters(
        os.environ["AF_INCIDENT_CATALOG"],
        bucket=os.environ.get("AF_CATALOG_BUCKET"),
        owner=os.environ.get("AF_CATALOG_OWNER"),
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    from handler import dispatch  # Rol 2 module at the root of the Lambda ZIP.

    table, store = _runtime()
    action = event.get("action", "")
    incident = event.get("incident") or {}
    logger.append_keys(action=action, incidentId=incident.get("incidentId"), machineId=incident.get("machineId"))
    result = dispatch(action, event, table, store, now_iso())
    logger.info("task done")
    return result
