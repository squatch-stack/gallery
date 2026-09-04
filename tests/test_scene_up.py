"""Gravity recovery, file frames, rejection and CLI compatibility without COLMAP."""

import io
import json
import sys
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import scene_up


def rotation(quaternion):
    w, x, y, z = np.asarray(quaternion) / np.linalg.norm(quaternion)
    return np.array([
        [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
        [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
        [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
    ])


def cloud(matrix=None, noise=0.004):
    rng = np.random.default_rng(123)
    ground = rng.uniform(-3, 3, (1800, 3))
    ground[:, 1] = rng.normal(0, noise, len(ground))
    column = rng.normal(0, 0.15, (2400, 3))
    column[:, 1] = rng.uniform(0.15, 5, len(column))
    p = np.concatenate([ground, column])
    matrix = np.eye(3) if matrix is None else matrix
    return p @ matrix.T + [7, -3, 11], np.full(len(p), 0.9), matrix[:, 1]


def angle(actual, expected):
    return float(np.degrees(np.arccos(np.clip(np.dot(actual, expected) / np.linalg.norm(expected), -1, 1))))


def write_cloud(path, positions, alpha):
    """Independent file-frame fixtures; both formats store unflipped axes."""
    if path.suffix == ".ply":
        names = sorted(["x", "y", "z", "opacity"] + [
            f"{prefix}_{i}" for prefix, count in [("scale", 3), ("rot", 4), ("f_dc", 3)] for i in range(count)
        ])
        records = np.zeros(len(positions), dtype=[(name, "<f4") for name in names])
        for i, name in enumerate(("x", "y", "z")):
            records[name] = positions[:, i]
        records["opacity"] = np.log(alpha / (1 - alpha))
        records["rot_0"] = 1
        header = "ply\nformat binary_little_endian 1.0\nelement vertex " + str(len(positions)) + "\n"
        header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
        path.write_bytes(header.encode() + records.tobytes())
        return
    logpos = np.sign(positions) * np.log1p(np.abs(positions))
    lo, hi = logpos.min(axis=0), logpos.max(axis=0)
    quantized = np.rint((logpos - lo) / (hi - lo) * 65535).astype(np.uint16)
    planes = {}
    for name, values in [("low", quantized % 256), ("high", quantized // 256)]:
        pixels = np.full((len(positions), 4), 255, dtype=np.uint8)
        pixels[:, :3] = values
        planes[name] = pixels
    planes["scale"] = np.zeros((len(positions), 4), dtype=np.uint8)
    planes["color"] = planes["scale"].copy()
    planes["color"][:, 3] = np.rint(alpha * 255)
    planes["quat"] = np.full((len(positions), 4), 252, dtype=np.uint8)
    meta = {"version": 2, "count": len(positions),
            "means": {"files": ["low.png", "high.png"], "mins": lo.tolist(), "maxs": hi.tolist()},
            "scales": {"files": ["scale.png"], "codebook": [-3]},
            "sh0": {"files": ["color.png"], "codebook": [0]},
            "quats": {"files": ["quat.png"]}}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("meta.json", json.dumps(meta))
        for name, pixels in planes.items():
            stream = io.BytesIO()
            Image.fromarray(pixels.reshape(60, 70, 4)).save(stream, format="PNG")
            archive.writestr(name + ".png", stream.getvalue())


@pytest.mark.parametrize("quaternion", [[1, 0, 0, 0], [0.7, 0.2, -0.4, 0.5], [0.1, -0.8, 0.3, 0.5]])
def test_rotated_ground_column(quaternion):
    positions, alpha, expected = cloud(rotation(quaternion))
    result = scene_up.estimate_cloud_up(positions, alpha)
    assert result["reason"] is None
    assert angle(result["up"], expected) < 2
    assert 0.40 < result["inliers"] < 0.46
    assert min(result["extent"]) > 4.5
    assert result == scene_up.estimate_cloud_up(positions, alpha)


@pytest.mark.parametrize("extension", [".ply", ".sog"])
def test_cannon_frame_cli(tmp_path, capsys, extension):
    expected = np.array([-0.3973, -0.5569, 0.7294])
    expected /= np.linalg.norm(expected)
    # Quaternion rotating +Y directly onto the catalog's cannon vector.
    axis = np.cross([0, 1, 0], expected)
    matrix = rotation([1 + expected[1], *axis])
    positions, alpha, _ = cloud(matrix)
    path = tmp_path / ("cannon" + extension)
    write_cloud(path, positions * [1, -1, -1], alpha)
    assert scene_up.main(["--from-cloud", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert angle(result["up"], expected) < 2
    assert result["up"] == result["from_cloud"]["up"]
    assert result["views"] == 0
    assert result["focus"] is None
    assert result["from_cloud"]["agreement_deg"] == dict.fromkeys(scene_up.AXES)
    assert result["frame"] == scene_up.FRAME


@pytest.mark.parametrize("kind", ["volume", "sphere", "line", "point", "empty", "transparent"])
def test_no_plane(kind):
    rng = np.random.default_rng(4)
    p = rng.normal(size=(4200, 3))
    a = np.ones(len(p))
    if kind == "sphere":
        p /= np.linalg.norm(p, axis=1)[:, None]
    elif kind == "line":
        p[:, 1:] = 0
    elif kind == "point":
        p[:] = 0
    elif kind == "empty":
        p, a = p[:0], a[:0]
    elif kind == "transparent":
        a[:] = 0.01
    result = scene_up.estimate_cloud_up(p, a)
    assert result["up"] is None
    assert result["reason"]
    json.dumps(result, allow_nan=False)


def test_sign_exact_ground_and_scale():
    p, a, expected = cloud(rotation([0.2, 0.7, -0.1, 0.3]), noise=0)
    # Ground dominates the alpha-weighted median, putting it exactly on the plane.
    a[1800:] = 0.21
    for scale in (0.001, 1000):
        result = scene_up.estimate_cloud_up(p * scale, a)
        assert angle(result["up"], expected) < 2


def test_alpha_filter_and_nonfinite():
    p, a, expected = cloud()
    rng = np.random.default_rng(99)
    distractors = rng.uniform(-8, 8, (5000, 3))
    distractors[:, 0] = 0
    p = np.concatenate([p, distractors, [[np.nan, 0, 0], [1, 2, 3]]])
    a = np.concatenate([a, np.full(len(distractors), 0.01), [0.9, np.nan]])
    result = scene_up.estimate_cloud_up(p, a)
    assert angle(result["up"], expected) < 2


def test_camera_compatibility_and_agreement(tmp_path, monkeypatch, capsys):
    class ImagePose:
        def __init__(self, matrix, center):
            self.pose = types.SimpleNamespace(rotation=types.SimpleNamespace(matrix=lambda: matrix),
                                              translation=-matrix @ center)

        def cam_from_world(self):
            return self.pose

    images = {}
    for i, theta in enumerate(np.linspace(0, 2 * np.pi, 8, endpoint=False)):
        center = np.array([4 * np.cos(theta), 0, 4 * np.sin(theta)])
        forward = -center / np.linalg.norm(center)
        down = np.array([0, 1, 0])
        matrix = np.stack([np.cross(down, forward), down, forward])
        images[i] = ImagePose(matrix, center)
    monkeypatch.setitem(sys.modules, "pycolmap", types.SimpleNamespace(
        Reconstruction=lambda path: types.SimpleNamespace(images=images)))
    assert scene_up.main([str(tmp_path), "--axis", "-y"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["up"] == [0, 1, 0]
    assert before["views"] == 8
    assert before["orbit_radius"] == 4
    p, a, _ = cloud(rotation([0.9, 0.3, 0.1, -0.2]))
    path = tmp_path / "cloud.ply"
    write_cloud(path, p * [1, -1, -1], a)
    assert scene_up.main([str(tmp_path), "--from-cloud", str(path)]) == 0
    after = json.loads(capsys.readouterr().out)
    assert {k: after[k] for k in before} == before
    for name, candidate in before["up_by_axis"].items():
        assert after["from_cloud"]["agreement_deg"][name] == pytest.approx(
            angle(after["from_cloud"]["up"], candidate))
    assert after["from_cloud"]["agreement_deg"]["-y"] > 20


def test_invalid_input(tmp_path, capsys):
    assert scene_up.main(["--from-cloud", str(tmp_path / "missing.ply")]) == 1
    assert "scene_up:" in capsys.readouterr().err
    with pytest.raises(ValueError, match="PLY or SOG"):
        scene_up.cloud_estimate(tmp_path / "cloud.spz")
    for argv in ([], ["--from-cloud", "test.ply", "--alpha-min", "nan"]):
        with pytest.raises(SystemExit) as error:
            scene_up.parse_args(argv)
        assert error.value.code == 2


def test_plane_without_subject():
    rng = np.random.default_rng(45)
    p = rng.uniform(-3, 3, (2000, 3))
    p[:, 1] = 0
    result = scene_up.estimate_cloud_up(p, np.ones(len(p)))
    assert result["up"] is None
    assert "orient" in result["reason"]
    assert result["inliers"] == 1


def test_rejected_cli(tmp_path, capsys):
    path = tmp_path / "volume.ply"
    rng = np.random.default_rng(4)
    write_cloud(path, rng.normal(size=(4200, 3)), np.full(4200, 0.9))
    assert scene_up.main(["--from-cloud", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["up"] is None
    assert result["from_cloud"]["up"] is None
    assert result["from_cloud"]["reason"]
