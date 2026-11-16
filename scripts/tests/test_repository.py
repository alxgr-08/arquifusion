from pathlib import Path

import pytest

from scripts.check_repository import check_files, local_links, public_path


@pytest.mark.parametrize("path", [
    ".env", "frontend/.env.production", "infra/aws/terraform.tfstate.backup", "infra/aws/prod.tfvars",
    "build/plan.json", ".terraform/providers/provider", "private.key", "service-account-prod.json",
    "application_default_credentials.json", "frontend/node_modules/lib/index.js",
])
def test_rejects_private_files_without_reading_them(path):
    assert not public_path(path)
    assert check_files(Path("."), [path]) == [f"{path}: private or generated file must not be versioned"]


@pytest.mark.parametrize("path", [".env.example", "frontend/.env.example", "infra/aws/terraform.tfvars.example"])
def test_public_configuration_examples_are_allowed(path):
    assert public_path(path)


def test_links_are_relative_to_the_document_and_allow_fragments_and_encoded_spaces(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").touch()
    (docs / "schema file.json").write_text("{}")
    (docs / "guide.md").write_text("[Home](../README.md#setup) ![Diagram](schema%20file.json) [Root](/README.md)")
    assert check_files(tmp_path, ["docs/guide.md", "docs/schema file.json"]) == []


def test_missing_links_and_links_outside_the_checkout_fail(tmp_path):
    (tmp_path / "README.md").write_text("[Missing](missing.md) [Outside](../outside.md)")
    assert len(check_files(tmp_path, ["README.md"])) == 2


def test_remote_links_anchors_and_code_samples_do_not_require_local_files():
    text = """[GitHub](https://github.com/example/repo) [Section](#setup)
`[Inline sample](absent.md)`
```md
[Fenced sample](also-absent.md)
```
[Local](README.md)
"""
    assert list(local_links(text)) == ["README.md"]


def test_invalid_json_and_conflicts_are_reported_without_printing_file_contents(tmp_path):
    (tmp_path / "broken.json").write_text('{"test": }')
    (tmp_path / "README.md").write_text("<<<<<<< HEAD\nexample\n=======\nother\n>>>>>>> branch\n")
    assert check_files(tmp_path, ["broken.json", "README.md"]) == [
        "broken.json:1: invalid JSON", "README.md: unresolved merge conflict",
    ]
