"""CPU-only geometry, CLI accounting, and ground feather checks."""

import importlib.util
import math
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

SPEC = importlib.util.spec_from_file_location("combine_masks", Path(__file__).parents[1] / "tools/combine_masks.py")
mask_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mask_tool)


def rectangle(scale):
    subject = np.zeros((200 * scale, 240 * scale), dtype=bool)
    subject[40 * scale:120 * scale, 90 * scale:110 * scale] = True
    return subject


@pytest.mark.parametrize("scale", [1, 2])
def test_disc_analytic_area_and_subject_preservation(scale):
    subject = rectangle(scale)
    alpha = mask_tool.combine(subject, np.ones_like(subject), 0.25)
    radius, half_width = 20 * scale, 10 * scale
    # Circle/rectangle overlap: integral of sqrt(r*r-x*x) + 0.5
    # over [-half_width, half_width]; bottom pixel centres anchor the disc.
    overlap = (half_width * math.sqrt(radius**2 - half_width**2)
               + radius**2 * math.asin(half_width / radius) + half_width)
    expected = subject.sum() + math.pi * radius**2 - overlap
    assert abs((alpha > 0).sum() - expected) < math.pi * radius**2 * 0.02
    assert np.all(alpha[subject] == 255)
    assert set(np.unique(alpha)) == {0, 255}


@pytest.mark.parametrize("scale", [1, 2])
def test_feather_only_in_ground_band(scale):
    subject = rectangle(scale)
    ground = np.ones_like(subject)
    width = 5
    hard = mask_tool.combine(subject, ground, 0.25)
    soft = mask_tool.combine(subject, ground, 0.25, width)
    y, x = np.indices(subject.shape)
    distance = np.hypot(x - (100 * scale - 0.5), y - (120 * scale - 1))
    intermediate = (soft > 0) & (soft < 255)
    assert intermediate.any()
    assert np.all(~subject[intermediate])
    assert np.all(distance[intermediate] > 20 * scale - width)
    assert np.all(distance[intermediate] < 20 * scale)
    assert np.all(soft[subject] == 255)
    assert np.all(soft <= hard)
    band = ~subject & (distance > 20 * scale - width) & (distance < 20 * scale)
    expected = np.rint(255 * (20 * scale - distance[band]) / width)
    np.testing.assert_array_equal(soft[band], expected)


def test_ground_boundary_and_nonoverlap():
    subject = rectangle(1)
    ground = np.ones_like(subject)
    ground[:, 115:] = False
    alpha = mask_tool.combine(subject, ground, 0.5, 5)
    assert alpha[125, 114] == 51
    assert alpha[125, 113] == 102
    assert alpha[125, 110] == 255
    assert np.all(alpha[~ground & ~subject] == 0)
    assert np.all(alpha[subject] == 255)


def test_cli_mixed_grids_missing_and_diagnostics(tmp_path, capsys):
    for name in ("tree", "ground"):
        (tmp_path / name).mkdir()
    for scale in (1, 2):
        Image.fromarray(rectangle(scale)).save(tmp_path / f"tree/view-{scale}.png")
        Image.new("1", (12, 10), 1).save(tmp_path / f"ground/view-{scale}.png")
    for stem, value in (("white", 255), ("empty", 0), ("tiny", 0)):
        im = Image.new("L", (100, 100), value)
        if stem == "tiny":
            im.putpixel((50, 50), 255)
        im.save(tmp_path / f"tree/{stem}.png")
        Image.new("1", (100, 100), 1).save(tmp_path / f"ground/{stem}.png")
    Image.new("1", (4, 4)).save(tmp_path / "tree/no-ground.png")
    Image.new("1", (4, 4)).save(tmp_path / "ground/no-subject.png")
    (tmp_path / "names.txt").write_text("neither.jpg\n")
    mask_tool.main([str(tmp_path), "--subject-masks", "tree", "--ground-masks", "ground",
                    "--out", "patch", "--radius", "0.25"])
    output = capsys.readouterr().out
    assert "written=5, skipped=3, missing subject=2, missing ground=2, suspicious subject=3" in output
    assert "subject fully white" in output
    assert "subject nearly empty" in output
    assert "mean per-view alpha coverage" in output
    assert "pixel-weighted alpha coverage" in output
    assert len(list((tmp_path / "patch").glob("*.png"))) == 5
    for scale in (1, 2):
        with Image.open(tmp_path / f"patch/view-{scale}.png") as im:
            assert im.size == (240 * scale, 200 * scale)
            np.testing.assert_array_equal(np.asarray(im), mask_tool.combine(
                rectangle(scale), np.ones_like(rectangle(scale)), 0.25))
    assert not np.asarray(Image.open(tmp_path / "patch/empty.png")).any()
    assert np.asarray(Image.open(tmp_path / "patch/white.png")).min() == 255


@pytest.mark.parametrize("flag,value", [("--radius", "-1"), ("--radius", "nan"),
                                       ("--feather", "-1"), ("--feather", "inf")])
def test_invalid_geometry_rejected(tmp_path, flag, value):
    with pytest.raises(SystemExit, match="2"):
        mask_tool.main([str(tmp_path), "--subject-masks", "tree", "--ground-masks", "ground",
                        "--out", "patch", "--radius", "0.25", flag, value])
    assert not list(tmp_path.iterdir())
