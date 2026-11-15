"""Validate Vite settings and package the static frontend for Amplify or Firebase Hosting."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
PUBLIC = Path("frontend/dist")
VITE_SETTINGS = {"VITE_API_MODE", "VITE_API_URL", "VITE_GOOGLE_CLIENT_ID", "VITE_OPERATION_ID", "VITE_MAP_STYLE_URL"}
ASSET = re.compile(r"[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(js|css|woff2?|png|jpe?g|gif|webp|avif|svg|ico|wasm)")


def check_env() -> None:
    if any(name.startswith("VITE_") and name not in VITE_SETTINGS for name in os.environ):
        raise ValueError("Unexpected public VITE_ setting. Review the five inputs in the hosting README.")
    mode = os.getenv("VITE_API_MODE") or "mock"
    if mode not in {"mock", "live"}:
        raise ValueError("VITE_API_MODE must be mock or live.")
    if not re.fullmatch(r"[A-Z0-9-]+", os.getenv("VITE_OPERATION_ID") or "MINA-NORTE"):
        raise ValueError("VITE_OPERATION_ID must match the ArquiFusion operation ID format.")
    if mode == "live":
        client_id = os.getenv("VITE_GOOGLE_CLIENT_ID", "")
        api_url = os.getenv("VITE_API_URL", "")
        if not re.fullmatch(r"\d+-[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com", client_id):
            raise ValueError("Set the registered public Google web client ID for live mode.")
        if not re.fullmatch(r"https://[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com/dev", api_url):
            raise ValueError("Use the approved incident_api_base_url for the dev stage in us-east-1.")
    if style := os.getenv("VITE_MAP_STYLE_URL"):
        url = urlsplit(style)
        if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError("Use a public HTTPS map style URL suitable for distribution to browsers.")
    print(f"Public Vite settings checked for {mode} mode.")


class Entrypoint(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.styles: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "script" and attr.get("type") == "module" and attr.get("src"):
            self.scripts.append(attr["src"])
        if tag == "link" and attr.get("rel") in {"stylesheet", "modulepreload"} and attr.get("href"):
            self.styles.append(attr["href"])


def public_files() -> tuple[Path, list[Path]]:
    dist = HERE.parents[1] / PUBLIC
    assets = dist / "assets"
    index = dist / "index.html"
    # Refuse links or Windows junctions out of the checked-out frontend.
    for path in (dist.parent, dist, assets, index):
        if path.resolve() != path or not path.exists():
            raise ValueError("Build frontend/dist with a regular index.html and assets directory first.")
    if not index.is_file() or not assets.is_dir():
        raise ValueError("Expected a regular Vite index.html and an assets directory.")
    files = [index]
    for path in sorted(assets.iterdir()):
        name = path.name.lower()
        if name.startswith(".") or "credentials" in name or "service-account" in name or not ASSET.fullmatch(path.name):
            continue
        if path.resolve() != path or not path.is_file():
            raise ValueError("A Vite asset resolves outside the build or is not a regular file.")
        files.append(path)
    entry = Entrypoint()
    entry.feed(index.read_text(encoding="utf-8"))
    urls = {"/" + path.relative_to(dist).as_posix() for path in files}
    if not entry.scripts or not set(entry.scripts + entry.styles).issubset(urls):
        raise ValueError("index.html must reference built, local Vite assets.")
    return dist, files


def new_release(prefix: str) -> Path:
    builds = HERE / "build"
    if builds.resolve() != builds:
        raise ValueError("The local hosting build directory must be inside infra/hosting.")
    builds.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=builds))


def stage() -> None:
    dist, files = public_files()
    config = HERE / "firebase.json"
    if json.loads(config.read_text(encoding="utf-8"))["hosting"]["public"] != PUBLIC.as_posix():
        raise ValueError("The Firebase public directory must be frontend/dist.")
    release = new_release("ensayo-")
    (release / PUBLIC / "assets").mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, release / PUBLIC / path.relative_to(dist))
    # Place the configuration after all assets. Each run prepares a fresh Firebase project directory.
    shutil.copyfile(config, release / "firebase.json")
    print(f"{len(files)} static files staged in {release}")


def amplify() -> Path:
    dist, files = public_files()
    release = new_release("amplify-")
    output = release / "arquifusion-web.zip"
    # ZIP_STORED plus fixed metadata makes the archive independent of zlib, mtimes and host OS.
    with ZipFile(output.with_suffix(".partial"), "x") as archive:
        for path in files:
            info = ZipInfo(path.relative_to(dist).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    shutil.copyfile(HERE / "amplify-rewrites.json", release / "amplify-rewrites.json")
    output.with_suffix(".partial").rename(output)
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(f"{len(files)} static files | SHA-256 {digest} | {output}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check-env", "amplify", "stage"])
    try:
        actions = {"check-env": check_env, "amplify": amplify, "stage": stage}
        actions[parser.parse_args().action]()
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
