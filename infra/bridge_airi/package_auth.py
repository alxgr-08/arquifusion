"""Make a deterministic ZIP from the integrity-pinned upstream, JS-only verifier."""

import base64
import hashlib
import io
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "build/bridge-permanent"
URL = "https://registry.npmjs.org/aws-jwt-verify/-/aws-jwt-verify-5.2.1.tgz"
INTEGRITY = "J+buA4M+qvQDk58WXFBLkDodkEX3DDL1ac5XFQPW3opxAsaLXYu5hYnlSHsaBRDBUXBAn695kE/cw/mdyJKwJg=="


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=20) as response:
        source = response.read(2_000_000)
    if base64.b64encode(hashlib.sha512(source).digest()).decode() != INTEGRITY:
        raise ValueError("Unexpected upstream package digest")
    files = {"authorizer.mjs": (HERE / "authorizer.mjs").read_bytes()}
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("package/")
            if member.isfile() and not name.startswith("/") and ".." not in name.split("/"):
                if name.endswith((".js", ".json")) or name == "LICENSE":
                    files["node_modules/aws-jwt-verify/" + name] = archive.extractfile(member).read()
    with zipfile.ZipFile(OUT / "auth.zip", "w") as package:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            package.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


if __name__ == "__main__":
    main()
