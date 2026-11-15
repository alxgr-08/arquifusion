"""Build the maintenance trial ZIP offline with the project's locked Linux Lambda dependencies."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from infra.aws.health_evaluator.build import add_file  # noqa: E402

BASE = ROOT / "build/incident/incident-tasks-catalog.zip"
OUTPUT = ROOT / "build/maintenance"
SOURCES = {
    "infra/aws/maintenance/maintenance_trial.py": "maintenance_trial.py",
    "services/incident_tasks/preventive.py": "services/incident_tasks/preventive.py",
    "services/offline_detector/handler.py": "services/offline_detector/handler.py",
}
OLD_HANDLERS = {"handler.py", "incident_catalog.py", "incidents.py", "preventive.py"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inputs = dict(SOURCES)
    for path in sorted((ROOT / "shared/af_shared").rglob("*.py")):
        inputs[path.relative_to(ROOT).as_posix()] = path.relative_to(ROOT / "shared").as_posix()
    with tempfile.TemporaryDirectory(dir=OUTPUT) as tmp:
        target = Path(tmp) / "trial.zip"
        manifest = Path(tmp) / "trial.manifest.json"
        with ZipFile(BASE) as base, ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
            for name in sorted(base.namelist()):
                if name not in OLD_HANDLERS and not name.startswith("af_shared/") and not name.endswith("/"):
                    add_file(archive, name, base.read(name))
            for source, entry in sorted(inputs.items()):
                add_file(archive, entry, (ROOT / source).read_bytes().replace(b"\r\n", b"\n"))
        checksum = digest(target)
        manifest.write_text(
            json.dumps(
                {
                    "package": checksum,
                    "dependencies": digest(BASE),
                    "sources": {source: digest(ROOT / source) for source in inputs},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        target.replace(OUTPUT / target.name)
        manifest.replace(OUTPUT / manifest.name)
    print(f"{checksum}  {OUTPUT / 'trial.zip'}")


if __name__ == "__main__":
    build()
