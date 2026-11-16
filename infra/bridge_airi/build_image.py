"""Submit exactly the worker, Dockerfile and pinned requirements to a private, regional Cloud Build."""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from uuid import UUID

import google.auth
from google.auth.transport.requests import AuthorizedSession

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "build/bridge-permanent"
PARENT = "projects/airi-erp/locations/us-east1"
API = "https://cloudbuild.googleapis.com/v1/"
IMAGE = "us-east1-docker.pkg.dev/airi-erp/af-bridge/worker"
SOURCE = ("Dockerfile", "requirements.txt", "worker.py")


def build_spec():
    files = {p: base64.b64encode((HERE / p).read_bytes()).decode("ascii") for p in SOURCE}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    tag = IMAGE + ":src-" + digest[:16]
    write = (
        "import base64,json,pathlib,sys\n"
        "for name,data in json.loads(''.join(sys.argv[1:])).items():\n"
        "    pathlib.Path(name).write_bytes(base64.b64decode(data))"
    )
    encoded = json.dumps(files)
    chunks = [encoded[i : i + 6000] for i in range(0, len(encoded), 6000)]
    return {
        "serviceAccount": "projects/airi-erp/serviceAccounts/af-bridge-builder@airi-erp.iam.gserviceaccount.com",
        "steps": [
            {"name": "python:3.12-slim", "entrypoint": "python", "args": ["-c", write, *chunks]},
            {"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
        ],
        "images": [tag],
        "timeout": "600s",
        "options": {"logging": "CLOUD_LOGGING_ONLY"},
        "tags": ["arquifusion", "airi-bridge"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", type=UUID, help="Regional Cloud Build ID")
    args = parser.parse_args()
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"], quota_project_id="airi-erp"
    )
    http = AuthorizedSession(creds)
    if args.resume:
        build_id = str(args.resume)
    else:
        response = http.post(API + PARENT + "/builds", json=build_spec(), timeout=40)
        if not response.ok:
            error = response.json().get("error", {})
            raise RuntimeError(f"Cloud Build {response.status_code}: {error.get('message', '')[:550]}")
        build_id = str(UUID(response.json()["metadata"]["build"]["id"]))
    name = PARENT + "/builds/" + build_id
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "build-operation.txt").write_text(name, encoding="utf-8")
    print("Cloud Build:", name, flush=True)
    for _ in range(100):
        response = http.get(API + name, timeout=20)
        response.raise_for_status()
        result = response.json()
        if "airi-bridge" not in result.get("tags", []):
            raise ValueError("Unexpected Cloud Build task")
        if result.get("status") not in ("QUEUED", "WORKING", "PENDING", "STATUS_UNKNOWN"):
            if result.get("status") != "SUCCESS":
                detail = result.get("statusDetail", "")[:200]
                raise RuntimeError(f"Build {result.get('id')} ended as {result.get('status')}: {detail}")
            (image,) = result["results"]["images"]
            evidence = {
                "id": result["id"],
                "digest": image["digest"],
                "image": image["name"],
                "status": result["status"],
            }
            (OUT / "image.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(json.dumps(evidence))
            return
        time.sleep(7)
    raise TimeoutError("Continue observing the same operation with --resume")


if __name__ == "__main__":
    main()
