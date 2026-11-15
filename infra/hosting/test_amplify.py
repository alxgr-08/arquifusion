import json
import re
from pathlib import Path
from zipfile import ZipFile

from infra.hosting import prepare


def test_reproducible_zip_and_static_rewrites(tmp_path, monkeypatch):
    hosting = tmp_path / "infra/hosting"
    hosting.mkdir(parents=True)
    rule_file = "amplify-rewrites.json"
    (hosting / rule_file).write_bytes((Path(__file__).parent / rule_file).read_bytes())
    assets = tmp_path / "frontend/dist/assets"
    assets.mkdir(parents=True)
    name = "app-12345678.js"
    (assets / name).write_text("export {}", encoding="utf-8")
    for excluded in (name + ".map", "service-account-12345678.js", "credentials.json", ".env", "server.key"):
        (assets / excluded).touch()
    (assets.parent / "index.html").write_text(f'<script type="module" src="/assets/{name}"></script>', encoding="utf-8")
    monkeypatch.setattr(prepare, "HERE", hosting)
    first = prepare.amplify()
    (assets / name).touch()
    second = prepare.amplify()
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as bundle:
        assert bundle.testzip() is None
        assert bundle.namelist() == ["index.html", "assets/" + name]
    [rule] = json.loads((first.parent / rule_file).read_bytes())
    source = re.compile(rule["source"].removeprefix("</").removesuffix("/>"))
    assert rule["status"] == "200" and rule["target"] == "/index.html"
    assert all(source.fullmatch(route) for route in ["/", "/kpis", "/incidents", "/machines/TST-0001"])
    assert all(
        not source.fullmatch(route) for route in ["/assets/foo", "/assets/app.js", "/missing.png", "/index.html"]
    )
