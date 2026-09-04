"""File-only gallery inventory and checks freshness."""

import importlib.util
import gzip
import json
import os
import sys
import struct
from pathlib import Path

import pytest

from test_promote_scene import fake_sog

TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("gallery_status", TOOLS / "gallery_status.py")
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


@pytest.fixture
def gallery(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "provenance").mkdir()
    fake_sog(tmp_path / "scenes/one.sog", 73)
    (tmp_path / "scenes/one.spz").write_bytes(b"companion")
    (tmp_path / "scenes/two.glb").write_bytes(b"mesh")
    (tmp_path / "provenance/one.html").write_text("<li>Generated: 2026-09-04T19:08:07+00:00.</li>")
    (tmp_path / "scenes.json").write_text(json.dumps([
        {"stem": "one", "title": "One", "splats": 99, "captured": "2026-08-30",
         "provenance": "provenance/one.html"},
        {"stem": "two", "title": "Two", "mesh": "scenes/two.glb", "splats": 0},
    ]))
    (tmp_path / "checks.json").write_text(json.dumps({"results": [
        {"scene": "one", "platform": "web-mobile", "passed": True, "file": "scenes/one.sog",
         "count": 73, "size_bytes": (tmp_path / "scenes/one.sog").stat().st_size},
        {"scene": "two", "platform": "web-mobile", "passed": False, "file": "scenes/two.glb",
         "count": 0, "size_bytes": 4},
        {"scene": "one", "platform": "fab", "passed": False},
    ]}))
    for file in (tmp_path / "scenes").iterdir():
        os.utime(file, (1000, 1000))
    os.utime(tmp_path / "checks.json", (2000, 2000))
    return tmp_path


def test_inventory_json_and_table(gallery, capsys):
    archive = gallery / "archive/replaced/one-2026-09-04"
    archive.mkdir(parents=True)
    fake_sog(archive / "one.sog")
    other = gallery / "archive/replaced/one-extra-2026-09-04"
    other.mkdir()
    fake_sog(other / "one-extra.sog")
    assert status.main(["--json"], root=gallery) == 0
    rows = json.loads(capsys.readouterr().out)
    one, two = rows
    assert one["splats"] == 73
    assert one["size_bytes"] == sum(p.stat().st_size for p in (gallery / "scenes").glob("one.*"))
    assert one["web_mobile"] == "PASS"
    assert one["provenance_present"]
    assert one["provenance_generated"] == "2026-09-04T19:08:07+00:00"
    assert one["captured"] == "2026-08-30"
    assert one["archive_count"] == 1
    assert two["kind"] == "mesh" and two["splats"] == 0 and two["size_bytes"] == 4
    assert two["web_mobile"] == "FAIL" and not two["provenance_present"]
    assert status.main([], root=gallery) == 0
    assert "| one | One | splat | 73 |" in capsys.readouterr().out


@pytest.mark.parametrize("filename", ["one.sog", "one.spz", "two.glb"])
def test_matching_content_ignores_mtime(gallery, filename, capsys):
    os.utime(gallery / "scenes" / filename, (2001, 2001))
    assert status.main(["--json"], root=gallery) == 0
    rows = json.loads(capsys.readouterr().out)
    assert all(not r["stale"] for r in rows)
    assert status.main([], root=gallery) == 0
    assert "CURRENT" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["count", "size"])
def test_content_change_with_equal_mtimes(gallery, change, capsys):
    path = gallery / "scenes/one.sog"
    if change == "count":
        original_size = path.stat().st_size
        fake_sog(path, 74)
        assert path.stat().st_size == original_size
    else:
        path.write_bytes(path.read_bytes() + b"extra")
    os.utime(path, (2000, 2000))
    assert status.main(["--json"], root=gallery) == 1
    assert json.loads(capsys.readouterr().out)[0]["stale"]


@pytest.mark.parametrize("metric", ["both", "count", "size"])
def test_legacy_evidence(gallery, metric):
    snapshot = gallery / "checks.json"
    checks = json.loads(snapshot.read_text())
    result = checks["results"][0]
    size = result.pop("size_bytes")
    result.pop("count")
    result["checks"] = []
    if metric in {"both", "count"}:
        result["checks"].append({"name": "count", "detail": "73 splats; budget 500000"})
    if metric in {"both", "size"}:
        result["checks"].append({"name": "size", "detail": f"{size} bytes; budget 20000000"})
    snapshot.write_text(json.dumps(checks))
    os.utime(snapshot, (500, 500))
    assert not status.gallery_status(gallery)[0]["stale"]
    fake_sog(gallery / "scenes/one.sog", 100)
    assert status.gallery_status(gallery)[0]["stale"]


def test_mtime_fallback_is_labelled_and_does_not_fail(gallery, capsys):
    snapshot = gallery / "checks.json"
    checks = json.loads(snapshot.read_text())
    result = checks["results"][0]
    result.pop("count")
    result.pop("size_bytes")
    snapshot.write_text(json.dumps(checks))
    os.utime(snapshot, (500, 500))
    assert status.main([], root=gallery) == 0
    assert "STALE; mtime" in capsys.readouterr().out
    os.utime(snapshot, (2000, 2000))
    assert status.main([], root=gallery) == 0
    assert "CURRENT; mtime" in capsys.readouterr().out


def test_missing_entry(gallery):
    snapshot = gallery / "checks.json"
    checks = json.loads(snapshot.read_text())
    checks["results"] = checks["results"][1:]
    snapshot.write_text(json.dumps(checks))
    assert status.main([], root=gallery) == 1
    assert status.gallery_status(gallery)[0]["checks_rule"] == "missing"


def test_companion_bytes_are_not_compared_to_primary_snapshot(gallery):
    (gallery / "scenes/one.spz").write_bytes(b"changed unchecked companion")
    assert not status.gallery_status(gallery)[0]["stale"]


@pytest.mark.parametrize("suffix", [".ply", ".spz", ".compressed.spz"])
def test_checked_file_counts(gallery, suffix):
    path = gallery / "scenes" / ("one" + suffix)

    def write_count(count):
        if suffix == ".ply":
            path.write_bytes(f"ply\nformat ascii 1.0\nelement vertex {count}\nend_header\n".encode())
        else:
            data = struct.pack("<IIIBBBB", 0x5053474E, 2, count, 0, 0, 0, 0)
            path.write_bytes(gzip.compress(data) if suffix == ".compressed.spz" else data)
        os.utime(path, (2000, 2000))

    write_count(73)
    snapshot = gallery / "checks.json"
    checks = json.loads(snapshot.read_text())
    result = checks["results"][0]
    result["file"] = str(path.relative_to(gallery))
    result.pop("size_bytes")
    snapshot.write_text(json.dumps(checks))
    os.utime(snapshot, (2000, 2000))
    assert not status.gallery_status(gallery)[0]["stale"]
    write_count(74)
    assert status.gallery_status(gallery)[0]["stale"]
    path.write_bytes(b"invalid")
    assert status.gallery_status(gallery)[0]["stale"]


def test_age_threshold_missing_files_and_checks(gallery, capsys):
    rows = status.gallery_status(gallery, stale_days=1, now=2000 + 86401)
    assert all(r["checks_aged"] and not r["stale"] for r in rows)
    assert not status.gallery_status(gallery, stale_days=1, now=2000 + 86400)[0]["checks_aged"]
    assert status.main(["--stale-days", "0"], root=gallery) == 0
    (gallery / "scenes/two.glb").unlink()
    assert not status.gallery_status(gallery)[1]["file_present"]
    assert status.main([], root=gallery) == 1
    (gallery / "checks.json").unlink()
    assert status.main(["--json"], root=gallery) == 1
    assert status.gallery_status(gallery)[0]["web_mobile"] == "UNKNOWN"
    with pytest.raises(SystemExit, match="2"):
        status.main(["--stale-days", "-1"], root=gallery)


@pytest.mark.parametrize("text,expected", [
    ("<li>Sheet generated 2026-09-04 20:32 UTC from files.</li>", "2026-09-04 20:32 UTC"),
    ("<p>No generation date</p>", None),
])
def test_generation_date(tmp_path, text, expected):
    page = tmp_path / "page.html"
    page.write_text(text)
    assert status.generation_date(page) == expected
