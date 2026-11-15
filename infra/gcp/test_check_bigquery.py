"""Exercise the metadata-only checker without a real project, account or SDK."""

import sys

import check_bigquery as checker
import pytest


@pytest.mark.parametrize("dataset_region,expected", [("US", 0), ("EU", 1)])
def test_only_project_metadata_is_requested(monkeypatch, dataset_region, expected):
    project = "test-bridge-local"
    calls = []

    def query(command):
        calls.append(command)
        if command[1:3] == ["projects", "describe"]:
            return {"projectId": project, "lifecycleState": "ACTIVE"}, "VISIBLE"
        if command[1:3] == ["services", "list"]:
            return [{"config": {"name": "bigquery.googleapis.com"}}], "VISIBLE"
        assert command[0] == "bq" and command[3] == "show"
        assert command[-1].startswith(project + ":")
        return {"location": dataset_region, "numRows": "0", "modelType": "KMEANS"}, "VISIBLE"

    monkeypatch.setattr(sys, "argv", ["check_bigquery.py", project])
    monkeypatch.setattr(checker.shutil, "which", lambda name: name)
    monkeypatch.setattr(checker, "run_json", query)
    assert checker.main() == expected
    assert len(calls) == 2 + len(checker.DATASETS) + len(checker.TABLES) + len(checker.MODELS)


def test_project_denied_never_probes_other_projects(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_bigquery.py", "test-bridge-local"])
    monkeypatch.setattr(checker.shutil, "which", lambda name: name)
    calls = []

    def denied(command):
        calls.append(command)
        return None, "ACCESS_DENIED"

    monkeypatch.setattr(checker, "run_json", denied)
    assert checker.main() == 2
    assert len(calls) == 1


@pytest.mark.parametrize("ttl,expected", [("86400000", 0), (None, 1), ("0", 1), ("invalid", 1)])
def test_bridge_preflight_checks_staging_expiry_and_selected_return_tables(monkeypatch, ttl, expected):
    project = "arquifusion"
    calls = []

    def query(command):
        calls.append(command)
        if command[1:3] == ["projects", "describe"]:
            return {"projectId": project, "lifecycleState": "ACTIVE"}, "VISIBLE"
        if command[1:3] == ["services", "list"]:
            return [{"config": {"name": "bigquery.googleapis.com"}}], "VISIBLE"
        assert command[0] == "bq" and command[3] == "show"
        assert command[-1].startswith(project + ":")
        return {"location": "US", "defaultTableExpirationMs": ttl, "numRows": "0"}, "VISIBLE"

    monkeypatch.setattr(sys, "argv", ["check_bigquery.py", project, "--bridge"])
    monkeypatch.setattr(checker.shutil, "which", lambda name: name)
    monkeypatch.setattr(checker, "run_json", query)
    assert checker.main() == expected
    objects = {command[-1] for command in calls if command[0] == "bq"}
    assert {
        "arquifusion:af_bridge_stage",
        "arquifusion:af_out.machine_clusters",
        "arquifusion:af_out.fuel_trends",
    } <= objects
