"""Run pytest for shared/, analytics/, statemachines/ and each service in its own process (each service has its own
`handler`)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    targets = [ROOT / "shared" / "tests", ROOT / "analytics" / "tests", ROOT / "statemachines" / "tests"]
    targets += sorted(p for p in (ROOT / "services").glob("*/tests") if any(p.glob("test_*.py")))
    failed = []
    for target in targets:
        print(f"\n=== {target.relative_to(ROOT)} ===", flush=True)
        if subprocess.run([sys.executable, "-m", "pytest", "-q", str(target)], cwd=ROOT).returncode != 0:
            failed.append(target.relative_to(ROOT))
    if failed:
        print("\nFAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
