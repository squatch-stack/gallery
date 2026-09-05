"""Subject-shaped crops, using only deterministic synthetic scene data."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("holo", reason="tools/clean_export.py exports through hdc-holo; CI has no holo")

TOOL = Path(__file__).resolve().parents[1] / "tools/clean_export.py"
SPEC = importlib.util.spec_from_file_location("clean_export_shapes", TOOL)
clean = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clean)


def legacy_box(pos, alpha, center, quantile, margin, radius):
    # Frozen pre-B11 crop calculation; compare selection bytes, not just counts.
    if radius > 0:
        lo = center - radius
        hi = center + radius
    else:
        r = clean.weighted_quantile(np.abs(pos - center).max(axis=1), alpha, quantile)
        lo = center - margin * r
        hi = center + margin * r
    return np.all((pos >= lo) & (pos <= hi), axis=1), lo, hi


@pytest.mark.parametrize("radius", [0, 0.75])
@pytest.mark.parametrize("quantile", [0.0, 0.60, 0.90, 1.0])
def test_box_matches_legacy_bytes(radius, quantile):
    rng = np.random.default_rng(11)
    pos = rng.normal(size=(4096, 3)) * [1, 2, 3]
    alpha = rng.uniform(0.05, 1, len(pos)).astype(np.float32)
    center = np.array([clean.weighted_quantile(pos[:, i], alpha, 0.5) for i in range(3)])
    expected, lo, hi = legacy_box(pos, alpha, center, quantile, 1.4, radius)
    actual, actual_lo, actual_hi, _, _ = clean.crop_mask(pos, alpha, center, quantile, 1.4, radius)
    assert actual.tobytes() == expected.tobytes()
    assert pos[actual].tobytes() == pos[expected].tobytes()
    assert actual_lo.tobytes() == lo.tobytes()
    assert actual_hi.tobytes() == hi.tobytes()


def test_cannon_sized_synthetic_regression(tmp_path):
    # Match the documented alpha arm's input/output sizes without requiring
    # its private source. This is synthetic evidence, not a real-cannon rerun.
    n, total = 159_755, 177_579
    pos = np.zeros((total, 3))
    pos[:n, 0] = np.linspace(-1, 1, n)
    pos[n:, 0] = np.linspace(10, 20, total - n)
    alpha = np.concatenate((np.full(n, 0.9), np.full(total - n, 0.5))).astype(np.float32)
    center = np.array([clean.weighted_quantile(pos[:, i], alpha, 0.5) for i in range(3)])
    old, _, _ = legacy_box(pos, alpha, center, 0.90, 1.4, 0)
    new, *_ = clean.crop_mask(pos, alpha, center, quantile=0.90)
    assert old.sum() == new.sum() == 159_755
    assert pos[old].tobytes() == pos[new].tobytes()
    rgba = np.column_stack((np.full((total, 3), 0.5), alpha)).astype(np.float32)
    quat = np.tile([1.0, 0.0, 0.0, 0.0], (total, 1))
    scale = np.full((total, 3), 0.001)
    source = tmp_path / "cannon-synthetic.ply"
    clean.save_ply(str(source), pos, scale, rgba, quat)
    loaded, loaded_scale, loaded_rgba, loaded_quat, _ = clean.load_gaussian_ply_any_order(source)
    loaded_alpha = loaded_rgba[:, 3]
    center = np.array([clean.weighted_quantile(loaded[:, i], loaded_alpha, 0.5) for i in range(3)])
    legacy, _, _ = legacy_box(loaded, loaded_alpha, center, 0.90, 1.4, 0)
    expected = tmp_path / "legacy.ply"
    clean.save_ply(str(expected), loaded[legacy], loaded_scale[legacy], loaded_rgba[legacy], loaded_quat[legacy])
    for shape in (None, "box"):
        output = tmp_path / (shape or "default")
        run = subprocess.run([
            sys.executable, "-B", str(TOOL), str(source), "--stem", "cannon",
            "--out", str(output), "--archive", str(output), "--crop-quantile", "0.90",
            *([] if shape is None else ["--crop-shape", shape]),
        ], capture_output=True, text=True, check=True)
        assert "177,579 -> 159,755 splats" in run.stdout
        assert (output / "cannon.ply").read_bytes() == expected.read_bytes()


def test_dense_core_and_square_corner_halo():
    rng = np.random.default_rng(11)
    core = rng.uniform(-0.5, 0.5, (2000, 3))
    # Thin halo: side midpoints and corners at the same box radius.
    halo = np.array([[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1],
                     [1, 0, 1], [1, 0, -1], [-1, 0, 1], [-1, 0, -1]])
    pos = np.vstack((core, halo))
    alpha = np.concatenate((np.ones(len(core)), np.full(len(halo), 0.05)))
    masks = {shape: clean.crop_mask(pos, alpha, np.zeros(3), quantile=1, margin=1,
                                   shape=shape, up=[0, 1, 0])[0]
             for shape in ("box", "ellipsoid", "cylinder")}
    assert masks["box"].all()
    for shape in ("ellipsoid", "cylinder"):
        assert masks[shape][-8:-4].all()  # boundary side points stay
        assert not masks[shape][-4:].any()  # footprint corners go
        assert masks[shape][:len(core)].sum() > 1000


def test_cylinder_flat_caps_and_ellipsoid_taper():
    pos = np.array([[0.8, 0.8, 0], [0, 1, 0], [0, 1.01, 0], [0.8, 0, 0.8]])
    args = (pos, np.ones(4), np.zeros(3))
    cylinder, *_ = clean.crop_mask(*args, radius=1, shape="cylinder", up=[0, 1, 0])
    ellipsoid, *_ = clean.crop_mask(*args, radius=1, shape="ellipsoid")
    assert cylinder.tolist() == [True, True, False, False]
    assert ellipsoid.tolist() == [False, True, False, False]


def test_oblique_up_and_sign_and_magnitude():
    up = np.array([1.0, 2.0, 3.0])
    _, _, _, _, basis = clean.crop_mask(np.zeros((1, 3)), np.ones(1), np.zeros(3),
                                       radius=1, shape="cylinder", up=up)
    local = np.array([[0.5, 0, 0.8], [0.8, 0.8, 0], [0, 0, 1.1]])
    center = np.array([4.0, -3.0, 2.0])
    pos = local @ basis.T + center
    for axis in (up, -up, 7 * up):
        mask, lo, hi, half, actual_basis = clean.crop_mask(
            pos, np.ones(3), center, radius=1, shape="cylinder", up=axis,
        )
        assert mask.tolist() == [True, False, False]
        np.testing.assert_allclose(actual_basis.T @ actual_basis, np.eye(3), atol=1e-15)
        assert np.all(pos[mask] >= lo) and np.all(pos[mask] <= hi)
        np.testing.assert_array_equal(half, np.ones(3))


def test_cylinder_fallback_and_weighted_local_extents():
    # The point of this fixture is that a 0.1%-weight outlier 50x further out
    # than the subject must not drag the crop with it. Two subject points are
    # too few to test that: holo's weighted quantile pins each sample to its
    # own cumulative midpoint, so with three samples one sample IS the grid and
    # a q0.9 target lands between the second point and the outlier, returning
    # ~61 rather than 2. From about thirty points on, the midpoint and
    # inclusive grids agree exactly, and on the real captures the whole change
    # is worth at most 76 splats in 753,314. So mirror the subject enough times
    # to measure the outlier rejection this test is named for.
    subject = np.array([[2, 0.2, 3], [-2, -0.2, -3]])
    pos = np.vstack([np.tile(subject, (20, 1)), [[100, 100, 100]]])
    alpha = np.concatenate([np.ones(len(pos) - 1), [0.001]])
    _, _, _, half, basis = clean.crop_mask(pos, alpha, np.zeros(3), quantile=0.9, margin=1,
                                         shape="cylinder")
    np.testing.assert_array_equal(basis[:, 2], [0, 1, 0])
    np.testing.assert_allclose(half, [2, 3, 0.2])


@pytest.mark.parametrize("shape", ["box", "ellipsoid", "cylinder"])
def test_radius_overrides_margin_and_quantile(shape):
    pos = np.array([[0, 0, 0], [0.9, 0, 0], [1.1, 0, 0]])
    mask, *_ = clean.crop_mask(pos, np.ones(3), np.zeros(3), quantile=0, margin=100,
                              radius=1, shape=shape, up=[0, 1, 0])
    assert mask.tolist() == [True, True, False]


@pytest.mark.parametrize("shape", ["ellipsoid", "cylinder"])
def test_zero_extents_are_exact_planes(shape):
    pos = np.array([[-1, 0, 0], [1, 0, 0], [0, 0.1, 0]])
    with np.errstate(all="raise"):
        mask, *_ = clean.crop_mask(pos, np.array([1, 1, 0.001]), np.zeros(3),
                                  quantile=0.9, margin=1, shape=shape, up=[0, 1, 0])
    assert mask.tolist() == [True, True, False]


@pytest.mark.parametrize("option", ["--up=0,0,0", "--up=nan,0,1", "--up=1,2", "--up=a,1,2",
                                    "--center=1,2", "--crop-quantile=1.1", "--crop-margin=0",
                                    "--crop-radius=-1", "--crop-shape=sphere"])
def test_invalid_crop_arguments_fail_before_loading(option):
    run = subprocess.run([sys.executable, "-B", str(TOOL), "missing.ply", "--stem", "unused", option],
                         capture_output=True, text=True)
    assert run.returncode == 2
    assert "error:" in run.stderr
    assert "Traceback" not in run.stderr
