"""Prepare a local live web release or read-only HTTP probes. Never publish cloud data."""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from infra.hosting import prepare
except ModuleNotFoundError:
    import prepare

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://demo.d2ygcm34t8aku9.amplifyapp.com"
CLIENT_ID = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
API_URL = re.compile(r"https://[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com/dev")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def check_frontend_inputs(root=ROOT):
    """Do not read local dotenv files: they may contain credentials."""
    unexpected = sorted(p.name for p in (root / "frontend").glob(".env*") if p.name != ".env.example")
    require(not unexpected, "Use a clean frontend without local .env files; names found: " + ", ".join(unexpected))


def run(command, *, env=None, log=None):
    result = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if log:
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
    require(result.returncode == 0, f"Local command failed: {command[0]}. Review its output locally.")
    return result.stdout


def terraform_outputs(executable):
    value = json.loads(run([executable, f"-chdir={ROOT / 'infra/aws'}", "output", "-json"]))
    api = value.get("incident_api_base_url", {}).get("value")
    origin = value.get("manual_web_origin", {}).get("value")
    require(
        isinstance(api, str) and API_URL.fullmatch(api),
        "No real incident_api_base_url yet. Deploy the reviewed API before building a live release.",
    )
    require(origin == ORIGIN, "Terraform hosting origin differs from the reviewed Amplify branch.")
    return api


def live_environment(api, operation, inherited=None):
    require(API_URL.fullmatch(api), "Use the real us-east-1 /dev API output, without a trailing slash.")
    require(re.fullmatch(r"[A-Z0-9-]+", operation), "Use the agreed operation ID.")
    # VITE_* is public browser configuration. Discard inherited public settings,
    # including any accidentally named VITE_* secret, without reading or printing it.
    env = {k: v for k, v in (inherited if inherited is not None else os.environ).items() if not k.startswith("VITE_")}
    env.update(
        VITE_API_MODE="live",
        VITE_API_URL=api,
        VITE_OPERATION_ID=operation,
        VITE_GOOGLE_CLIENT_ID=CLIENT_ID,
        VITE_MAP_STYLE_URL="",
    )
    return env


def build_live(executable, operation, output):
    check_frontend_inputs()
    api = terraform_outputs(executable)
    env = live_environment(api, operation)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    installed_npm = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "nodejs/npm.cmd"
    if not npm and os.name == "nt" and installed_npm.is_file():
        npm = str(installed_npm)
    require(npm, "Install Node/npm before building the web release.")
    output.mkdir(parents=True, exist_ok=False)
    for name, args in (
        ("tests", ["--prefix", "frontend", "test"]),
        ("build", ["--prefix", "frontend", "run", "build"]),
    ):
        run([npm, *args], env=env, log=output / f"{name}.log")
    assets = list((ROOT / "frontend/dist/assets").glob("*.js"))
    require(assets, "No production JavaScript assets generated.")
    code = b"\n".join(path.read_bytes() for path in assets)
    require(
        api.encode() in code and CLIENT_ID.encode() in code,
        "Built assets do not contain the approved public API/client settings.",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        archive = prepare.amplify()
    bundle = output / "arquifusion-web-live.zip"
    shutil.copyfile(archive, bundle)
    shutil.copyfile(archive.parent / "amplify-rewrites.json", output / "amplify-rewrites.json")
    report = {
        "status": "PREPARED_LOCAL_ONLY",
        "origin": ORIGIN,
        "publicSettings": {k: env[k] for k in sorted(prepare.VITE_SETTINGS)},
        "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest().upper(),
        "zip": bundle.name,
        "published": False,
        "cloudWrites": [],
        "publicationPrerequisites": [
            "Alexis confirms the exact Google JavaScript origin",
            "API endpoint opened by an approved Terraform plan",
            "Real operation grants and technician assignment verified",
            "Leonardo authorizes this exact web publication",
        ],
    }
    (output / "release.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(api, operation, *, send=False):
    live_environment(api, operation, {})
    path = f"{api}/operations/{operation}/machines"
    ack = f"{api}/machines/AF-CORS-PROBE/incidents/INC-PROBE/ack"
    cases = [
        ("no_token", "GET", path, {"Origin": ORIGIN}),
        ("invalid_token", "GET", path, {"Origin": ORIGIN, "Authorization": "Bearer invalid-development-probe"}),
        (
            "approved_preflight",
            "OPTIONS",
            ack,
            {
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,if-match",
            },
        ),
        (
            "other_origin_preflight",
            "OPTIONS",
            ack,
            {
                "Origin": "https://unapproved.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,if-match",
            },
        ),
    ]
    if not send:
        return {
            "status": "PREVIEW_ONLY",
            "requests": [{"case": name, "method": method, "url": url} for name, method, url, _ in cases],
            "writes": [],
        }
    opener = build_opener(NoRedirects())
    results = []
    for name, method, url, headers in cases:
        request = Request(url, method=method, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            # Do not read bodies, user data or tokens. Only public CORS/status evidence.
            row = {
                "case": name,
                "status": response.code,
                "allowOrigin": response.headers.get("Access-Control-Allow-Origin"),
                "allowMethods": response.headers.get("Access-Control-Allow-Methods"),
                "allowHeaders": response.headers.get("Access-Control-Allow-Headers"),
                "requestId": response.headers.get("apigw-requestid") or response.headers.get("x-amzn-requestid"),
            }
        results.append(row)
    by_case = {row["case"]: row for row in results}
    require(
        all(by_case[name]["status"] == 401 for name in ("no_token", "invalid_token")),
        "Expected 401 for both unauthenticated probes. Check endpoint openness and route authorization.",
    )
    require(
        by_case["approved_preflight"]["status"] in (200, 204)
        and by_case["approved_preflight"]["allowOrigin"] == ORIGIN,
        "Expected exact-origin preflight.",
    )
    methods = {value.strip().upper() for value in (by_case["approved_preflight"]["allowMethods"] or "").split(",")}
    headers = {value.strip().lower() for value in (by_case["approved_preflight"]["allowHeaders"] or "").split(",")}
    require(
        {"GET", "POST"} <= methods and {"authorization", "content-type", "if-match"} <= headers,
        "Expected the v1.2 methods and headers in preflight.",
    )
    require(by_case["other_origin_preflight"]["allowOrigin"] is None, "An unapproved origin received a CORS grant.")
    return {"status": "READ_ONLY_PROBES_PASSED", "results": results, "writes": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "prepare", "probe"])
    parser.add_argument("--terraform", default=shutil.which("terraform"))
    parser.add_argument("--operation", default="MINA-NORTE")
    parser.add_argument("--output", type=Path, default=ROOT / "build/readiness-20261115/web/live-release")
    parser.add_argument(
        "--send", action="store_true", help="Perform only the GET/OPTIONS probes; no token or POST is accepted"
    )
    args = parser.parse_args()
    try:
        check_frontend_inputs()
        if args.action == "check":
            print(
                json.dumps(
                    {
                        "status": "LOCAL_RECIPE_READY",
                        "origin": ORIGIN,
                        "clientId": CLIENT_ID,
                        "liveZipPrepared": False,
                        "blockers": [
                            "Real API stage output required",
                            "Google origin confirmation required for login",
                            "Operation grants required for the authenticated rehearsal",
                        ],
                    },
                    indent=2,
                )
            )
            return
        require(args.terraform, "Provide --terraform with the installed executable.")
        if args.action == "prepare":
            output = args.output.resolve()
            require(
                output.is_relative_to(ROOT / "build"),
                "Keep local release evidence under the repository build directory.",
            )
            build_live(args.terraform, args.operation, output)
        else:
            print(json.dumps(probe(terraform_outputs(args.terraform), args.operation, send=args.send), indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
