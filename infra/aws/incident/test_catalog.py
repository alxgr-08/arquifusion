"""Catalog compatibility tests using the production simulator and incident readers."""

import base64
import gzip
import hashlib
import io
import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from af_shared import config
from af_shared.fakes import FakeSsm, Recorder, demo_fleet_raw

from infra.aws.incident.incident_catalog import CATALOG_KEY, IncidentParameters

PARAMETER = "/af/incident/fleet-catalog"
BUCKET = "af-dev-catalog-000000000000"
OWNER = "000000000000"


def catalog_manifest(raw, transport):
    document = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    checksum = hashlib.sha256(document).hexdigest()
    if transport == "s3":
        return {
            "format": "s3",
            "sha256": checksum,
            "bucket": BUCKET,
            "key": CATALOG_KEY,
            "versionId": "test-v1",
        }, document
    data = base64.b64encode(gzip.compress(document, mtime=0)).decode("ascii")
    return {"format": "gzip+base64", "sha256": checksum, "data": data}, document


@pytest.mark.parametrize("transport", ["inline", "s3"])
def test_catalog_preserves_simulator_outputs_and_fleet(transport, load_handler):
    raw = demo_fleet_raw()
    ref, document = catalog_manifest(raw, transport)
    assert len(json.dumps(ref).encode()) <= 4096
    s3 = Mock()
    s3.get_object.return_value = {"Body": io.BytesIO(document)}
    old = config.ParamStore(client=FakeSsm({config.FLEET_CONFIG: raw}))
    client = FakeSsm({PARAMETER: ref})
    new = IncidentParameters(PARAMETER, client=client, s3=s3, bucket=BUCKET, owner=OWNER)
    assert new.get_json(PARAMETER) == new.get_json(config.FLEET_CONFIG) == old.get_json(config.FLEET_CONFIG) == raw
    assert new.get_json(config.SIM_FAULTS, default={"faults": []}) == {"faults": []}

    service = load_handler("simulator")

    def telemetry(store):
        sink = Recorder()
        service.run(
            store,
            service.Publisher("iot", {"iot-data": sink}),
            fast_interval=0,
            clock=lambda: datetime(2026, 11, 14, tzinfo=UTC),
        )
        return sink.of("publish")

    messages = telemetry(new)
    assert messages == telemetry(old)
    assert len(messages) == len(raw["machines"]) == 12
    if transport == "s3":
        s3.get_object.assert_called_once_with(
            Bucket=BUCKET, Key=CATALOG_KEY, VersionId="test-v1", ExpectedBucketOwner=OWNER
        )
        assert s3.get_object.return_value["Body"].closed
    else:
        s3.get_object.assert_not_called()


def test_rejects_corrupt_catalog_and_recovers_on_refresh():
    raw = demo_fleet_raw()
    ref, _ = catalog_manifest(raw, "inline")
    client = FakeSsm({PARAMETER: {**ref, "sha256": "0" * 64}})
    store = IncidentParameters(PARAMETER, client=client)
    with pytest.raises(ValueError, match="SHA-256"):
        store.get_json(config.FLEET_CONFIG)
    client.put_parameter(Name=PARAMETER, Value=json.dumps(ref))
    assert store.get_json(config.FLEET_CONFIG, fresh=True) == raw


@pytest.mark.parametrize("field, value", [("bucket", "outside-project"), ("key", "other.json"), ("versionId", "null")])
def test_refuses_unexpected_s3_location_without_fetching(field, value):
    ref, _ = catalog_manifest(demo_fleet_raw(), "s3")
    s3 = Mock()
    store = IncidentParameters(
        PARAMETER, client=FakeSsm({PARAMETER: {**ref, field: value}}), s3=s3, bucket=BUCKET, owner=OWNER
    )
    with pytest.raises(ValueError, match="Unexpected catalog"):
        store.get_json(config.FLEET_CONFIG)
    s3.get_object.assert_not_called()
