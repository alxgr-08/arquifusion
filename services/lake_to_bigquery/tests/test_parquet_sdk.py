"""Exercise google-cloud-bigquery's real multipart transport and job parser with an in-process HTTP response."""

import json
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

import requests
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

from services.lake_to_bigquery.load_jobs import load_attempt


def test_sdk_emits_unmodified_parquet_and_reads_native_job_metadata(monkeypatch):
    data = Path(__file__).with_name("firehose-syn-9001.parquet").read_bytes()
    uploaded = []

    def respond(request, **kwargs):
        response = requests.Response()
        response.request, response.url = request, request.url
        if request.method == "GET":
            assert "/jobs/af_stage_fixture" in request.url
            response.status_code = 404
            payload = {"error": {"code": 404, "message": "missing local job"}}
        else:
            assert request.method == "POST" and "uploadType=multipart" in request.url
            content_type = request.headers["content-type"]
            if isinstance(content_type, str):
                content_type = content_type.encode()
            envelope = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + content_type + b"\r\n\r\n" + request.body
            )
            metadata, file = envelope.iter_parts()
            uploaded.append(file.get_payload(decode=True))
            payload = json.loads(metadata.get_payload(decode=True))
            config = payload["configuration"]["load"]
            assert config["sourceFormat"] == "PARQUET" and config["writeDisposition"] == "WRITE_EMPTY"
            assert config["createDisposition"] == "CREATE_IF_NEEDED" and config["maxBadRecords"] == 0
            assert config["destinationTable"] == {
                "projectId": "local-arquifusion",
                "datasetId": "af_bridge_stage",
                "tableId": "fixture",
            }
            payload.update(status={"state": "DONE"}, statistics={"load": {"outputRows": "1"}})
            response.status_code = 200
        response._content = json.dumps(payload).encode()
        return response

    credentials = AnonymousCredentials()
    with AuthorizedSession(credentials) as session:
        session.trust_env = False
        monkeypatch.setattr(session, "send", respond)
        client = bigquery.Client(
            project="local-arquifusion", location="US", credentials=credentials, _http=session
        )
        config = bigquery.LoadJobConfig(
            source_format="PARQUET",
            create_disposition="CREATE_IF_NEEDED",
            write_disposition="WRITE_EMPTY",
            max_bad_records=0,
        )
        now = datetime.now(UTC)
        job = load_attempt(
            client,
            "local-arquifusion.af_bridge_stage.fixture",
            data,
            config,
            "af_stage_fixture",
            now,
            now=now,
            timeout=2,
        )
    assert uploaded == [data] and job.output_rows == 1 and job.job_id == "af_stage_fixture"
