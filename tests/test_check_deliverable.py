"""Exercise file decoding, thresholds and CLI failure reporting."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("check", Path(__file__).parents[1] / "tools/check_deliverable.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance/test.html").write_text("<p>Test provenance</p>")
    (tmp_path / "LICENSE").write_text("Apache-2.0")
    (tmp_path / "README.md").write_text("Scans published here are CC BY 4.0.")
    (tmp_path / "scenes.json").write_text(
        json.dumps([{"stem": "test", "splats": 64, "provenance": "provenance/test.html"}])
    )
    return tmp_path


def ply(root, *, dirty=False, encoding="ascii", nonfinite=False):
    names = sorted(check.REQUIRED, reverse=True)  # x/y/z need not lead the header.
    data = np.zeros((64, len(names)))
    pos = np.array(np.meshgrid(*([[-1, -0.3, 0.3, 1]] * 3))).reshape(3, -1).T
    for i, axis in enumerate("xyz"):
        data[:, names.index(axis)] = pos[:, i]
    data[:, names.index("opacity")] = 4
    data[:, names.index("rot_0")] = 1
    for i in range(3):
        data[:, names.index(f"scale_{i}")] = -6
    if dirty:
        data[:3, names.index("x")] = 100
        data[0, names.index("scale_0")] = 5  # one of 64 > 1%, even with inflated bounds
    if nonfinite:
        data[0, names.index("f_dc_0")] = np.nan
    header = f"ply\nformat {encoding} 1.0\nelement vertex 64\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    path = root / "scenes/test.ply"
    with path.open("wb") as f:
        f.write(header.encode())
        if encoding == "ascii":
            np.savetxt(f, data)
        else:
            f.write(data.astype(">f4" if encoding == "binary_big_endian" else "<f4").tobytes())
    return path


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_clean_passes(repo, encoding):
    ply(repo, encoding=encoding)
    result = check.check_scene("test", root=repo)
    assert result["passed"]
    assert result["count"] == 64
    assert result["metrics"]["extent"] == [2, 2, 2]


def test_dirty_fails_with_right_lines(repo, monkeypatch, capsys):
    ply(repo, dirty=True)
    monkeypatch.setattr(check, "ROOT", repo)
    original = check.check_scene
    monkeypatch.setattr(check, "check_scene", lambda t, p, **kwargs: original(t, p, root=repo))
    assert check.main(["test"]) == 1
    text = capsys.readouterr().out
    assert "[FAIL] floater:" in text
    assert "[FAIL] fog:" in text
    assert check.main(["--all", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["metrics"]["floater_fraction"] == 3 / 64
    assert result["metrics"]["fog_fraction"] == 1 / 64


def test_nonfinite_and_catalog_mismatch(repo):
    ply(repo, nonfinite=True)
    catalog = json.loads((repo / "scenes.json").read_text())
    catalog[0]["splats"] = 100
    (repo / "scenes.json").write_text(json.dumps(catalog))
    result = check.check_scene("test", root=repo)
    failed = {c["name"] for c in result["checks"] if c["status"] == "fail"}
    assert {"catalog", "nonfinite"} <= failed
    json.dumps(result, allow_nan=False)


def test_splat_fails_fab_and_missing_provenance(repo):
    ply(repo)
    (repo / "provenance/test.html").unlink()
    result = check.check_scene("test", "fab", root=repo)
    failed = {c["name"] for c in result["checks"] if c["status"] == "fail"}
    assert {"format", "provenance"} <= failed


@pytest.mark.parametrize("payload", [b"", b"ply\n", b"ply\nformat ascii 1.0\nelement vertex 1\nend_header\n"])
def test_bad_ply(repo, payload):
    (repo / "scenes/test.ply").write_bytes(payload)
    result = check.check_scene("test", root=repo)
    assert result["checks"][0]["status"] == "fail"


def test_translucency_and_zero_weight():
    pos = np.array([[0, 0, 0], [1, 1, 1]], dtype=float)
    m = check.cleanliness((pos, pos * 0, np.zeros(2), pos))
    assert m["translucent_fraction"] == 1
    assert m["floater_fraction"] is None


def test_budget_boundaries(repo, monkeypatch):
    path = ply(repo)
    monkeypatch.setitem(check.BUDGETS, "web-mobile", (64, path.stat().st_size))
    assert check.check_scene("test", root=repo)["passed"]
    monkeypatch.setitem(check.BUDGETS, "web-mobile", (63, path.stat().st_size - 1))
    r = check.check_scene("test", root=repo)
    assert {"count", "size"} <= {c["name"] for c in r["checks"] if c["status"] == "fail"}


@pytest.mark.parametrize("extension", ["sog", "spz"])
def test_bad_compressed_formats(repo, extension):
    (repo / f"scenes/test.{extension}").write_bytes(b"invalid container")
    result = check.check_scene(f"scenes/test.{extension}", root=repo)
    assert not result["passed"]
    assert result["checks"][0]["status"] == "fail"


def test_missing_file(repo):
    result = check.check_scene("missing", root=repo)
    assert not result["passed"]
    assert result["size_bytes"] is None


def test_sog_decode(repo):
    import io
    import zipfile

    from PIL import Image

    meta = {
        "version": 2,
        "count": 64,
        "means": {"mins": [-1, -1, -1], "maxs": [1, 1, 1], "files": ["low.webp", "high.webp"]},
        "scales": {"codebook": [-6] * 256, "files": ["scales.webp"]},
        "sh0": {"codebook": [0] * 256, "files": ["sh0.webp"]},
        "quats": {"files": ["quats.webp"]},
    }
    with zipfile.ZipFile(repo / "scenes/test.sog", "w") as z:
        z.writestr("meta.json", json.dumps(meta))
        for name in ["low.webp", "high.webp", "scales.webp", "sh0.webp", "quats.webp"]:
            a = np.zeros((8, 8, 4), dtype=np.uint8)
            a[:, :, 3] = 255
            if name == "high.webp":
                a[4:, :, :3] = 255
            buf = io.BytesIO()
            Image.fromarray(a).save(buf, format="WEBP", lossless=True)
            z.writestr(name, buf.getvalue())
    n, arrays = check.read_sog(repo / "scenes/test.sog")
    assert n == 64
    assert arrays[0].shape == (64, 3)
    assert np.allclose(arrays[1], np.exp(-6))
    assert np.all(arrays[2] == 1)


def test_place_reports_floaters_without_failing_on_them(repo):
    # A place is its surroundings: distance from the centre is scenery there,
    # so the floater rule is reported, not enforced; every other rule stands.
    ply(repo, dirty=True)
    catalog = json.loads((repo / "scenes.json").read_text())
    catalog[0]["subject"] = "place"
    (repo / "scenes.json").write_text(json.dumps(catalog))
    result = check.check_scene("test", root=repo)
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["floater"]["status"] == "info"
    assert "scenery" in by_name["floater"]["detail"]
    assert by_name["fog"]["status"] == "fail"  # the dirty fixture's oversized splat still fails
    # --subject on the command line overrides the catalog the other way
    result = check.check_scene("test", root=repo, subject="object")
    assert {c["name"]: c["status"] for c in result["checks"]}["floater"] == "fail"
