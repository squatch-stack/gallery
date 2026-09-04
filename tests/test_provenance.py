"""Provenance evidence, GLB topology, and offline export regressions."""

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("provenance", Path(__file__).parents[1] / "tools/provenance.py")
provenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provenance)


def glb(tmp_path, mode=4, indexed=True, count=6):
    primitive = {"mode": mode, "attributes": {"POSITION": 0}}
    if indexed:
        primitive["indices"] = 1
    data = {
        "asset": {"version": "2.0", "generator": "Fixture"},
        "meshes": [{"primitives": [primitive]}],
        "accessors": [{"count": count}, {"count": count}],
    }
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    path = tmp_path / "model.glb"
    path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 20 + len(payload), len(payload), 0x4E4F534A) + payload)
    return path


@pytest.mark.parametrize(("mode", "count", "expected"), [(4, 6, 2), (5, 7, 5), (6, 7, 5), (1, 6, 0)])
@pytest.mark.parametrize("indexed", [True, False])
def test_triangle_modes(tmp_path, mode, count, expected, indexed):
    assert provenance.glb_summary(glb(tmp_path, mode, indexed, count)) == (expected, "Fixture")


def test_glb_rejects_corruption(tmp_path):
    path = glb(tmp_path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="length"):
        provenance.glb_summary(path)
    with pytest.raises(ValueError, match="count"):
        provenance.glb_summary(glb(tmp_path, count=5))


def test_app_export_omits_unsourced_statistics(tmp_path, monkeypatch):
    (tmp_path / "scenes").mkdir()
    delivered = tmp_path / "scenes/test.sog"
    delivered.write_bytes(b"a delivered file")
    (tmp_path / "scenes.json").write_text(
        json.dumps([{"stem": "test", "title": "Test <scan>", "splats": 12, "place": "", "captured": ""}])
    )
    message = "A scan <script>alert(1)</script> with **literal** markup.\n\nCrop --alpha-min 0.04."
    monkeypatch.setattr(provenance, "git_output", lambda _repo, *args: message if args[0] == "show" else "a" * 40)
    args = argparse.Namespace(
        subject=Path("test"),
        source="App, PLY export",
        source_commit=["abc"],
        cleaning=["Crop --alpha-min 0.04."],
        note=[],
        export=[],
        out=tmp_path / "sheet.html",
    )
    provenance.app_export(args, tmp_path)
    sheet = args.out.read_text()
    assert hashlib.sha256(delivered.read_bytes()).hexdigest() in sheet
    assert "16 bytes" in sheet
    assert "--alpha-min 0.04" in sheet
    assert "**literal**" in sheet
    assert "&lt;script&gt;" in sheet
    for absent in ("<script>", "Camera solve", "<h2>Training", "reprojection", "<b>Captured:", "<b>Place:"):
        assert absent not in sheet
    sidecar = json.loads(args.out.with_suffix(".json").read_text())
    assert sidecar["schema_version"] == 1
    assert sidecar["mode"] == "app-export"
    assert sidecar["inputs"]["source"] == "App, PLY export"
    assert sidecar["inputs"]["cleaning"] == ["Crop --alpha-min 0.04."]
    assert sidecar["date"] in sheet

    args.cleaning = ["clean_export.py --alpha-min 0.05"]
    with pytest.raises(ValueError, match="must quote"):
        provenance.app_export(args, tmp_path)
    args.cleaning_source = "candidate"
    provenance.app_export(args, tmp_path)
    assert "--alpha-min 0.05" in args.out.read_text()
    assert "Candidate cleaning command/flags recorded at promotion" in args.out.read_text()


def test_mesh_uses_catalog_delivery_only(tmp_path, monkeypatch):
    mesh = glb(tmp_path)
    (tmp_path / "scenes.json").write_text(
        json.dumps([{"stem": "test", "title": "Mesh", "splats": 0, "mesh": mesh.name}])
    )
    monkeypatch.setattr(provenance, "git_output", lambda *_args: "mesh export")
    args = argparse.Namespace(
        subject=Path("test"),
        source="Mesh app",
        source_commit=["abc"],
        cleaning=[],
        note=[],
        export=[],
        out=tmp_path / "sheet.html",
    )
    provenance.app_export(args, tmp_path)
    sheet = args.out.read_text()
    assert "2 stored triangles" in sheet
    assert "GLB asset.generator: Fixture" in sheet
    assert "Catalog splats" not in sheet
    assert "Recorded cleaning" not in sheet


@pytest.mark.parametrize("mode", ["passes", "fails", "crashes", "invalid"])
def test_refresh_preserves_snapshot_on_errors(tmp_path, mode):
    import os
    import subprocess
    import sys

    root = tmp_path
    (root / "tools").mkdir()
    script = root / "tools/refresh_checks.sh"
    script.write_text((Path(__file__).parents[1] / "tools/refresh_checks.sh").read_text())
    (root / "scenes.json").write_text('[{"stem": "test"}]')
    snapshot = root / "checks.json"
    snapshot.write_text("previous snapshot")
    data = {
        "schema_version": 1,
        "results": [
            {
                "scene": "test",
                "platform": "web-mobile",
                "passed": mode == "passes",
                "checks": [{"name": "count", "status": "pass" if mode == "passes" else "fail"}],
            }
        ],
    }
    checker = root / "tools/check_deliverable.py"
    if mode == "crashes":
        checker.write_text('raise RuntimeError("test crash")')
    elif mode == "invalid":
        checker.write_text('print("{}")')
    else:
        checker.write_text(f"print({json.dumps(data)!r})\nraise SystemExit({int(mode == 'fails')})")
    result = subprocess.run(
        ["sh", str(script)],
        check=False,
        cwd=root / "tools",
        env={**os.environ, "PYTHON": sys.executable},
        capture_output=True,
        text=True,
    )
    if mode in ("passes", "fails"):
        assert result.returncode == 0, result.stderr
        assert json.loads(snapshot.read_text()) == data
    else:
        assert result.returncode != 0
        assert snapshot.read_text() == "previous snapshot"
    assert not list(root.glob(".checks.*"))


def test_sidecar_paths_are_portable(tmp_path):
    # A sidecar is tracked and published: repo paths are repo-relative and any
    # other home path starts with ~, so no machine's home directory travels.
    import pathlib

    import provenance as prov

    repo = tmp_path / "repo"
    (repo / "scenes").mkdir(parents=True)
    inside = str(repo / "scenes" / "x.sog")
    home = str(pathlib.Path.home()) + "/Documents/captures/x.ply"
    out = prov.portable({"argv": [inside, home, "--flag", "text"], "n": 3}, repo)
    assert out["argv"][0] == "scenes/x.sog"
    assert out["argv"][1].startswith("~/")
    assert out["argv"][2:] == ["--flag", "text"] and out["n"] == 3
    # A path outside both the repo and the home directory keeps only its name.
    assert prov.portable("/private/tmp/some-scratch-dir/job/metrics.json", repo) == "<external>/metrics.json"
