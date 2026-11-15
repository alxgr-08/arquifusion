"""Build the project's CPython 3.12 Lambda ZIPs for Linux x86_64."""

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHARED = REPO / "shared" / "af_shared"
INCIDENT_SERVICES = ("incident_tasks", "assign_technician", "notify", "ai_diagnosis")


def add_file(bundle: ZipFile, name: str, data: bytes) -> None:
    if name in bundle.NameToInfo:
        raise ValueError(f"Duplicate Lambda package entry: {name}")
    entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    entry.compress_type = bundle.compression
    bundle.writestr(entry, data)


def build(service_name: str) -> None:
    service = REPO / "services" / service_name
    health = service_name == "health_evaluator"
    folder = {"api": "api", "health_evaluator": "health-evaluator"}.get(service_name, "incident")
    package_name = "incident-tasks-catalog" if service_name == "incident_tasks" else service_name.replace("_", "-")
    if service_name == "api":
        package_name = "api-ack"
    output = REPO / "build" / folder / (package_name + ".zip")
    requirements = [HERE / "requirements.lock"]
    if service_name == "ai_diagnosis":
        requirements.append(HERE.parent / "incident" / "ai-requirements.lock")
    requirements.append(service / "requirements.txt")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        staging = Path(tmp)
        command = [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "download",
            "--no-input",
            "--index-url",
            "https://pypi.org/simple",
            "--only-binary=:all:",
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--abi",
            "cp312",
            "--require-hashes",
            "--dest",
            str(staging),
        ]
        for requirement in requirements:
            command.extend(["-r", str(requirement)])
        subprocess.run(command, check=True)
        archive = staging / output.name
        # Keep the original stored ZIP format for the already-deployed evaluator.
        with ZipFile(archive, "w", compression=ZIP_STORED if health else ZIP_DEFLATED) as bundle:
            for wheel in sorted(staging.glob("*.whl")):
                with ZipFile(wheel) as package:
                    for name in sorted(package.namelist()):
                        if name.endswith("/") or ".data/scripts/" in name:
                            continue
                        if ".data/" in name:
                            raise ValueError(f"Review wheel installation layout: {name}")
                        add_file(bundle, name, package.read(name))
            sources = sorted(service.glob("*.py"))
            sources.extend(sorted(SHARED.rglob("*.py")))
            sources.append(SHARED / "py.typed")
            for source in sources:
                name = source.name if source.parent == service else source.relative_to(SHARED.parent).as_posix()
                add_file(bundle, name, source.read_bytes().replace(b"\r\n", b"\n"))
            if service_name == "incident_tasks":
                adapter = HERE.parent / "incident" / "incident_catalog.py"
                add_file(bundle, adapter.name, adapter.read_bytes().replace(b"\r\n", b"\n"))

        if archive.stat().st_size >= 50 * 1024 * 1024:
            raise ValueError(f"{output.name} exceeds the Lambda direct upload limit")
        archive.replace(output)
    with output.open("rb") as built:
        print(f"{hashlib.file_digest(built, 'sha256').hexdigest()}  {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--incident", action="store_true", help="Build the four incident tasks")
    group.add_argument("--api", action="store_true", help="Build the services/api handler with af_shared")
    args = parser.parse_args()
    services = ("api",) if args.api else ("health_evaluator",)
    for service_name in INCIDENT_SERVICES if args.incident else services:
        build(service_name)
