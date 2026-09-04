"""Contribution pruning through the CLI and the order-agnostic PLY loader."""

import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("holo", reason="tools/clean_export.py exports through hdc-holo; CI has no holo")

TOOL = Path(__file__).resolve().parents[1] / "tools/clean_export.py"
SPEC = importlib.util.spec_from_file_location("clean_export", TOOL)
clean_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clean_export)


@pytest.mark.parametrize("shape", [None, "box", "ellipsoid", "cylinder"])
def test_cli_keeps_large_opaque_splats(tmp_path, shape):
    n, m = 12, 48
    # Brush's alphabetical order puts positions last. Interleave both classes
    # so neither input order nor distance alone can select the right subset.
    fields = sorted(["x", "y", "z", "opacity"] + [
        f"{prefix}_{i}" for prefix, count in (("f_dc", 3), ("scale", 3), ("rot", 4))
        for i in range(count)
    ])
    rec = np.zeros(n + m, dtype=[(name, "<f4") for name in fields])
    large = np.arange(n + m) % 5 == 0
    rec["x"] = np.arange(n + m) / 10
    rec["opacity"] = np.where(large, np.log(0.99 / 0.01), np.log(0.03 / 0.97))
    rec["rot_0"] = 1
    for i, axis in enumerate((0.1, 0.2, 0.3)):
        rec[f"scale_{i}"] = np.log(np.where(large, axis, 0.0001))
    source = tmp_path / "source.ply"
    header = "\n".join([
        "ply", "format binary_little_endian 1.0", f"element vertex {len(rec)}",
        *(f"property float {name}" for name in fields), "end_header", "",
    ])
    source.write_bytes(header.encode("ascii") + rec.tobytes())
    run = subprocess.run([
        sys.executable, "-B", str(TOOL), str(source), "--stem", "pruned",
        "--out", str(tmp_path / "delivery"), "--archive", str(tmp_path / "archive"),
        "--center", "0,0,0", "--crop-radius", "10", "--target-count", str(n),
        *([] if shape is None else ["--crop-shape", shape]),
    ], capture_output=True, text=True, check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    pos, scale, rgba, _, _ = clean_export.load_gaussian_ply_any_order(tmp_path / "archive/pruned.ply")
    assert len(pos) == n
    np.testing.assert_allclose(pos[:, 0], rec["x"][large])
    np.testing.assert_allclose(scale, np.tile([0.1, 0.2, 0.3], (n, 1)), rtol=1e-6)
    np.testing.assert_allclose(rgba[:, 3], 0.99, rtol=1e-6)
    assert f"removed {m} splats" in run.stdout
    assert f"crop shape {shape or 'box'}; local half-extents" in run.stdout
    fraction = float(re.search(r"removed-mass fraction ([\deE.+-]+)", run.stdout)[1])
    expected = m * 0.03 * 0.0001**2 / (n * 0.99 * 0.2 * 0.3 + m * 0.03 * 0.0001**2)
    assert fraction == pytest.approx(expected, rel=1e-5)
    assert fraction < 1e-6


def test_footprint_uses_two_largest_axes_and_stable_ties():
    scale = np.array([[0.001, 3, 2], [2, 0.001, 3], [1, 1, 1], [0.001, 3, 2]])
    keep, fraction = clean_export.contribution_keep(scale, np.ones(4), 2)
    assert keep.tolist() == [True, True, False, False]
    assert fraction == pytest.approx(7 / 19)


@pytest.mark.parametrize("target", [3, 4])
def test_budget_at_or_above_count_is_unchanged(target):
    keep, fraction = clean_export.contribution_keep(np.ones((3, 3)), np.ones(3), target)
    assert keep.all()
    assert fraction == 0


def test_zero_mass_is_reported_without_nan():
    keep, fraction = clean_export.contribution_keep(np.ones((3, 3)), np.zeros(3), 1)
    assert keep.tolist() == [True, False, False]
    assert fraction == 0


@pytest.mark.parametrize("target", ["0", "-1"])
def test_cli_rejects_nonpositive_budget(target):
    run = subprocess.run([
        sys.executable, "-B", str(TOOL), "missing.ply", "--stem", "unused", "--target-count", target,
    ], capture_output=True, text=True)
    assert run.returncode == 2
    assert "--target-count must be positive" in run.stderr
