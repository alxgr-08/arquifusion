"""Build locked Linux CPython 3.12 bridge ZIPs. Run as python -m services.lake_to_bigquery.build."""

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from infra.aws.health_evaluator.build import add_file

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "build" / "bridge"
SERVICES = {
    "stream_exporter": (
        "services/stream_exporter/handler.py",
        "services/stream_exporter/projection.py",
        "services/stream_exporter/lake_io.py",
    ),
    "lake_to_bigquery": (
        "contracts/telemetry.schema.json",
        "services/lake_to_bigquery/handler.py",
        "services/lake_to_bigquery/loader.py",
        "services/lake_to_bigquery/load_jobs.py",
        "services/lake_to_bigquery/parquet_input.py",
        "services/lake_to_bigquery/telemetry.py",
        "services/stream_exporter/lake_io.py",
    ),
    "bigquery_sync_back": ("services/bigquery_sync_back/handler.py",),
}
SHARED = ("__init__.py", "contracts.py", "ddb.py", "keys.py")


def build(service, output, wheelhouse):
    requirements = ROOT / "services" / service
    with tempfile.TemporaryDirectory(dir=OUTPUT) as temp:
        work = Path(temp)
        index = (
            ["--no-index", "--find-links", str(wheelhouse)]
            if wheelhouse
            else ["--index-url", "https://pypi.org/simple"]
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "download",
                "--quiet",
                "--no-input",
                "--keyring-provider",
                "disabled",
                "--only-binary=:all:",
                "--require-hashes",
                "--platform",
                "manylinux2014_x86_64",
                "--platform",
                "manylinux_2_28_x86_64",
                "--implementation",
                "cp",
                "--python-version",
                "3.12",
                "--abi",
                "cp312",
                *index,
                "--dest",
                str(work),
                "-r",
                str(requirements / "requirements-linux.lock"),
                "-r",
                str(requirements / "requirements.txt"),
            ],
            check=True,
            env={**os.environ, "PIP_CONFIG_FILE": os.devnull, "NETRC": os.devnull},
        )
        artifact = work / output.name
        with ZipFile(artifact, "w", compression=ZIP_DEFLATED) as archive:
            for wheel in sorted(work.glob("*.whl")):
                with ZipFile(wheel) as dependency:
                    for name in sorted(dependency.namelist()):
                        if name.endswith("/") or ".data/scripts/" in name:
                            continue
                        if ".data/" in name:
                            raise ValueError(f"review wheel installation layout for {name}")
                        add_file(archive, name, dependency.read(name))
            for name in SERVICES[service]:
                add_file(archive, name, (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
            for name in SHARED:
                source = ROOT / "shared" / "af_shared" / name
                add_file(archive, f"af_shared/{name}", source.read_bytes().replace(b"\r\n", b"\n"))
            size = sum(entry.file_size for entry in archive.infolist())
        if artifact.stat().st_size > 50 * 1024**2 or size > 250 * 1024**2:
            raise ValueError("Lambda ZIP exceeds the compressed or uncompressed package limit")
        artifact.replace(output)
    with output.open("rb") as file:
        signature = hashlib.file_digest(file, "sha256").hexdigest()
    print(f"{output.name}  sha256={signature}  zip={output.stat().st_size}  unpacked={size}")


def smoke(service, artifact, *, sources_only=False):
    linux_target = sys.platform == "linux" and platform.machine() == "x86_64" and sys.version_info[:2] == (3, 12)
    if not sources_only and not linux_target:
        raise SystemExit("The ZIP import smoke requires Linux x86_64 and CPython 3.12.")
    with tempfile.TemporaryDirectory() as folder:
        with ZipFile(artifact) as archive:
            members = None
            if sources_only:
                members = [
                    name for name in archive.namelist() if name.startswith(("services/", "af_shared/", "contracts/"))
                ]
            archive.extractall(folder, members=members)
        source = """
import importlib, importlib.util, pathlib, sys
folder, service, mode = sys.argv[1:]
sys.path.insert(0, folder)
if mode == "sources":
    sys.modules.update({"pyarrow": None, "pandas": None})
handler = importlib.import_module(f"services.{service}.handler")
assert callable(handler.handler)
if service == "bigquery_sync_back":
    assert callable(handler.selected_handler)
assert pathlib.Path(handler.__file__).resolve().is_relative_to(folder)
assert importlib.util.find_spec("pandas") is None and importlib.util.find_spec("pyarrow") is None
if service == "lake_to_bigquery":
    from services.lake_to_bigquery.telemetry import validator
    assert "messageId" in validator().schema["required"]
"""
        flags = ["-I", "-B"] if sources_only else ["-I", "-S", "-B"]
        mode = "sources" if sources_only else "linux"
        subprocess.run([sys.executable, *flags, "-c", source, folder, service, mode], check=True)
        print(f"{artifact.name} import OK ({mode}, {sys.platform} {platform.machine()})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=SERVICES, help="Limit the build or smoke to one Lambda.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--wheelhouse", type=Path, help="Build offline from an existing directory of locked wheels.")
    group.add_argument(
        "--smoke", action="store_true", help="Import the existing ZIPs with only the stdlib on sys.path."
    )
    group.add_argument(
        "--smoke-sources", action="store_true", help="Import packaged source using the host's dependencies."
    )
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True, parents=True)
    for service in [args.service] if args.service else SERVICES:
        output = OUTPUT / (service.replace("_", "-") + ".zip")
        if args.smoke or args.smoke_sources:
            smoke(service, output, sources_only=args.smoke_sources)
        else:
            build(service, output, args.wheelhouse)


if __name__ == "__main__":
    main()
