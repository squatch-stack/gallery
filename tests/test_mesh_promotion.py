"""Mesh promotion uses real provenance and checks in an isolated fixture gallery."""

import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from test_promote_scene import promote_scene

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_deliverable as checker
import gallery_status as status


def textured_quad(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))
    positions = struct.pack("<12f", 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0)
    uvs = struct.pack("<8f", 0, 0, 1, 0, 1, 1, 0, 1)
    indices = struct.pack("<6H", 0, 1, 2, 0, 2, 3)
    binary = positions + uvs + indices + png
    data = {
        "asset": {"version": "2.0", "generator": "stdlib fixture"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": offset, "byteLength": size}
                        for offset, size in [(0, 48), (48, 32), (80, 12), (92, len(png))]],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0}]}],
        "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "textures": [{"source": 0}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
    }
    encoded = json.dumps(data).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
                     + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                     + struct.pack("<II", len(binary), 0x004E4942) + binary)
    return len(png)


@pytest.fixture
def mesh_gallery(tmp_path):
    for directory in ("scenes", "tools", "provenance", "candidate"):
        (tmp_path / directory).mkdir()
    repo = Path(__file__).parents[1]
    for name in ("promote_scene.py", "check_deliverable.py", "provenance.py"):
        shutil.copy2(repo / "tools" / name, tmp_path / "tools" / name)
    git_dir = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"], text=True).strip()
    (tmp_path / ".git").write_text(f"gitdir: {git_dir}\n")  # history is only read, never changed
    (tmp_path / "scenes.json").write_text("[]\n")
    (tmp_path / "README.md").write_text("Scans are CC BY 4.0.")
    (tmp_path / "LICENSE").write_text("Fixture license")
    candidate = tmp_path / "candidate/quad.glb"
    textured_quad(candidate)
    return tmp_path, candidate


def arguments(candidate):
    return [str(candidate), "--stem", "quad", "--title", "Quad", "--blurb", "Textured quad.",
            "--source", "Synthetic fixture capture", "--source-commit", "HEAD", "--up", "0,1,0"]


def snapshot(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_mesh_promote_replace_revert_and_status(mesh_gallery, capfd):
    root, candidate = mesh_gallery
    args = [*arguments(candidate), "--cleaning", "posekit --model full --masks"]
    before = snapshot(root)
    assert promote_scene.main([*args, "--dry-run"], root=root) == 0
    assert snapshot(root) == before
    assert promote_scene.main(args, root=root) == 0
    entry = json.loads((root / "scenes.json").read_text())[0]
    assert entry["mesh"] == "scenes/quad.glb"
    assert entry["splats"] == 0 and entry["triangles"] == 2 and entry["up"] == [0, 1, 0]
    page = root / entry["provenance"]
    assert "posekit --model full --masks" in page.read_text()
    old_files = {p: p.read_bytes() for p in (page, page.with_suffix(".json"), root / entry["mesh"])}
    assert json.loads(page.with_suffix(".json").read_text())["mode"] == "app-export"
    result = checker.check_scene("quad", root=root)
    assert result["passed"] and result["triangles"] == 2
    row = status.gallery_status(root)[0]
    assert row["triangles"] == 2
    assert "| quad | Quad | mesh | 2 |" in status.table([row])
    # Replacement recovers source history and notes from the existing app-export sidecar.
    replacement = [str(candidate), "--stem", "quad", "--title", "Changed", "--blurb", "New", "--replace",
                   "--cleaning", "posekit --model low"]
    candidate.with_suffix(".spz").write_bytes(b"must not be copied for a mesh")
    assert promote_scene.main(replacement, root=root) == 0
    assert not (root / "scenes/quad.spz").exists()
    assert "posekit --model low" in page.read_text()
    before = snapshot(root)
    assert promote_scene.main(["--revert", "quad", "--dry-run"], root=root) == 0
    assert snapshot(root) == before
    assert promote_scene.main(["--revert", "quad"], root=root) == 0
    assert json.loads((root / "scenes.json").read_text())[0] == entry
    assert all(p.read_bytes() == data for p, data in old_files.items())
    assert "check_deliverable verdict: PASS" in capfd.readouterr().out


@pytest.mark.parametrize("cleaning", [[], ["--cleaning", "mesh; no cleaning"], ["--cleaning", "posekit"]])
def test_mesh_refuses_missing_detail_without_mutation(mesh_gallery, cleaning, capsys):
    root, candidate = mesh_gallery
    before = snapshot(root)
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main([*arguments(candidate), *cleaning], root=root)
    assert "mesher" in capsys.readouterr().err
    assert snapshot(root) == before


def test_mesh_candidate_record_and_budget(mesh_gallery, monkeypatch):
    root, candidate = mesh_gallery
    texture_bytes = textured_quad(candidate)
    result = checker.check_scene(str(candidate), root=root)
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["catalog"]["status"] == "not_applicable"
    assert by_name["size"]["status"] == "pass"
    assert by_name["mesh"]["detail"] == f"2 triangles; {texture_bytes} embedded texture bytes"
    monkeypatch.setitem(checker.BUDGETS, "web-mobile", (0, candidate.stat().st_size - 1))
    result = checker.check_scene(str(candidate), root=root)
    assert "size" in {c["name"] for c in result["checks"] if c["status"] == "fail"}
    (candidate.parent / "attempt.json").write_text(json.dumps({"cleaning": "posekit --model full --masks"}))
    assert promote_scene.main(arguments(candidate), root=root) == 0


def test_invalid_mesh_refused_before_archive(mesh_gallery):
    root, candidate = mesh_gallery
    candidate.write_bytes(b"invalid")
    before = snapshot(root)
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main([*arguments(candidate), "--replace", "--cleaning", "posekit --model full"], root=root)
    assert snapshot(root) == before
    assert not checker.check_scene(str(candidate), root=root)["passed"]


def test_splat_to_mesh_and_revert(mesh_gallery):
    from test_promote_scene import fake_sog

    root, candidate = mesh_gallery
    old = {"stem": "quad", "title": "Old splat", "splats": 42, "custom": "preserved",
           "provenance": "provenance/quad.html"}
    (root / "scenes.json").write_text(json.dumps([old]))
    fake_sog(root / "scenes/quad.sog")
    (root / "provenance/quad.html").write_text("Original solve provenance")
    assert promote_scene.main([*arguments(candidate), "--replace", "--cleaning", "posekit --model=full"],
                              root=root) == 0
    assert not (root / "scenes/quad.sog").exists()
    assert json.loads((root / "scenes.json").read_text())[0]["custom"] == "preserved"
    # Header-only SOG fixture cannot pass geometry checks; still restore the entire previous version.
    assert promote_scene.main(["--revert", "quad"], root=root) == 1
    assert json.loads((root / "scenes.json").read_text()) == [old]
    assert promote_scene.sog_count(root / "scenes/quad.sog") == 42
    assert not (root / "scenes/quad.glb").exists()
    assert (root / "provenance/quad.html").read_text() == "Original solve provenance"


def test_explicit_cleaning_overrides_candidate_record(mesh_gallery):
    root, candidate = mesh_gallery
    (candidate.parent / "attempt.json").write_text(json.dumps({"cleaning": "posekit --model low"}))
    assert promote_scene.main([*arguments(candidate), "--cleaning", "posekit --model full"], root=root) == 0
    page = (root / "provenance/quad.html").read_text()
    assert "posekit --model full" in page and "posekit --model low" not in page
