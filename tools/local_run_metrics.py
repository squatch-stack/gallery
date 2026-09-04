#!/usr/bin/env python3
"""Reconstruct local Brush metrics from exports and dataset metadata, without loading the cloud."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

EXPORT = re.compile(r".+-(\d+)\.ply$", re.IGNORECASE)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic", ".heif"}
SCHEMA_NOTE = (
    "wall_seconds is a wall_seconds_estimate from dataset directory ctime to final PLY mtime; "
    "it includes dataset build time if the dataset was built into the job dir. "
    "ctime is filesystem metadata-change time, not a reliable training start timestamp. "
    "seconds_per_1k_steps inherits this estimate. rc and peak_rss_mb are unknown (null); "
    "an export does not prove successful trainer exit. Flags are operator-supplied, not verified training records."
)


def final_export(job):
    exports = [(int(match[1]), path) for path in [*job.glob("*.ply"), *job.glob("*/*.ply")]
               if path.is_file() and (match := EXPORT.fullmatch(path.name))]
    if not exports:
        raise ValueError("no step-numbered PLY exports found")
    highest = max(step for step, _ in exports)
    matches = [path for step, path in exports if step == highest]
    if len(matches) != 1:
        raise ValueError("ambiguous highest-step exports; use a job directory with one export series")
    return highest, matches[0]


def ply_metrics(path):
    digest = hashlib.sha256()
    size = 0
    count = None
    with path.open("rb") as stream:
        if stream.readline(4096).strip() != b"ply":
            raise ValueError("invalid PLY magic")
        for _ in range(10000):
            line = stream.readline(4096)
            if not line or len(line) == 4096:
                raise ValueError("invalid or truncated PLY header")
            fields = line.split()
            if fields[:2] == [b"element", b"vertex"]:
                if count is not None or len(fields) != 3:
                    raise ValueError("invalid PLY vertex element")
                count = int(fields[2])
            if fields == [b"end_header"]:
                break
        else:
            raise ValueError("PLY header too large")
        if count is None or count <= 0:
            raise ValueError("PLY must contain a positive vertex count")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"splats": count, "ply_bytes": size, "ply_sha256": digest.hexdigest()}


def image_count(dataset):
    binary = dataset / "sparse/0/images.bin"
    if binary.is_file():
        with binary.open("rb") as stream:
            header = stream.read(8)
        if len(header) != 8:
            raise ValueError("truncated images.bin count header")
        count = struct.unpack("<Q", header)[0]
    else:
        count = sum(p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                    for p in (dataset / "images").rglob("*"))
    if count <= 0:
        raise ValueError("dataset has no images")
    return count


def reconstruct(args):
    if not args.job_dir.is_dir() or not args.dataset.is_dir():
        raise ValueError("job and dataset must be directories")
    inferred_steps, ply = final_export(args.job_dir)
    steps = inferred_steps if args.steps is None else args.steps
    if steps <= 0:
        raise ValueError("steps must be positive")
    wall = ply.stat().st_mtime - args.dataset.stat().st_ctime
    if not math.isfinite(wall) or wall <= 0:
        raise ValueError("final PLY mtime must be later than dataset ctime")
    reconstructed = ["splats", "ply_bytes", "ply_sha256", "images", "wall_seconds", "seconds_per_1k_steps"]
    if args.steps is None:
        reconstructed.insert(0, "steps")
    return {
        "name": args.name or args.job_dir.resolve().name,
        "recipe": "splat-brush", "origin": "local", "trainer": args.trainer, "photos": "embedded",
        "rc": None, "peak_rss_mb": None,
        "steps": steps, "max_res": args.max_res, "seed": args.seed, "max_splats": args.max_splats,
        **ply_metrics(ply), "images": image_count(args.dataset),
        "wall_seconds": wall, "wall_seconds_estimate": True,
        "seconds_per_1k_steps": wall * 1000 / steps,
        "reconstructed": reconstructed, "schema_note": SCHEMA_NOTE,
    }


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def seed_value(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--max-res", type=positive, required=True)
    parser.add_argument("--seed", type=seed_value, required=True)
    parser.add_argument("--max-splats", type=positive, required=True)
    parser.add_argument("--steps", type=positive)
    parser.add_argument("--trainer", default="Brush 0.3.0 (Mac, Metal)")
    args = parser.parse_args(argv)
    try:
        metrics = reconstruct(args)
        args.out.write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"local_run_metrics: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
