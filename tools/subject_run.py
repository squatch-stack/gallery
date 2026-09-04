#!/usr/bin/env python3
"""Run the repeatable photo-subject-to-gallery preparation pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SIZE_LIMIT = 256 * 1024 * 1024


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(path: Path) -> dict[str, object]:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hasher.hexdigest()}


def files(paths: Iterable[Path]) -> list[Path]:
    found: set[Path] = set()
    for path in paths:
        if path.is_file():
            found.add(path)
        elif path.is_dir():
            found.update(item for item in path.rglob("*") if item.is_file())
    return sorted(found, key=str)


def snapshots(paths: Iterable[Path]) -> list[dict[str, object]]:
    return [digest(path) for path in files(paths)]


def mask_summary(subject: Path, min_coverage: float) -> dict[str, object]:
    from PIL import Image

    coverages = []
    dropped = []
    for path in sorted((subject / "masks").glob("*.png")):
        image = Image.open(path).convert("L")
        histogram = image.histogram()
        total = image.width * image.height
        coverage = 1.0 - histogram[0] / total
        coverages.append(coverage)
        if coverage >= 0.995 or coverage < min_coverage:
            dropped.append({"name": path.name, "coverage": coverage})
    return {
        "count": len(coverages),
        "mean_coverage": sum(coverages) / len(coverages) if coverages else None,
        "dropped_views": dropped,
    }


def display(argv: list[str], cwd: Path | None = None) -> str:
    command = shlex.join(argv)
    return f"env -C {shlex.quote(str(cwd))} {command}" if cwd else command


class Runner:
    def __init__(self, job: Path, dry_run: bool):
        self.job = job
        self.dry_run = dry_run
        self.record = {"schema_version": 1, "started_at": timestamp(), "steps": []}

    def save(self) -> None:
        if self.dry_run:
            return
        self.job.mkdir(parents=True, exist_ok=True)
        target = self.job / "run.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.record, indent=2) + "\n")
        temporary.replace(target)

    def run(
        self,
        name: str,
        argv: list[str],
        consumed: Iterable[Path],
        produced: Iterable[Path],
        cwd: Path | None = None,
    ) -> None:
        if self.dry_run:
            print(f"{name}: {display(argv, cwd)}")
            return
        step = {
            "name": name,
            "argv": argv,
            "cwd": str(cwd) if cwd else None,
            "started_at": timestamp(),
            "consumed": snapshots(consumed),
        }
        self.record["steps"].append(step)
        self.save()
        print(f"{name}: {display(argv, cwd)}", flush=True)
        try:
            result = subprocess.run(argv, cwd=cwd, check=False)
            step["exit_status"] = result.returncode
        except OSError as exc:
            step["exit_status"] = 127
            step["error"] = str(exc)
        step["ended_at"] = timestamp()
        step["produced"] = snapshots(produced)
        self.save()
        if step["exit_status"] != 0:
            raise SystemExit(step["exit_status"])

    def gate(self, scene: Path, passed: bool) -> None:
        now = timestamp()
        self.record["steps"].append(
            {
                "name": "size_gate",
                "argv": [],
                "cwd": None,
                "started_at": now,
                "ended_at": now,
                "exit_status": 0 if passed else 2,
                "consumed": snapshots([scene]),
                "produced": [],
            }
        )
        self.save()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("subject_dir", type=Path)
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--stem", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--limit", type=int, help="keep at most N registered views (minimum 8)")
    ap.add_argument("--host-composite", action="store_true", help="ship names and masks for host alpha compositing")
    ap.add_argument("--max-side", type=int, default=1920)
    ap.add_argument("--crop-quantile", type=float, default=0.90)
    ap.add_argument("--min-coverage", type=float, default=0.03)
    ap.add_argument("--steps", type=int, default=30000)
    ap.add_argument("--max-splats", type=int, default=1500000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-masks", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--gpugate", default=os.path.expanduser("~/bin/gpugate-mac"))
    ap.add_argument("--tools-dir", type=Path, default=ROOT / "tools", help=argparse.SUPPRESS)
    ap.add_argument("--mask-tool", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--payload-tool", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--clean-tool", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--promote-tool", type=Path, help=argparse.SUPPRESS)
    return ap


def newest_result(job: Path) -> tuple[Path, Path] | None:
    metrics = sorted(job.glob("gpugate-*/metrics.json"), key=lambda path: path.stat().st_mtime)
    for metric in reversed(metrics):
        ply = metric.parent / "point_cloud.ply"
        if ply.is_file():
            return metric, ply
    return None


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 8:
        ap.error("--limit must be at least 8")
    subject = args.subject_dir.resolve()
    job = args.job_dir.resolve()
    tools = args.tools_dir.resolve()
    mask_tool = (args.mask_tool or tools / "make_subject_masks.py").resolve()
    payload_tool = (args.payload_tool or tools / "make_scene_payload.py").resolve()
    clean_tool = (args.clean_tool or tools / "clean_export.py").resolve()
    promote_tool = (args.promote_tool or tools / "promote_scene.py").resolve()
    scene = job / "scene.tar"
    clean_dir = job / "scenes"
    archive_dir = job / "archive"
    runner = Runner(job, args.dry_run)
    py = args.python

    mask_argv = [py, str(mask_tool), str(subject), "--prompt", args.prompt]
    payload_argv = [
        py, str(payload_tool), str(subject), str(scene),
        "--alpha-from-masks",
        "--min-coverage", str(args.min_coverage),
    ]
    if args.host_composite:
        payload_argv += ["--masks"]
    else:
        payload_argv += ["--embed-images", "--max-side", str(args.max_side)]
    if args.limit is not None:
        payload_argv += ["--limit", str(args.limit)]
    runner.record.update(supervision="alpha", provenance_python=py, host_composite=args.host_composite,
                         limit=args.limit)
    submit_argv = [
        args.gpugate, "run", "splat-brush", "scene.tar", f"--steps={args.steps}",
        f"--max_splats={args.max_splats}", f"--max_res={args.max_side}", f"--seed={args.seed}",
    ]
    if args.host_composite:
        submit_argv.append("--alpha_from_masks=true")
    result = newest_result(job) if job.is_dir() else None
    metric_hint = result[0] if result else job / "gpugate-<id>" / "metrics.json"
    ply_hint = result[1] if result else job / "gpugate-<id>" / "point_cloud.ply"
    clean_argv = [
        py, str(clean_tool), str(ply_hint), "--stem", args.stem,
        "--out", str(clean_dir), "--archive", str(archive_dir),
        "--crop-quantile", str(args.crop_quantile), "--crop-margin", "1.1",
        "--alpha-min", "0.05",
    ]
    candidate = clean_dir / f"{args.stem}.sog"
    title = args.stem.replace("-", " ").replace("_", " ").title()
    promote_argv = [
        py, str(promote_tool), str(candidate), "--stem", args.stem,
        "--title", title, "--blurb", f"A scan of {title}.", "--replace", "--dry-run",
        "--supervision", "alpha", "--provenance-python", py,
        "--provenance-from", str(subject), "--trained", str(metric_hint),
    ]

    if args.dry_run:
        print("dry-run plan (no files will be written):")
        if not (subject / "masks").is_dir() and not args.skip_masks:
            print("masks: GPU step would run because masks/ is absent")
            runner.run("masks", mask_argv, [], [])
        elif not result:
            print("masks: skipped")
        if not result:
            runner.run("payload", payload_argv, [], [])
            print(f"size-gate: refuse scene.tar above {SIZE_LIMIT} bytes")
            runner.run("submit", submit_argv, [], [], cwd=job)
        runner.run("clean", clean_argv, [], [])
        runner.run("promote", promote_argv, [], [])
        return 0

    runner.save()
    if not result:
        if not (subject / "masks").is_dir():
            if args.skip_masks:
                print("masks: skipped by --skip-masks")
            else:
                print("masks/ is absent; starting GPU mask generation", flush=True)
                runner.run("masks", mask_argv, [subject / "images"], [subject / "masks"])
        else:
            print("masks: existing directory found; skipping GPU step")
        if not (subject / "masks").is_dir():
            raise SystemExit("masks/ is required for the alpha payload")
        runner.record["masks"] = mask_summary(subject, args.min_coverage)
        runner.save()
        runner.run(
            "payload", payload_argv,
            [subject / "images", subject / "masks", subject / "sparse", subject / "sparse-global"],
            [scene],
        )
        size = scene.stat().st_size
        runner.gate(scene, size <= SIZE_LIMIT)
        if size > SIZE_LIMIT:
            mib = size / (1024 * 1024)
            print(f"size gate: scene.tar is {mib:.1f} MiB; limit is 256 MiB; refusing submission")
            with tarfile.open(scene) as archive:
                member = next(m for m in archive.getmembers() if Path(m.name).name == "names.txt" and m.isfile())
                with archive.extractfile(member) as stream:
                    views = sum(bool(line.strip()) for line in stream)
            suggested = (views * SIZE_LIMIT // size) // 4 * 4
            runner.record["payload_views"] = views
            runner.record["suggested_limit"] = suggested
            if suggested >= 8:
                print(f"alternative: use fewer views via --limit {suggested} (estimated from {views} shipped views)")
            else:
                print(f"fewer views via --limit would require {suggested}; minimum is 8; reduce --max-side "
                      "or use --host-composite")
            print(
                "alternative: names+masks payload for host-side compositing: "
                "rerun subject_run.py with --host-composite (--alpha_from_masks=true at submission)"
            )
            runner.record["outcome"] = "size_gate_refused"
            runner.record["ended_at"] = timestamp()
            runner.save()
            return 2
        if not args.submit:
            print("submission not requested; run this command when ready:")
            print(display(submit_argv, job))
            runner.record["outcome"] = "awaiting_submission"
            runner.record["ended_at"] = timestamp()
            runner.save()
            return 0
        runner.run("submit", submit_argv, [scene], [], cwd=job)
        result = newest_result(job)
        if result is None:
            raise SystemExit("submission succeeded but no gpugate-*/metrics.json and point_cloud.ply appeared")
        metric_hint, ply_hint = result
        broker_outputs = [metric_hint, ply_hint, metric_hint.parent / "train.log"]
        runner.record["steps"][-1]["produced"] = snapshots(broker_outputs)
        runner.save()
        clean_argv[2] = str(ply_hint)
        promote_argv[-1] = str(metric_hint)

    runner.run(
        "clean", clean_argv, [ply_hint],
        [clean_dir / f"{args.stem}.sog", clean_dir / f"{args.stem}.spz", archive_dir / f"{args.stem}.ply"],
    )
    runner.run("promote", promote_argv, [candidate, metric_hint, subject], [])
    runner.record["outcome"] = "complete"
    runner.record["ended_at"] = timestamp()
    runner.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
