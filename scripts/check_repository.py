"""Check public repository hygiene and local documentation links without cloud access."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.tfstate", "*.tfstate.*",
    "*.tfvars", "*.tfvars.json", "*.tfplan", "*.tfplan.*", "tfplan", "tfplan.*", "backend.hcl",
    "*-credentials.json", "gcp-sa*.json", "service-account*.json", "arquifusion-????????????.json",
    "grants-participantes*.json", "credentials", "application_default_credentials.json",
)
PRIVATE_DIRS = {".terraform", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".md", ".py", ".ts", ".tsx", ".mjs", ".json", ".yml", ".yaml", ".tf", ".hcl", ".toml", ".sh"}
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
CONFLICT = re.compile(r"(?m)^(?:<{7} |={7}$|>{7} )")


def public_path(path: str) -> bool:
    parts = Path(path).parts
    if set(parts) & PRIVATE_DIRS or "build" in parts or "dist" in parts:
        return False
    name = parts[-1].lower()
    if name == ".env.example" or name.endswith(".tfvars.example"):
        return True
    return not any(fnmatch.fnmatchcase(name, pattern) for pattern in PRIVATE_NAMES)


def local_links(text: str):
    """Ignore code samples; validate inline Markdown links and image sources."""
    fence = None
    lines = []
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    prose = re.sub(r"`+[^`\n]*`+", "", "\n".join(lines))
    for match in LINK.finditer(prose):
        target = match[1].strip()
        if not target:
            continue
        target = target[1:target.index(">")] if target.startswith("<") else target.split()[0]
        url = urlsplit(target)
        if not url.scheme and not url.netloc and url.path:
            yield unquote(url.path)


def check_files(root: Path, paths: list[str]) -> list[str]:
    root = root.resolve()
    errors = []
    for relative in paths:
        if not public_path(relative):
            errors.append(f"{relative}: private or generated file must not be versioned")
            continue
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            errors.append(f"{relative}: repository files must stay within the checkout")
            continue
        if not path.is_file():  # Locally deleted files will disappear in the next commit.
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if CONFLICT.search(text):
            errors.append(f"{relative}: unresolved merge conflict")
        if path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{relative}:{exc.lineno}: invalid JSON")
        if path.suffix == ".md":
            for target in local_links(text):
                destination = (root / target.lstrip("/")) if target.startswith("/") else path.parent / target
                if not destination.resolve().is_relative_to(root) or not destination.exists():
                    errors.append(f"{relative}: broken local link: {target}")
    return errors


def main() -> int:
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8").rstrip("\0").split("\0")
    errors = check_files(ROOT, sorted(set(filter(None, paths))))
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Repository hygiene, JSON and local Markdown links checked ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
