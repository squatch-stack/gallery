from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("reframe360", ROOT / "tools" / "reframe360.py")
assert SPEC and SPEC.loader
reframe360 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reframe360
SPEC.loader.exec_module(reframe360)


def synthetic_equirect(width: int = 1024, height: int = 512) -> np.ndarray:
    """Create a continuous coordinate image with grid lines, bands, labels, and checkerboard."""
    x = np.arange(width, dtype=np.float32)[None, :]
    y = np.arange(height, dtype=np.float32)[:, None]
    longitude = ((x + 0.5) / width - 0.5) * 2 * np.pi
    latitude = (0.5 - (y + 0.5) / height) * np.pi
    red = np.broadcast_to((np.sin(longitude) * 0.5 + 0.5) * 255, (height, width))
    green = np.broadcast_to((np.sin(latitude) * 0.5 + 0.5) * 255, (height, width))
    checker = ((x // 32 + y // 32) % 2) * 100 + 60
    image = np.stack((red, green, checker), axis=-1).astype(np.uint8)
    horizon = np.abs(latitude) < np.pi / height * 1.5
    image[np.broadcast_to(horizon, image.shape[:2])] = (255, 255, 255)
    pil = Image.fromarray(image)
    draw = ImageDraw.Draw(pil)
    for lon in range(-180, 180, 30):
        pixel_x = int((lon / 360 + 0.5) * width)
        draw.line((pixel_x, 0, pixel_x, height), fill=(30, 30, 30), width=1)
    for lat in range(-60, 61, 30):
        pixel_y = int((0.5 - lat / 180) * height)
        draw.line((0, pixel_y, width, pixel_y), fill=(220, 220, 0), width=1)
    draw.text((width // 2 + 8, height // 2 + 8), "0/0 FRONT", fill=(255, 0, 255))
    return np.asarray(pil)


def test_horizon_is_straight_in_side_faces() -> None:
    # Render a panorama where green encodes latitude; the equator must stay on one output row.
    height, width, size = 512, 1024, 257
    latitude_rows = np.linspace(255, 0, height, dtype=np.uint8)[:, None]
    source = np.zeros((height, width, 3), dtype=np.uint8)
    source[..., 1] = latitude_rows
    for view in reframe360.make_views(4):
        face = reframe360.render_view(source, view, size, 95.0)
        crossing = np.argmin(np.abs(face[..., 1].astype(np.int16) - 127), axis=0)
        assert np.ptp(crossing) <= 1
        assert abs(float(np.mean(crossing)) - size / 2) <= 1


def test_lon_lat_origin_lands_at_front_center() -> None:
    size = 257
    rays = reframe360.pinhole_rays(size, 95.0)
    map_x, map_y = reframe360.rays_to_equirect_map(rays, reframe360.make_views(4)[0], (512, 1024))
    center = size // 2
    assert abs(float(map_x[center, center]) - 511.5) < 1e-5
    assert abs(float(map_y[center, center]) - 255.5) < 1e-5


def test_face_pixel_round_trip_recovers_source_colour() -> None:
    source = synthetic_equirect()
    size = 255
    view = reframe360.make_views(4)[0]
    rays = reframe360.pinhole_rays(size, 90.0)
    map_x, map_y = reframe360.rays_to_equirect_map(rays, view, source.shape[:2])
    rendered = reframe360.render_view(source, view, size, 90.0, rays)
    expected = reframe360._numpy_bilinear(source, map_x, map_y)
    error = np.abs(rendered.astype(np.int16) - expected.astype(np.int16))
    assert float(np.quantile(error, 0.99)) <= 4.0


def test_numpy_remap_fallback() -> None:
    source = synthetic_equirect(256, 128)
    rays = reframe360.pinhole_rays(65, 95.0)
    view = reframe360.make_views(4)[3]
    map_x, map_y = reframe360.rays_to_equirect_map(rays, view, source.shape[:2])
    expected = reframe360._numpy_bilinear(source, map_x, map_y)
    original_cv2 = reframe360.cv2
    try:
        reframe360.cv2 = None
        actual = reframe360.remap_equirect(source, map_x, map_y)
    finally:
        reframe360.cv2 = original_cv2
    np.testing.assert_array_equal(actual, expected)


def test_near_duplicate_filter(tmp_path: Path) -> None:
    base = synthetic_equirect(256, 128)
    paths = [tmp_path / f"frame_{index}.png" for index in range(3)]
    Image.fromarray(base).save(paths[0])
    Image.fromarray(base).save(paths[1])
    changed = base.copy()
    changed[:, :64] = 255 - changed[:, :64]
    Image.fromarray(changed).save(paths[2])
    kept = reframe360.drop_near_duplicates([(path, index / 2) for index, path in enumerate(paths)])
    assert kept == [(paths[0], 0.0), (paths[2], 1.0)]


def test_cli_outputs_metadata_and_nadir_alpha(tmp_path: Path) -> None:
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    Image.fromarray(synthetic_equirect(512, 256)).save(inputs / "pano.png")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "reframe360.py"),
            str(inputs),
            "--face-size",
            "64",
            "--mask-nadir",
            "--out",
            str(output),
        ],
        check=True,
    )
    manifest = json.loads((output / "manifest.json").read_text())
    config = json.loads((output / "colmap_rig_config.json").read_text())
    assert manifest["frame_count"] == 1 and manifest["face_count"] == 6
    assert len(list((output / "images").glob("*.png"))) == 6
    assert Image.open(output / "images" / "frame_00000000_down.png").mode == "RGBA"
    assert config[0]["cameras"][0]["ref_sensor"] is True
    assert (output / "colmap_images" / "front" / "frame_00000000.png").exists()
