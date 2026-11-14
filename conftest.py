"""Test setup shared by all services.

Every services/<name>/ folder is put on sys.path so tests can import a service's own modules
(e.g. `import fleet_sim`). All services have a `handler.py`, so never `import handler` in a test:
use the `load_handler` fixture, which loads it under a unique module name.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
for service_dir in sorted((ROOT / "services").iterdir()):
    if service_dir.is_dir() and str(service_dir) not in sys.path:
        sys.path.append(str(service_dir))


def _load(service: str):
    name = f"{service}_handler"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "services" / service / "handler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_handler():
    return _load
