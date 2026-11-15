"""Inspect only the named ArquiFusion project and its expected BigQuery objects. No jobs are submitted."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

DATASETS = ("af_lake", "af_ml", "af_out")
TABLES = (
    "af_lake.telemetry",
    "af_lake.incidents",
    "af_lake.parts_movements",
    "af_out.daily_kpis",
    "af_out.model_baselines",
    "af_out.parts_forecast",
)
MODELS = ("af_ml.fuel_lpt_arima", "af_ml.machine_clusters", "af_ml.parts_demand")


def run_json(command):
    env = {**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1", "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true"}
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if result.returncode:
        message = (result.stderr + result.stdout).lower()
        if "access denied" in message or "permission" in message or "forbidden" in message:
            return None, "ACCESS_DENIED"
        if "not found" in message or "notfound" in message:
            return None, "NOT_FOUND_OR_HIDDEN"
        return None, "CLI_OR_SESSION_ERROR"
    try:
        return json.loads(result.stdout), "VISIBLE"
    except ValueError:
        return None, "UNEXPECTED_RESPONSE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="Verified public Google Cloud project ID of the team")
    project = parser.parse_args().project
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        parser.error("Use a valid project ID confirmed by the team.")
    cloud, bq = shutil.which("gcloud"), shutil.which("bq")
    if not cloud or not bq:
        print("MISSING_CLI: gcloud and bq are required. Use an existing authenticated installation.")
        return 2
    data, status = run_json([cloud, "projects", "describe", project, "--format=json(projectId,lifecycleState)"])
    if not data or data.get("projectId") != project or data.get("lifecycleState") != "ACTIVE":
        print(f"Project unverified: {status}")
        return 2
    print(f"Project {project} ACTIVE")
    services, status = run_json(
        [
            cloud,
            "services",
            "list",
            "--enabled",
            "--project",
            project,
            "--filter=config.name:bigquery.googleapis.com",
            "--format=json(config.name)",
        ]
    )
    if services is None:
        print(f"BigQuery service metadata: {status}")
    else:
        print("BigQuery API enabled:", any(x["config"]["name"] == "bigquery.googleapis.com" for x in services))
    missing = services is None or not any(x["config"]["name"] == "bigquery.googleapis.com" for x in services)
    for name in (*DATASETS, *TABLES, *MODELS):
        command = [bq, f"--project_id={project}", "--format=prettyjson", "show"]
        if name in MODELS:
            command.append("--model")
        obj, status = run_json([*command, f"{project}:{name}"])
        if obj is None:
            print(name, status)
            missing = True
        elif name in DATASETS:
            print(name, status, "location", obj.get("location"))
            missing |= obj.get("location") != "US"
        elif name in MODELS:
            print(name, status, obj.get("modelType"))
        else:
            print(name, status, "rows", obj.get("numRows"))
    return int(missing)


if __name__ == "__main__":
    sys.exit(main())
