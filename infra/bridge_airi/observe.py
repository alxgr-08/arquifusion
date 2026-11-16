"""Inspect the named job and safe counters or ask Cloud Scheduler for one immediate execution."""

import argparse
import json
import re

import google.auth
from google.auth.transport.requests import AuthorizedSession

JOB = "projects/airi-erp/locations/us-east1/jobs/af-bridge"
SCHEDULE = "projects/airi-erp/locations/us-east1/jobs/af-bridge-hourly"


def session():
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"], quota_project_id="airi-erp"
    )
    return AuthorizedSession(creds)


def fetch(http, uri):
    r = http.get(uri, timeout=15)
    if not r.ok:
        raise RuntimeError(f"Google HTTP {r.status_code}: {r.json().get('error', {}).get('message', '')[:240]}")
    return r.json()


def executions(http):
    data = fetch(http, "https://run.googleapis.com/v2/" + JOB + "/executions?pageSize=20")
    return [
        {
            "name": x["name"].split("/")[-1],
            "created": x.get("createTime"),
            "ended": x.get("completionTime"),
            "succeeded": x.get("succeededCount", 0),
            "failed": x.get("failedCount", 0),
            "condition": [
                [v.get("state"), v.get("reason"), v.get("message", "")[:180]]
                for v in x.get("conditions", [])
                if v.get("type") == "Completed"
            ],
        }
        for x in data.get("executions", [])
    ]


def logs(http):
    params = {
        "resourceNames": ["projects/airi-erp"],
        "filter": (
            'resource.type="cloud_run_job" AND resource.labels.job_name="af-bridge" '
            'AND resource.labels.location="us-east1"'
        ),
        "orderBy": "timestamp desc",
        "pageSize": 150,
    }
    result = http.post("https://logging.googleapis.com/v2/entries:list", json=params, timeout=20)
    result.raise_for_status()
    out = []
    for e in result.json().get("entries", []):
        p = e.get("jsonPayload", {})
        line = e.get("textPayload", "").strip()
        if line.startswith("{"):
            try:
                p = json.loads(line)
            except ValueError:
                continue
        safe = {
            k: p[k]
            for k in ("utc", "sha256", "loadJob", "queryJob", "sourceRows", "kpi", "deliveriesAcknowledged")
            if k in p
        }
        if safe:
            out.append({"execution": e.get("labels", {}).get("run.googleapis.com/execution_name"), **safe})
        elif re.match(
            r"^(ValueError|RuntimeError|ImportError|ModuleNotFoundError|google\.api_core\.exceptions\.\w+|requests\.exceptions\.\w+|xml\.etree\.ElementTree\.ParseError):",
            line,
        ):
            if not re.search(r"bearer|token=|signature=|authorization:|receiptHandle", line, re.I):
                out.append({"failure": line[:400]})
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "logs", "trigger"))
    action = parser.parse_args().action
    http = session()
    if action == "trigger":
        r = http.post("https://cloudscheduler.googleapis.com/v1/" + SCHEDULE + ":run", json={}, timeout=20)
        r.raise_for_status()
        print("Accepted by Cloud Scheduler", r.status_code)
    elif action == "logs":
        print(json.dumps(logs(http), indent=2))
    else:
        s = fetch(http, "https://cloudscheduler.googleapis.com/v1/" + SCHEDULE)
        print(
            json.dumps(
                {
                    "schedule": s.get("schedule"),
                    "state": s.get("state"),
                    "lastAttempt": s.get("lastAttemptTime"),
                    "next": s.get("scheduleTime"),
                    "executions": executions(http),
                },
                indent=2,
            )
        )
