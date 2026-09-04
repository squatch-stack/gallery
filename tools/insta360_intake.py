#!/usr/bin/env python3
"""Check whether a stitched 360 export is suitable for reframe360.py."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
TAG_ALIASES = {
    "projection_type": ("ProjectionType", "XMP-GPano:ProjectionType"),
    "full_pano_width": ("FullPanoWidthPixels", "XMP-GPano:FullPanoWidthPixels"),
    "full_pano_height": ("FullPanoHeightPixels", "XMP-GPano:FullPanoHeightPixels"),
    "heading": ("PoseHeadingDegrees", "XMP-GPano:PoseHeadingDegrees"),
    "pitch": ("PosePitchDegrees", "XMP-GPano:PosePitchDegrees"),
    "roll": ("PoseRollDegrees", "XMP-GPano:PoseRollDegrees"),
    "software": ("Software", "XMP-xmp:CreatorTool", "CreatorTool", "Encoder"),
    "capture_time": ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "CreationTime"),
    "camera_model": ("Model", "CameraModelName", "QuickTime:Model"),
}


def _executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path("/opt/homebrew/bin") / name
    return str(candidate) if candidate.is_file() else None


def _first(mapping: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
        suffix = ":" + name.split(":")[-1]
        for key, value in mapping.items():
            if key.endswith(suffix) and value not in (None, ""):
                return value
    return None


def read_exiftool(path: Path) -> dict[str, Any]:
    executable = _executable("exiftool")
    if not executable:
        return {}
    command = [executable, "-j", "-G1", "-n", "-a", str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    records = json.loads(result.stdout)
    return records[0] if records else {}


def parse_xmp_packet(data: bytes) -> dict[str, str]:
    """Extract simple XMP attributes/elements without requiring an XML package."""
    text = data.decode("utf-8", errors="ignore")
    packet = re.search(r"<\?xpacket\b.*?<\/x:xmpmeta\s*>", text, re.DOTALL | re.IGNORECASE)
    if packet:
        text = packet.group(0)
    values: dict[str, str] = {}
    for names in TAG_ALIASES.values():
        canonical = names[0]
        for alias in names:
            local = alias.split(":")[-1]
            escaped = re.escape(local)
            attribute = re.search(rf"(?:[\w.-]+:)?{escaped}\s*=\s*(['\"])(.*?)\1", text, re.DOTALL)
            element = re.search(
                rf"<(?:[\w.-]+:)?{escaped}\b[^>]*>(.*?)</(?:[\w.-]+:)?{escaped}\s*>",
                text,
                re.DOTALL,
            )
            match = attribute or element
            if match:
                values[canonical] = (match.group(2) if attribute else match.group(1)).strip()
                break
    return values


def read_ffprobe(path: Path) -> dict[str, Any]:
    executable = _executable("ffprobe")
    if not executable:
        return {}
    command = [
        executable,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,tags,side_data_list:format=format_name,tags",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def inspect(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".insv":
        return {
            "file": str(path),
            "ready": False,
            "missing": ["stitched standard equirectangular export (MP4 or JPG); .insv is a camera original"],
            "warnings": [],
        }
    if suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
        raise ValueError(f"unsupported file type: {suffix or '(none)'}")

    raw = read_exiftool(path)
    if not raw:
        raw = parse_xmp_packet(path.read_bytes())
    metadata = {key: _first(raw, aliases) for key, aliases in TAG_ALIASES.items()}
    ffprobe: dict[str, Any] = {}
    width = _first(raw, ("ImageWidth", "ExifImageWidth", "SourceImageWidth"))
    height = _first(raw, ("ImageHeight", "ExifImageHeight", "SourceImageHeight"))
    spherical: list[dict[str, Any]] = []

    if suffix in VIDEO_SUFFIXES:
        ffprobe = read_ffprobe(path)
        video = next((stream for stream in ffprobe.get("streams", []) if stream.get("codec_type") == "video"), {})
        width, height = video.get("width", width), video.get("height", height)
        spherical = [
            item
            for item in video.get("side_data_list", [])
            if "spherical" in str(item.get("side_data_type", "")).lower()
            or "stereo" in str(item.get("side_data_type", "")).lower()
        ]
        tags = {**ffprobe.get("format", {}).get("tags", {}), **video.get("tags", {})}
        for key, aliases in TAG_ALIASES.items():
            metadata[key] = metadata[key] or _first(tags, aliases)
    else:
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Image.UnidentifiedImageError as error:
            raise ValueError(f"cannot decode image: {path}") from error

    width = int(width) if width is not None else None
    height = int(height) if height is not None else None
    aspect = width / height if width and height else None
    projection = str(metadata["projection_type"] or "").lower()
    spherical_projection = any(
        "equirectangular" in json.dumps(item).lower() for item in spherical
    )
    missing: list[str] = []
    warnings: list[str] = []
    if not width or not height:
        missing.append("decodable pixel dimensions")
    elif width != 2 * height:
        missing.append(f"exact 2:1 dimensions (found {width}x{height})")
    if projection != "equirectangular" and not spherical_projection:
        missing.append("equirectangular projection metadata (GPano XMP or MP4 spherical side data)")
    if not metadata["software"]:
        warnings.append("stitching/export software tag is absent")
    if not any(metadata[key] is not None for key in ("heading", "pitch", "roll")):
        warnings.append("pose heading/pitch/roll tags are absent; reframe360 will use its default orientation")
    full_width = metadata["full_pano_width"]
    full_height = metadata["full_pano_height"]
    if full_width is not None and width and int(float(full_width)) != width:
        warnings.append(f"GPano full width {full_width} differs from image width {width}")
    if full_height is not None and height and int(float(full_height)) != height:
        warnings.append(f"GPano full height {full_height} differs from image height {height}")

    return {
        "file": str(path),
        "kind": "video" if suffix in VIDEO_SUFFIXES else "image",
        "dimensions": {"width": width, "height": height, "aspect": aspect},
        "projection_type": metadata["projection_type"],
        "full_pano_width": full_width,
        "full_pano_height": full_height,
        "pose": {"heading": metadata["heading"], "pitch": metadata["pitch"], "roll": metadata["roll"]},
        "stitching_software": metadata["software"],
        "capture_time": metadata["capture_time"],
        "camera_model": metadata["camera_model"],
        "mp4_spherical_side_data": spherical,
        "ready": not missing,
        "missing": missing,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="stitched JPG/PNG/TIFF or MP4/MOV export")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    arguments = parser.parse_args()
    try:
        report = inspect(arguments.file)
    except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        parser.error(str(error))
    if arguments.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"File: {report['file']}")
        if "dimensions" in report:
            dimensions = report["dimensions"]
            print(f"Dimensions: {dimensions['width']}x{dimensions['height']} (aspect {dimensions['aspect']})")
            print(f"Projection: {report['projection_type'] or 'not tagged'}")
            print(f"Pose H/P/R: {report['pose']['heading']} / {report['pose']['pitch']} / {report['pose']['roll']}")
            print(f"Stitching software: {report['stitching_software'] or 'not tagged'}")
            print(f"Capture time: {report['capture_time'] or 'not tagged'}")
            print(f"Camera model: {report['camera_model'] or 'not tagged'}")
        print("READY FOR REFRAME360" if report["ready"] else "NOT READY FOR REFRAME360")
        for item in report["missing"]:
            print(f"MISSING: {item}")
        for item in report["warnings"]:
            print(f"WARNING: {item}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
