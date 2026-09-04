#!/usr/bin/env python3
"""Promote a candidate SOG or GLB into the gallery catalog and validate it."""

import argparse
import datetime as dt
import difflib
import html
import json
import math
import re
import shutil
import subprocess
import sys
import struct
import zipfile
from pathlib import Path

try:
    from tools import scene_up
    from tools.check_deliverable import glb_summary
except ModuleNotFoundError:
    import scene_up
    from check_deliverable import glb_summary

ROOT = Path(__file__).resolve().parents[1]


def sog_count(path):
    try:
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("meta.json"))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read SOG meta.json: {exc}") from exc
    count = meta.get("count")
    if type(count) is not int or count <= 0:
        raise ValueError("SOG meta.json must contain a positive integer count")
    return count


def catalog_text(entries):
    return json.dumps(entries, indent=2) + "\n"


def parse_up(value):
    try:
        up = [float(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("up must be x,y,z") from exc
    if len(up) != 3 or not all(math.isfinite(v) for v in up) or not any(up):
        raise argparse.ArgumentTypeError("up must be a finite, nonzero x,y,z vector")
    return up


def choose_up(args, parser):
    """Resolve splat gravity before any promotion writes or archival moves."""
    if args.up is not None:
        print(f"up (explicit): {json.dumps(args.up)}")
        return args.up, {"up_source": "explicit", "up": args.up}
    try:
        estimate = scene_up.cloud_estimate(args.candidate)
        extent = estimate["extent"]
        reason = estimate["reason"]
        if estimate["up"] is None:
            reason = reason or "cloud estimate returned no up vector"
        elif estimate["inliers"] < args.up_min_inliers:
            reason = f"inliers {estimate['inliers']:.6f} below --up-min-inliers {args.up_min_inliers:g}"
        elif not extent or min(extent) <= 0 or min(extent) < 0.25 * max(extent):
            reason = "plane footprint smaller extent is less than one quarter of the larger"
        if reason is None:
            confidence = {"inliers": estimate["inliers"], "extent": extent,
                          "footprint_ratio": min(extent) / max(extent),
                          "min_inliers": args.up_min_inliers}
            print(f"up (cloud): {json.dumps(estimate['up'])}; confidence: {json.dumps(confidence)}")
            return estimate["up"], {"up_source": "cloud", "up": estimate["up"],
                                    "up_confidence": confidence}
    except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError, ImportError, zipfile.BadZipFile) as exc:
        reason = f"cloud estimate failed: {exc}"
    message = f"cannot choose splat up: {reason}."
    if args.provenance_from:
        try:
            candidates = scene_up.camera_candidates(args.provenance_from)
            message += "\nCamera-axis candidates (catalog/viewer frame):"
            for axis, vector in candidates.items():
                if vector is None:
                    message += f"\n  {axis}: unavailable (camera axes cancel; inspect and supply --up manually)."
                    continue
                option = ",".join(str(v) for v in vector)
                message += f"\n  {axis}: {json.dumps(vector)}; stage with --up={option} and look."
            message += (f"\nAfter staging each candidate with --up, inspect "
                        f"viewer.html?scene={args.stem}&az=30&el=10&d=1.6. "
                        "This URL cannot inspect an unpromoted candidate.")
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            message += f"\nCamera-axis candidates unavailable: {exc}."
    parser.error(message + "\nPromotion refused; pass --up=x,y,z after inspection to proceed.")


def record_up(root, entry, inputs):
    """Augment generated/preserved provenance, or create an up-only sidecar."""
    page = provenance_path(root, entry) or root / "provenance" / f"{entry['stem']}.html"
    path = page.with_suffix(".json")
    data = json.loads(path.read_text()) if path.is_file() else {
        "schema_version": 1, "mode": "promotion", "argv": [],
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "inputs": {},
    }
    data.setdefault("inputs", {}).pop("up_confidence", None)
    data["inputs"].update(inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def scene_files(root, stem):
    return [p for ext in (".sog", ".spz", ".ply", ".glb", ".obj", ".fbx")
            if (p := root / "scenes" / f"{stem}{ext}").is_file()]


def archives_for(root, stem):
    base = root / "archive/replaced"
    return sorted((p for p in base.glob(f"{stem}-*")
                   if p.is_dir() and any((p / f"{stem}{ext}").is_file()
                                        for ext in (".sog", ".spz", ".ply", ".glb", ".obj", ".fbx"))),
                  key=lambda p: (p.stat().st_mtime_ns, p.name))


def new_archive(root, stem):
    today = dt.datetime.now().astimezone().date().isoformat()
    base = root / "archive/replaced" / f"{stem}-{today}"
    path, index = base, 1
    while path.exists():
        path = base.with_name(f"{base.name}-{index:04d}")
        index += 1
    return path


def provenance_path(root, entry):
    if not entry or not entry.get("provenance"):
        return None
    path = root / entry["provenance"]
    if not path.resolve().is_relative_to((root / "provenance").resolve()):
        raise ValueError("provenance must be inside provenance/")
    return path


def provenance_files(root, entry, stem=None):
    """Return an existing provenance page and its generator sidecar."""
    page = provenance_path(root, entry)
    if not page:
        sidecar = root / "provenance" / f"{stem}.json"
        return [sidecar] if stem and sidecar.is_file() else []
    return [path for path in (page, page.with_suffix(".json")) if path.is_file()]


def archive_current(root, stem, entry, archive, dry_run=False):
    files = [(p, archive / p.name) for p in scene_files(root, stem)]
    for page in provenance_files(root, entry, stem):
        files.append((page, archive / page.relative_to(root)))
    for source, target in files:
        print(f"move {source.relative_to(root)} -> {target.relative_to(root)}")
    print(f"save catalog entry -> {archive.relative_to(root)}/entry.json")
    if dry_run:
        return
    archive.mkdir(parents=True)
    (archive / "entry.json").write_text(json.dumps(entry, indent=2) + "\n")
    for source, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, target)


def check_deliverable(root, stem):
    verdict = subprocess.run(
        [sys.executable, str(root / "tools/check_deliverable.py"), stem], check=False
    )
    print(f"check_deliverable verdict: {'PASS' if verdict.returncode == 0 else 'FAIL'}")
    return verdict.returncode


def revert(args, root, catalog_path, entries, parser):
    stem = args.revert
    archives = archives_for(root, stem)
    source = args.from_archive
    if source is not None:
        source = (root / source).resolve()
        if source not in [p.resolve() for p in archives]:
            parser.error("--from must name an archive containing files for this stem under archive/replaced")
    elif archives:
        source = archives[-1]
    else:
        parser.error(f"no archive exists for {stem!r}")
    existing = next((e for e in entries if e.get("stem") == stem), None)
    entry_path = source / "entry.json"
    restored = json.loads(entry_path.read_text()) if entry_path.is_file() else existing
    if not entry_path.is_file():
        print("Archive has no entry.json; keeping the current catalog entry.")
    if restored is not None and (not isinstance(restored, dict) or restored.get("stem") != stem):
        parser.error("archived entry.json does not match the requested stem")
    # Validate all destinations before moving anything.
    page = provenance_path(root, restored)
    files = [(p, root / "scenes" / p.name) for p in source.iterdir()
             if p.is_file() and p.name in {f"{stem}{ext}" for ext in
                                          (".sog", ".spz", ".ply", ".glb", ".obj", ".fbx")}]
    if page:
        for target in (page, page.with_suffix(".json")):
            archived = source / target.relative_to(root)
            if archived.is_file():
                files.append((archived, target))
    else:
        target = root / "provenance" / f"{stem}.json"
        archived = source / target.relative_to(root)
        if archived.is_file():
            files.append((archived, target))
    archive = new_archive(root, stem)
    archive_current(root, stem, existing, archive, dry_run=args.dry_run)
    for old, target in files:
        print(f"move {old.relative_to(root)} -> {target.relative_to(root)}")
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old, target)
            target.touch()  # The saved checks describe the version we just displaced.
    if not entry_path.is_file() and page and (archive / page.relative_to(root)).is_file():
        print("Legacy archive has no provenance backup; keeping the current provenance page.")
        if not args.dry_run and not page.exists():
            shutil.copy2(archive / page.relative_to(root), page)
    index = entries.index(existing) if existing is not None else len(entries)
    if existing is not None:
        entries.remove(existing)
    if restored is not None:
        entries.insert(index, restored)
    print("restore catalog entry; rerun check_deliverable (web-mobile)")
    if args.dry_run:
        return 0
    catalog_path.write_text(catalog_text(entries))
    if entry_path.exists():
        entry_path.unlink()
    # Leave any unrelated legacy archive contents alone.
    for directory in sorted((p for p in source.rglob("*") if p.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    if not any(source.iterdir()):
        source.rmdir()
    return check_deliverable(root, stem)


def app_export_inputs(page):
    """Load operator-authored app-export inputs, preferring the JSON sidecar."""
    sidecar = page.with_suffix(".json")
    if sidecar.is_file():
        data = json.loads(sidecar.read_text())
        if data.get("mode") != "app-export" or not isinstance(data.get("inputs"), dict):
            raise ValueError(f"invalid app-export provenance sidecar: {sidecar}")
        inputs = data["inputs"]
        source = inputs.get("source")
        commits = inputs.get("source_commit", [])
        notes = inputs.get("note", [])
        if not source or not commits:
            raise ValueError(f"incomplete app-export provenance sidecar: {sidecar}")
        return source, commits, notes

    text = page.read_text()
    source_match = re.search(
        r"<b>Operator source summary:</b>\s*(.*?)</p>", text, flags=re.DOTALL
    )
    commits = re.findall(r"<b>Source commit:</b>\s*([^<]+)</p>", text)
    note_section = re.search(
        r"<h2>Evidence limits</h2>(.*?)(?:<h2>|</main>)", text, flags=re.DOTALL
    )
    notes = re.findall(r"<p>(.*?)</p>", note_section.group(1), flags=re.DOTALL) if note_section else []
    if not source_match or not commits:
        raise ValueError(
            f"cannot recover app-export inputs from {page}; regenerate it with provenance.py"
        )
    def clean(value):
        return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()

    return clean(source_match.group(1)), [clean(item) for item in commits], [clean(item) for item in notes]


def cleaning_from_candidate(candidate):
    """Find cleaning flags in candidate-local attempt/check records."""
    for name in ("attempt.json", "checks.json"):
        path = candidate.parent / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        pending = [data]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"cleaning", "cleaning_flags", "clean_export_flags", "flags"}:
                        flags = child if isinstance(child, list) else [child]
                        if flags and all(isinstance(flag, str) and flag.strip() for flag in flags):
                            return flags, path
                    pending.append(child)
            elif isinstance(value, list):
                pending.extend(value)
    return None, None


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--stem")
    parser.add_argument("--title")
    parser.add_argument("--blurb")
    parser.add_argument("--up", type=parse_up)
    parser.add_argument("--up-min-inliers", type=float, default=0.35,
                        help="minimum cloud ground-plane inlier fraction (default: 0.35)")
    parser.add_argument("--place", default="")
    parser.add_argument("--captured", default="")
    parser.add_argument("--provenance-from", type=Path)
    parser.add_argument("--trained", type=Path)
    parser.add_argument("--cleaning", action="append", default=[],
                        help="clean_export flags, or mesher and detail level "
                             "(e.g. posekit --model full --masks); repeatable")
    parser.add_argument("--source", help="app-export operator source summary; required for new mesh provenance")
    parser.add_argument("--source-commit", action="append", default=[], help="source history to quote; repeatable")
    parser.add_argument("--supervision", choices=["masks", "alpha"], default=None,
                       help="passed to provenance.py with --masked: how the masks reached the trainer")
    parser.add_argument("--provenance-python", default=sys.executable,
                       help="interpreter for provenance.py (it needs pycolmap)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replace", action="store_true")
    mode.add_argument("--revert", metavar="STEM")
    parser.add_argument("--from", dest="from_archive", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--catalog", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 <= args.up_min_inliers <= 1:
        parser.error("--up-min-inliers must be finite and in [0, 1]")
    root = root.resolve()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    stem = args.revert if args.revert is not None else args.stem
    if not stem or any(c not in allowed for c in stem):
        parser.error("stem may contain only letters, numbers, hyphens, and underscores")
    catalog_path = args.catalog or root / "scenes.json"
    old_text = catalog_path.read_text()
    entries = json.loads(old_text)
    if args.revert is not None:
        if (args.candidate or args.stem or args.title or args.blurb or args.provenance_from
                or args.trained or args.cleaning or args.source or args.source_commit):
            parser.error("--revert cannot be combined with promotion arguments")
        return revert(args, root, catalog_path, entries, parser)
    if args.from_archive:
        parser.error("--from requires --revert")
    if args.candidate is None or args.title is None or args.blurb is None:
        parser.error("promotion requires candidate, --stem, --title, and --blurb")
    if args.candidate.suffix.lower() not in {".sog", ".glb"} or not args.candidate.is_file():
        parser.error("candidate must be an existing .sog or .glb file")
    mesh = args.candidate.suffix.lower() == ".glb"
    if mesh and args.provenance_from:
        parser.error("mesh promotion uses app-export provenance; use --source and --source-commit")
    if args.source and (not args.source.strip() or args.provenance_from):
        parser.error("--source must be nonblank and cannot be combined with --provenance-from")
    if bool(args.source) != bool(args.source_commit):
        parser.error("--source and --source-commit must be used together")
    if bool(args.provenance_from) != bool(args.trained):
        parser.error("--provenance-from and --trained must be used together")
    existing = next((item for item in entries if item.get("stem") == args.stem), None)
    destination = root / "scenes" / f"{args.stem}{args.candidate.suffix.lower()}"
    if (existing or scene_files(root, args.stem)) and not args.replace:
        parser.error(f"scene {args.stem!r} already exists; pass --replace to replace it")

    app_inputs = None
    cleaning = args.cleaning
    cleaning_record = None
    old_page = provenance_path(root, existing)
    sidecar = old_page.with_suffix(".json") if old_page else None
    sidecar_mode = None
    if sidecar and sidecar.is_file():
        try:
            sidecar_mode = json.loads(sidecar.read_text()).get("mode")
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"invalid provenance sidecar {sidecar}: {exc}")
    legacy_app_page = bool(
        old_page and old_page.is_file() and "Operator source summary:" in old_page.read_text()
    )
    is_app_export = sidecar_mode == "app-export" or (sidecar_mode is None and legacy_app_page)
    if existing and old_page and not args.provenance_from and is_app_export and not args.source:
        if not old_page.is_file():
            parser.error(f"catalog provenance page does not exist: {old_page.relative_to(root)}")
        candidate_cleaning, cleaning_record = cleaning_from_candidate(args.candidate)
        if candidate_cleaning and not cleaning:
            cleaning = candidate_cleaning
        if not sidecar.is_file() and not cleaning:
            parser.error(
                "app-export promotion requires its provenance sidecar or --cleaning \"<flags>\"; "
                "never omit how the candidate was cleaned"
            )
        try:
            app_inputs = app_export_inputs(old_page)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        if not cleaning:
            parser.error(
                "app-export promotion has no candidate cleaning record; pass --cleaning \"<flags>\""
            )

    if args.source:
        app_inputs = (args.source, args.source_commit, [])
    if mesh:
        if not cleaning:
            cleaning, cleaning_record = cleaning_from_candidate(args.candidate)
        # Require an identified tool plus a model/detail/quality setting, not a generic mesh note.
        description = " ".join(cleaning or []).strip()
        if not re.search(r"\b[\w.-]+\s+.*(?:--(?:model|detail|quality|resolution|preset)(?:\s+|=)\S+|"
                         r"(?:detail|quality|resolution|model)\s*[:=]\s*\S+)", description, re.IGNORECASE):
            parser.error('mesh promotion requires --cleaning "<mesher> --model <detail>" or a candidate record')
        if not app_inputs:
            parser.error("mesh provenance requires --source and --source-commit or existing app-export provenance")
    try:
        triangles = glb_summary(args.candidate)[0] if mesh else None
        count = 0 if mesh else sog_count(args.candidate)
    except (ValueError, OSError, KeyError, IndexError, TypeError, struct.error) as exc:
        parser.error(f"invalid candidate: {exc}")

    if args.candidate.resolve() in [p.resolve() for p in scene_files(root, args.stem)]:
        parser.error("candidate must be outside the current scene files")
    up_inputs = None
    if not mesh:
        args.up, up_inputs = choose_up(args, parser)
    entry = dict(existing or {})
    entry.pop("mesh", None)
    entry.pop("triangles", None)
    entry.update(stem=args.stem, title=args.title, place=args.place,
                 captured=args.captured, splats=count, blurb=args.blurb)
    if mesh:
        entry.update(mesh=str(destination.relative_to(root)), triangles=triangles)
    if args.up is not None:
        entry["up"] = args.up
    if args.provenance_from or app_inputs:
        entry["provenance"] = f"provenance/{args.stem}.html"
    if existing:
        entries[entries.index(existing)] = entry
    else:
        entries.append(entry)
    new_text = catalog_text(entries)
    if args.replace:
        archive = new_archive(root, args.stem)
        provenance_path(root, existing)
    if args.dry_run:
        if args.replace:
            archive_current(root, args.stem, existing, archive, dry_run=True)
        print(f"copy {args.candidate.name} -> {destination.relative_to(root)}")
        print("".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True),
                                           fromfile=catalog_path.name, tofile=catalog_path.name)))
        if app_inputs:
            print("regenerate app-export provenance with candidate cleaning:", *cleaning)
        return 0

    if args.replace:
        archive_current(root, args.stem, existing, archive)
        # Keep the previous page only when no replacement provenance is being generated.
        page = provenance_path(root, existing)
        if page and not args.provenance_from and not app_inputs and (archive / page.relative_to(root)).is_file():
            shutil.copy2(archive / page.relative_to(root), page)
        if page and not args.provenance_from and not app_inputs:
            old_sidecar = archive / page.with_suffix(".json").relative_to(root)
            if old_sidecar.is_file():
                shutil.copy2(old_sidecar, page.with_suffix(".json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.candidate, destination)
    destination.touch()
    sibling = args.candidate.with_suffix(".spz")
    if not mesh and sibling.is_file():
        shutil.copy2(sibling, destination.with_suffix(".spz"))
        destination.with_suffix(".spz").touch()
    catalog_path.write_text(new_text)

    if args.provenance_from:
        command = [args.provenance_python, str(root / "tools/provenance.py"), str(args.provenance_from),
                   "--title", args.title, "--place", args.place, "--trained", str(args.trained),
                   "--export", str(destination), "--out", str(root / entry["provenance"])]
        if args.supervision:
            command += ["--masked", "--supervision", args.supervision]
        subprocess.run(command, check=True)
    elif app_inputs:
        source, commits, notes = app_inputs
        command = [args.provenance_python, str(root / "tools/provenance.py"), args.stem,
                   "--app-export", "--source", source, "--export", str(destination),
                   "--out", str(root / entry["provenance"])]
        for commit in commits:
            command += ["--source-commit", commit]
        for flags in cleaning:
            command += ["--cleaning", flags]
        command += ["--cleaning-source", "candidate"]
        for note in notes:
            command += ["--note", note]
        if cleaning_record:
            print(f"using cleaning flags from {cleaning_record}")
        subprocess.run(command, check=True)
    if up_inputs:
        record_up(root, entry, up_inputs)
    return check_deliverable(root, args.stem)


if __name__ == "__main__":
    raise SystemExit(main())
