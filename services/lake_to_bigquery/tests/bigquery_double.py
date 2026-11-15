"""Offline BigQuery load/query semantics used by bridge tests. Only this test client decodes Parquet."""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow.parquet as pq
from google.api_core.exceptions import BadRequest, Conflict, NotFound, ServiceUnavailable

STAGING = "af_bridge_stage"


def decoded_parquet(data):
    parquet = pq.ParquetFile(io.BytesIO(data), coerce_int96_timestamp_unit="us")
    rows = parquet.read().to_pylist()
    # BigQuery maps physical INT96 to TIMESTAMP. Arrow exposes it without a zone on this fixture.
    if "timestamp" in parquet.schema.names:
        col = parquet.schema.column(parquet.schema.names.index("timestamp"))
        if col.physical_type == "INT96":
            for row in rows:
                if row["timestamp"] is not None:
                    row["timestamp"] = row["timestamp"].replace(tzinfo=UTC)
    return rows


class BigQueryDouble:
    project = "local-arquifusion"

    def __init__(self):
        self.jobs = {}
        self.tables = {}
        self.stages = {}
        self.next_outcome = "success"
        self.submissions = []
        self.parquet_uploads = []
        self.queries = []
        self.dataset = SimpleNamespace(location="US", default_table_expiration_ms=86_400_000)

    def get_dataset(self, name):
        assert name == f"{self.project}.{STAGING}"
        return self.dataset

    def list_rows(self, table, *, page_size):
        assert page_size > 0
        return iter(self.stages[table])

    def query(self, statement, *, job_config, location):
        assert location == "US" and job_config.use_legacy_sql is False and job_config.destination is None
        assert statement.startswith("SELECT ") and "WHERE LOWER(messageId) IN UNNEST(@ids)" in statement
        destination = self.project + ".af_lake.telemetry"
        assert f"FROM `{destination}`" in statement
        [param] = job_config.query_parameters
        assert param.name == "ids" and param.array_type == "STRING"
        self.queries.append(param.values)
        result = [
            {**row, "timestamp": datetime.fromisoformat(row["timestamp"])}
            for row in self.tables.get(destination, [])
            if row["messageId"].lower() in param.values
        ]
        return SimpleNamespace(result=lambda *, timeout: iter(result))

    def get_job(self, job_id, *, project, location):
        assert project == self.project and location == "US"
        if job_id not in self.jobs:
            raise NotFound("local job not found")
        return self.jobs[job_id]

    def load_table_from_file(self, file, destination, *, size, job_config, job_id, location):
        assert location == "US" and job_config.max_bad_records == 0 and not job_config.schema_update_options
        data = file.read()
        assert len(data) == size
        if job_id in self.jobs:
            raise Conflict("local already exists")
        staging = job_config.source_format == "PARQUET"
        if staging:
            assert destination.startswith(f"{self.project}.{STAGING}.telemetry_")
            assert job_config.create_disposition == "CREATE_IF_NEEDED" and job_config.write_disposition == "WRITE_EMPTY"
            assert destination not in self.stages
            self.parquet_uploads.append(data)
            rows = decoded_parquet(data)
        else:
            assert job_config.source_format == "NEWLINE_DELIMITED_JSON"
            assert job_config.create_disposition == "CREATE_NEVER" and job_config.write_disposition == "WRITE_APPEND"
            assert job_config.autodetect is False and not job_config.ignore_unknown_values
            assert not job_config.schema and job_config.time_partitioning is None and not job_config.clustering_fields
            assert destination.startswith(self.project + ".af_lake.")
            rows = [json.loads(line) for line in data.splitlines()]
        self.submissions.append(job_id)
        outcome, self.next_outcome = self.next_outcome, "success"
        job = SimpleNamespace(
            job_type="load",
            job_id=job_id,
            location=location,
            project=self.project,
            source_format=job_config.source_format,
            create_disposition=job_config.create_disposition,
            write_disposition=job_config.write_disposition,
            destination=destination,
            state="DONE",
            error_result=None,
            output_rows=len(rows),
            result=Mock(),
        )
        self.jobs[job_id] = job
        if outcome in {"backendError", "invalid"}:
            job.error_result = {"reason": outcome}
            job.result.side_effect = BadRequest("local failed completed job")
        elif outcome == "running":
            job.result.side_effect = TimeoutError("local job still running")
            job.state = "RUNNING"
        else:
            store = self.stages if staging else self.tables
            store.setdefault(destination, []).extend(rows)
            if outcome == "submit_timeout":
                raise ServiceUnavailable("local response was lost after acceptance")
            if outcome == "result_timeout":
                job.result.side_effect = TimeoutError("local response lost after completion")
            if outcome == "conflict":
                raise Conflict("concurrent create won")
        return job
