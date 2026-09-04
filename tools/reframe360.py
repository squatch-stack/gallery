#!/usr/bin/env python3
"""Render pinhole views from equirectangular images or video on the CPU."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


try:
    import cv2
except ImportError:  # pragma: no cover - exercised on installations without OpenCV
    cv2 = None


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class View:
    """A virtual camera whose basis vectors are expressed in the front-camera rig frame."""

    name: str
    right: tuple[float, float, float]
    down: tuple[float, float, float]
    forward: tuple[float, float, float]

    @property
    def cam_from_rig(self) -> np.ndarray:
        import numpy as np

        return np.asarray((self.right, self.down, self.forward), dtype=np.float64)


def yaw_view(degrees: float, name: str | None = None) -> View:
    """Create a level view at clockwise yaw ``degrees`` from the panorama front."""
    angle = math.radians(degrees)
    forward = (math.sin(angle), 0.0, math.cos(angle))
    right = (math.cos(angle), 0.0, -math.sin(angle))
    if name is None:
        normalized = degrees % 360.0
        name = f"yaw{normalized:07.2f}".replace(".", "p")
    return View(name, right, (0.0, 1.0, 0.0), forward)


def make_views(faces: int, yaw_offsets: Sequence[float] | None = None) -> list[View]:
    if yaw_offsets is None:
        sides = [
            yaw_view(0.0, "front"),
            yaw_view(90.0, "right"),
            yaw_view(180.0, "back"),
            yaw_view(270.0, "left"),
        ]
    else:
        sides = [yaw_view(offset) for offset in yaw_offsets]
    if faces == 4:
        return sides
    return [
        *sides,
        View("up", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
        View("down", (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    ]


def pinhole_rays(size: int, fov_degrees: float) -> np.ndarray:
    """Return normalized camera rays at pixel centers in x-right/y-down/z-forward coordinates."""
    import numpy as np

    focal = focal_length(size, fov_degrees)
    coords = np.arange(size, dtype=np.float32) + 0.5
    x = (coords - size / 2.0) / focal
    y = (coords - size / 2.0) / focal
    grid_x, grid_y = np.meshgrid(x, y)
    rays = np.stack((grid_x, grid_y, np.ones_like(grid_x)), axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
    return rays


def rays_to_equirect_map(
    rays_camera: np.ndarray, view: View, source_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Map camera rays to floating-point equirectangular source pixel coordinates."""
    import numpy as np

    # A row-vector camera ray is transformed by cam_from_rig to the rig frame.
    rays_rig = rays_camera @ view.cam_from_rig
    longitude = np.arctan2(rays_rig[..., 0], rays_rig[..., 2])
    latitude = np.arcsin(np.clip(-rays_rig[..., 1], -1.0, 1.0))
    height, width = source_shape
    map_x = ((longitude / (2.0 * np.pi) + 0.5) * width - 0.5).astype(np.float32)
    map_y = ((0.5 - latitude / np.pi) * height - 0.5).astype(np.float32)
    return map_x, map_y


def _numpy_bilinear(source: np.ndarray, map_x: np.ndarray, map_y: np.ndarray) -> np.ndarray:
    """Bilinear sampler with horizontal wrap and vertical clamping."""
    import numpy as np

    height, width = source.shape[:2]
    x0_raw = np.floor(map_x).astype(np.int64)
    y0 = np.floor(map_y).astype(np.int64)
    x_weight = (map_x - x0_raw)[..., None]
    y_weight = (map_y - y0)[..., None]
    x0 = x0_raw % width
    x1 = (x0_raw + 1) % width
    y0 = np.clip(y0, 0, height - 1)
    y1 = np.clip(y0 + 1, 0, height - 1)
    source_float = source.astype(np.float32)
    top = source_float[y0, x0] * (1.0 - x_weight) + source_float[y0, x1] * x_weight
    bottom = source_float[y1, x0] * (1.0 - x_weight) + source_float[y1, x1] * x_weight
    result = top * (1.0 - y_weight) + bottom * y_weight
    return np.clip(np.rint(result), 0, 255).astype(np.uint8)


def remap_equirect(source: np.ndarray, map_x: np.ndarray, map_y: np.ndarray) -> np.ndarray:
    import numpy as np

    if cv2 is None:
        return _numpy_bilinear(source, map_x, map_y)
    # BORDER_WRAP also wraps y, so pad the poles and wrap only x explicitly.
    padded = np.pad(source, ((1, 1), (0, 0), (0, 0)), mode="edge")
    return cv2.remap(
        padded,
        np.mod(map_x, source.shape[1]),
        np.clip(map_y + 1.0, 0.0, source.shape[0] + 1.0),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,
    )


def render_view(
    source: np.ndarray,
    view: View,
    size: int,
    fov_degrees: float,
    rays: np.ndarray | None = None,
) -> np.ndarray:
    rays = pinhole_rays(size, fov_degrees) if rays is None else rays
    map_x, map_y = rays_to_equirect_map(rays, view, source.shape[:2])
    return remap_equirect(source, map_x, map_y)


def focal_length(size: int, fov_degrees: float) -> float:
    return (size / 2.0) / math.tan(math.radians(fov_degrees) / 2.0)


def rotation_matrix_to_quaternion(matrix: np.ndarray) -> list[float]:
    """Convert a proper rotation matrix to COLMAP's [w, x, y, z] order."""
    import numpy as np

    trace = float(np.trace(matrix))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        values = [
            0.25 * scale,
            (matrix[2, 1] - matrix[1, 2]) / scale,
            (matrix[0, 2] - matrix[2, 0]) / scale,
            (matrix[1, 0] - matrix[0, 1]) / scale,
        ]
    else:
        index = int(np.argmax(np.diag(matrix)))
        next_index = (index + 1) % 3
        last_index = (index + 2) % 3
        scale = (
            math.sqrt(1.0 + matrix[index, index] - matrix[next_index, next_index] - matrix[last_index, last_index])
            * 2.0
        )
        xyz = [0.0, 0.0, 0.0]
        xyz[index] = 0.25 * scale
        xyz[next_index] = (matrix[next_index, index] + matrix[index, next_index]) / scale
        xyz[last_index] = (matrix[last_index, index] + matrix[index, last_index]) / scale
        values = [
            (matrix[last_index, next_index] - matrix[next_index, last_index]) / scale,
            *xyz,
        ]
    quaternion = np.asarray(values, dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    if quaternion[0] < 0:
        quaternion *= -1
    return [float(value) for value in quaternion]


def _read_rgb(path: Path) -> np.ndarray:
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _ffmpeg_path() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    homebrew = Path("/opt/homebrew/bin/ffmpeg")
    if homebrew.is_file():
        return str(homebrew)
    raise RuntimeError("ffmpeg was not found on PATH or at /opt/homebrew/bin/ffmpeg")


def load_frames(input_path: Path, fps: float, temporary_directory: Path) -> list[tuple[Path, float | None]]:
    if input_path.is_dir():
        paths = sorted(path for path in input_path.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
        if not paths:
            raise ValueError(f"no PNG or JPEG images found in {input_path}")
        return [(path, None) for path in paths]
    if input_path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise ValueError(
            "input must be an image directory or an MP4/MOV video (convert .insv with Insta360 software first)"
        )
    pattern = temporary_directory / "frame_%08d.png"
    command = [
        _ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        f"fps={fps}",
        str(pattern),
    ]
    subprocess.run(command, check=True)
    extracted = sorted(temporary_directory.glob("frame_*.png"))
    return [(path, index / fps) for index, path in enumerate(extracted)]


def drop_near_duplicates(
    frames: Sequence[tuple[Path, float | None]], threshold: float = 1.5
) -> list[tuple[Path, float | None]]:
    """Drop adjacent sampled frames with mean thumbnail RGB difference below threshold."""
    import numpy as np
    from PIL import Image

    kept: list[tuple[Path, float | None]] = []
    previous: np.ndarray | None = None
    for path, timestamp in frames:
        with Image.open(path) as image:
            thumbnail = np.asarray(
                image.convert("RGB").resize((64, 32), Image.Resampling.BILINEAR),
                dtype=np.float32,
            )
        if previous is None or float(np.mean(np.abs(thumbnail - previous))) >= threshold:
            kept.append((path, timestamp))
            previous = thumbnail
    return kept


def add_nadir_alpha(image: np.ndarray) -> np.ndarray:
    """Mask a soft-edged circle around the optical axis of the down face."""
    import numpy as np

    height, width = image.shape[:2]
    y, x = np.ogrid[:height, :width]
    radius = np.hypot(x - width / 2.0, y - height / 2.0)
    inner, outer = min(height, width) * 0.18, min(height, width) * 0.24
    alpha = np.clip((radius - inner) / (outer - inner), 0.0, 1.0)
    return np.dstack((image, np.rint(alpha * 255).astype(np.uint8)))


def write_metadata(
    output: Path,
    views: Sequence[View],
    size: int,
    fov: float,
    manifest: dict[str, object],
) -> None:
    focal = focal_length(size, fov)
    camera_lines = [
        "# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]",
        f"# Face mapping: {', '.join(view.name for view in views)}",
    ]
    for camera_id, _view in enumerate(views, start=1):
        camera_lines.append(
            f"{camera_id} PINHOLE {size} {size} {focal:.12g} {focal:.12g} {size / 2:.12g} {size / 2:.12g}"
        )
    (output / "cameras.txt").write_text("\n".join(camera_lines) + "\n", encoding="utf-8")

    sensors = []
    rig_cameras = []
    for index, view in enumerate(views):
        quaternion = rotation_matrix_to_quaternion(view.cam_from_rig)
        sensors.append(
            {
                "name": view.name,
                "camera_id": index + 1,
                "cam_from_rig_rotation_wxyz": quaternion,
                "cam_from_rig_translation": [0.0, 0.0, 0.0],
                "reference": index == 0,
            }
        )
        camera = {
            "image_prefix": f"{view.name}/",
            "camera_model_name": "PINHOLE",
            "camera_params": [focal, focal, size / 2.0, size / 2.0],
        }
        if index == 0:
            camera["ref_sensor"] = True
        else:
            camera["cam_from_rig_rotation"] = quaternion
            camera["cam_from_rig_translation"] = [0.0, 0.0, 0.0]
        rig_cameras.append(camera)
    rig = {
        "coordinate_system": "COLMAP camera x-right/y-down/z-forward",
        "rotation_order": "wxyz",
        "reference_sensor": views[0].name,
        "sensors": sensors,
    }
    (output / "rig.json").write_text(json.dumps(rig, indent=2) + "\n", encoding="utf-8")
    (output / "colmap_rig_config.json").write_text(
        json.dumps([{"cameras": rig_cameras}], indent=2) + "\n", encoding="utf-8"
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def process(args: argparse.Namespace) -> dict[str, object]:
    from PIL import Image

    output = args.out.resolve()
    images_dir = output / "images"
    rig_images_dir = output / "colmap_images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rig_images_dir.mkdir(parents=True, exist_ok=True)
    views = make_views(args.faces, args.yaw_offsets)
    for view in views:
        (rig_images_dir / view.name).mkdir(parents=True, exist_ok=True)
    rays = pinhole_rays(args.face_size, args.fov)
    frame_records: list[dict[str, object]] = []
    render_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix="reframe360-", dir=output) as temporary:
        frames = load_frames(args.input.resolve(), args.fps, Path(temporary))
        if args.input.is_file():
            frames = drop_near_duplicates(frames)
        for frame_number, (source_path, timestamp) in enumerate(frames):
            source = _read_rgb(source_path)
            frame_name = f"frame_{frame_number:08d}"
            started = time.perf_counter()
            outputs = []
            for view in views:
                rendered = render_view(source, view, args.face_size, args.fov, rays)
                if args.mask_nadir and view.name == "down":
                    rendered = add_nadir_alpha(rendered)
                filename = f"{frame_name}_{view.name}.png"
                flat_path = images_dir / filename
                Image.fromarray(rendered).save(flat_path)
                # rig_configurator requires per-camera prefixes and identical frame filenames.
                rig_path = rig_images_dir / view.name / f"{frame_name}.png"
                try:
                    if rig_path.exists():
                        rig_path.unlink()
                    rig_path.hardlink_to(flat_path)
                except OSError:
                    shutil.copy2(flat_path, rig_path)
                outputs.append(str(flat_path.relative_to(output)))
            elapsed = time.perf_counter() - started
            render_seconds += elapsed
            frame_records.append(
                {
                    "frame": frame_name,
                    "source": str(args.input.resolve()) if args.input.is_file() else str(source_path),
                    "source_sample": source_path.name if args.input.is_file() else None,
                    "time_seconds": timestamp,
                    "render_seconds": elapsed,
                    "images": outputs,
                }
            )
    manifest: dict[str, object] = {
        "input": str(args.input.resolve()),
        "source_type": "video" if args.input.is_file() else "directory",
        "fps": args.fps if args.input.is_file() else None,
        "requested_faces": args.faces,
        "face_count": len(views),
        "faces": [view.name for view in views],
        "face_size": args.face_size,
        "fov_degrees": args.fov,
        "mask_nadir": args.mask_nadir,
        "frame_count": len(frame_records),
        "frames": frame_records,
        "mean_render_seconds_per_frame": render_seconds / len(frame_records) if frame_records else None,
        "colmap_image_root": "colmap_images",
    }
    write_metadata(output, views, args.face_size, args.fov, manifest)
    return manifest


def parse_yaw_offsets(value: str) -> list[float]:
    try:
        offsets = [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("yaw offsets must be comma-separated numbers") from error
    if not offsets:
        raise argparse.ArgumentTypeError("at least one yaw offset is required")
    normalized = [offset % 360.0 for offset in offsets]
    if len(set(normalized)) != len(normalized):
        raise argparse.ArgumentTypeError("yaw offsets must be unique modulo 360 degrees")
    return offsets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        help="directory of equirectangular images or an MP4/MOV video",
    )
    parser.add_argument("--fps", type=float, default=2.0, help="video sampling rate (default: 2)")
    parser.add_argument(
        "--faces",
        type=int,
        choices=(4, 6),
        default=6,
        help="render four sides or sides plus up/down",
    )
    parser.add_argument("--face-size", type=int, default=2048, help="square output size (default: 2048)")
    parser.add_argument(
        "--fov",
        type=float,
        default=95.0,
        help="horizontal and vertical field of view in degrees",
    )
    parser.add_argument(
        "--yaw-offsets",
        type=parse_yaw_offsets,
        help="replace four standard side views with comma-separated yaws",
    )
    parser.add_argument(
        "--mask-nadir",
        action="store_true",
        help="add alpha and mask the center of the down face",
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.fps <= 0 or args.face_size <= 0 or not 0 < args.fov < 180:
        parser.error("--fps and --face-size must be positive; --fov must be between 0 and 180")
    try:
        manifest = process(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"Rendered {manifest['frame_count']} frames x {manifest['face_count']} views to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
