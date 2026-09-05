"""Offline registry and CLI checks; all records live in temporary repositories."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("vet_download", Path(__file__).parents[1] / "tools/vet_download.py")
vet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vet)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("unexpected network request")
    monkeypatch.setattr(vet.urllib.request.OpenerDirector, "open", refuse)


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "addon.zip"
    path.write_bytes(b"local artefact fixture")
    return path


def record_file(root, artifact, *extra):
    return vet.main(["record", "--file", str(artifact), "--name", "addon", "--version", "1",
                     "--licence", "MIT", *extra], root=root)


def test_record_and_verify(tmp_path, artifact, capsys):
    assert record_file(tmp_path, artifact) == 0
    entry, = vet.load_entries(tmp_path)
    assert entry["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert entry["size_bytes"] == artifact.stat().st_size
    assert entry["date_checked"] == vet.date.today().isoformat()
    assert entry["file"] == "addon.zip"
    assert entry["url"] is None
    assert entry["verification"] == "hashed"
    assert vet.main(["verify"], root=tmp_path) == 0
    assert vet.main(["verify", "--offline"], root=tmp_path) == 0
    assert "PASS addon 1" in capsys.readouterr().out


def test_tampered_and_missing_file(tmp_path, artifact, capsys):
    record_file(tmp_path, artifact)
    artifact.write_bytes(b"tampered")
    assert vet.main(["verify"], root=tmp_path) == 1
    assert "FAIL addon 1: sha256 mismatch" in capsys.readouterr().out
    artifact.unlink()
    assert vet.main(["verify", "--offline"], root=tmp_path) == 1


@pytest.mark.parametrize("url", ["http://example.org/a.zip", "file:///tmp/a.zip", "ftp://example.org/a", "https:///a"])
def test_https_refusal(tmp_path, url):
    assert vet.main(["record", url, "--name", "bad", "--version", "1", "--licence", "MIT"], root=tmp_path) == 1
    assert not (tmp_path / "third-party.json").exists()


def test_redirect_refusal():
    request = vet.urllib.request.Request("https://example.org/a")
    with pytest.raises(ValueError, match="https"):
        vet.HTTPSRedirectHandler().redirect_request(request, None, 302, "Found", {}, "http://example.org/b")


@pytest.mark.parametrize("extra", [[], ["--licence", ""], ["--licence", "UNKNOWN"],
                                   ["--licence", "UNKNOWN", "--note", "  "]])
def test_licence_refusal(tmp_path, artifact, extra):
    assert vet.main(["record", "--file", str(artifact), "--name", "addon", "--version", "1", *extra],
                    root=tmp_path) == 1
    assert not (tmp_path / "third-party.json").exists()


def test_unknown_with_reason(tmp_path, artifact):
    assert record_file(tmp_path, artifact, "--licence", "UNKNOWN", "--note", "License not yet read") == 0


def test_supersede(tmp_path, artifact):
    for version in ("1", "2", "3"):
        assert record_file(tmp_path, artifact, "--version", version) == 0
    first, second, third = vet.load_entries(tmp_path)
    assert first["superseded_by"] == "2"
    assert second["superseded_by"] == "3"
    assert "superseded_by" not in third
    assert [entry["version"] for entry in (first, second, third)] == ["1", "2", "3"]


def test_list_json_and_presence(tmp_path, artifact, capsys):
    record_file(tmp_path, artifact)
    capsys.readouterr()
    assert vet.main(["list", "--json"], root=tmp_path) == 0
    entries = json.loads(capsys.readouterr().out)
    assert isinstance(entries, list)
    assert entries == [{**vet.load_entries(tmp_path)[0], "local_present": True}]
    artifact.unlink()
    vet.main(["list"], root=tmp_path)
    assert "| addon | 1 | MIT |" in capsys.readouterr().out
    vet.main(["list", "--json"], root=tmp_path)
    assert json.loads(capsys.readouterr().out)[0]["local_present"] is False


def seed_args():
    return ["record", "https://example.org/addon", "--name", "addon", "--version", "unpinned",
            "--licence", "MIT", "--no-fetch", "--sha256", "UNKNOWN", "--size", "UNKNOWN",
            "--date-checked", "2026-09-04"]


def test_seed_honesty_and_offline(tmp_path, capsys):
    assert vet.main(seed_args(), root=tmp_path) == 0
    entry, = vet.load_entries(tmp_path)
    assert entry["sha256"] is None and entry["size_bytes"] is None
    assert entry["verified_at"] is None and entry["verification"] == "unverified"
    assert vet.main(["verify"], root=tmp_path) == 1
    assert "no recorded sha256" in capsys.readouterr().out
    assert vet.main(["verify", "--offline"], root=tmp_path) == 0
    assert "SKIP addon unpinned" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--sha256", "--size", "--date-checked"])
def test_seed_requires_fields(tmp_path, flag):
    args = seed_args()
    index = args.index(flag)
    del args[index:index + 2]
    assert vet.main(args, root=tmp_path) == 1
    assert not (tmp_path / "third-party.json").exists()


@pytest.mark.parametrize(("flag", "value"), [("--sha256", "abc"), ("--size", "-1"),
                                            ("--date-checked", "yesterday")])
def test_invalid_seed(tmp_path, flag, value):
    args = seed_args()
    args[args.index(flag) + 1] = value
    assert vet.main(args, root=tmp_path) == 1


def test_verify_override_and_unknown_name(tmp_path, artifact):
    record_file(tmp_path, artifact)
    copy = tmp_path / "copy.zip"
    copy.write_bytes(artifact.read_bytes())
    artifact.unlink()
    assert vet.main(["verify", "--name", "addon", "--file", str(copy)], root=tmp_path) == 0
    assert vet.main(["verify", "--name", "missing"], root=tmp_path) == 1


def test_url_download_with_fake_response(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.org/addon.zip"

    class Opener:
        def open(self, url, timeout):
            assert url == "https://example.org/addon.zip"
            assert timeout > 0
            return Response(b"downloaded bytes")

    monkeypatch.setattr(vet.urllib.request, "build_opener", lambda *args: Opener())
    assert vet.main(["record", "https://example.org/addon.zip", "--name", "addon", "--version", "1",
                     "--licence", "MIT"], root=tmp_path) == 0
    entry, = vet.load_entries(tmp_path)
    assert entry["size_bytes"] == len(b"downloaded bytes")
    assert entry["file"] is None
    assert vet.main(["verify"], root=tmp_path) == 0


def test_registry_symlink_refused(tmp_path, artifact):
    target = tmp_path / "untouched.json"
    target.write_text("[]\n")
    (tmp_path / "third-party.json").symlink_to(target)
    assert record_file(tmp_path, artifact) == 1
    assert target.read_text() == "[]\n"
