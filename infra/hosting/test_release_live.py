import json

import pytest

from infra.hosting import release_live as release


def test_live_settings_are_public_exact_and_ignore_inherited_vite_secrets():
    api = "https://abc123.execute-api.us-east-1.amazonaws.com/dev"
    env = release.live_environment(
        api, "ENSAYO-9001", {"PATH": "local", "VITE_PRIVATE_KEY": "do-not-copy", "VITE_API_MODE": "mock"}
    )
    assert env["PATH"] == "local"
    assert env["VITE_API_MODE"] == "live"
    assert env["VITE_GOOGLE_CLIENT_ID"] == release.CLIENT_ID
    assert env["VITE_API_URL"] == api
    assert "VITE_PRIVATE_KEY" not in env


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/dev",
        "https://fake.execute-api.us-west-2.amazonaws.com/dev",
        "https://abc123.execute-api.us-east-1.amazonaws.com/dev/",
    ],
)
def test_wrong_or_missing_api_is_rejected(url):
    with pytest.raises(ValueError, match="real us-east-1"):
        release.live_environment(url, "MINA-NORTE", {})


def test_local_dotenv_is_refused_without_reading_it(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / ".env.example").touch()
    release.check_frontend_inputs(tmp_path)
    (frontend / ".env.local").touch()
    with pytest.raises(ValueError, match="clean frontend"):
        release.check_frontend_inputs(tmp_path)


def test_outputs_require_a_real_stage_and_the_existing_hosting(monkeypatch):
    monkeypatch.setattr(release, "run", lambda _: json.dumps({"manual_web_origin": {"value": release.ORIGIN}}))
    with pytest.raises(ValueError, match="No real"):
        release.terraform_outputs("terraform")
    api = "https://abc123.execute-api.us-east-1.amazonaws.com/dev"
    values = {"manual_web_origin": {"value": release.ORIGIN}, "incident_api_base_url": {"value": api}}
    monkeypatch.setattr(release, "run", lambda _: json.dumps(values))
    assert release.terraform_outputs("terraform") == api
    values["manual_web_origin"]["value"] = "https://other.example"
    with pytest.raises(ValueError, match="hosting origin"):
        release.terraform_outputs("terraform")


def test_probe_defaults_to_no_network_and_has_no_write_method(monkeypatch):
    monkeypatch.setattr(release, "build_opener", lambda *_: pytest.fail("Preview must not open a connection"))
    result = release.probe("https://abc123.execute-api.us-east-1.amazonaws.com/dev", "MINA-NORTE")
    assert result["status"] == "PREVIEW_ONLY"
    assert {row["method"] for row in result["requests"]} == {"GET", "OPTIONS"}
    assert result["writes"] == []


def test_no_redirects_are_followed():
    assert release.NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://other.example") is None


def fake_probe(monkeypatch, *, auth_status=401, other_origin=None, allow_headers="authorization,content-type,if-match"):
    requests = []

    class Response:
        def __init__(self, code, headers):
            self.code = code
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, *_):
            pytest.fail("The probe must never read response bodies")

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 15 and request.data is None
            if request.method == "GET":
                return Response(auth_status, {})
            origin = release.ORIGIN if request.headers["Origin"] == release.ORIGIN else other_origin
            return Response(
                204,
                {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET,POST",
                    "Access-Control-Allow-Headers": allow_headers,
                },
            )

    monkeypatch.setattr(release, "build_opener", lambda *_: Opener())
    return requests


def test_sent_probes_use_only_get_options_and_never_read_data(monkeypatch):
    requests = fake_probe(monkeypatch)
    result = release.probe("https://abc123.execute-api.us-east-1.amazonaws.com/dev", "MINA-NORTE", send=True)
    assert result["status"] == "READ_ONLY_PROBES_PASSED"
    assert [request.method for request in requests] == ["GET", "GET", "OPTIONS", "OPTIONS"]
    assert requests[2].full_url.endswith("/machines/AF-CORS-PROBE/incidents/INC-PROBE/ack")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_status": 200}, "Expected 401"),
        ({"other_origin": "*"}, "unapproved origin"),
        ({"allow_headers": "authorization,content-type"}, "v1.2 methods and headers"),
    ],
)
def test_auth_and_cors_mismatches_are_rejected(monkeypatch, overrides, message):
    fake_probe(monkeypatch, **overrides)
    with pytest.raises(ValueError, match=message):
        release.probe("https://abc123.execute-api.us-east-1.amazonaws.com/dev", "MINA-NORTE", send=True)
