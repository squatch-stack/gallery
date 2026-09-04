#!/usr/bin/env python3
"""Summarize the local gallery; exit 1 when scene files need refreshed checks."""

import argparse
import gzip
import json
import re
import struct
import time
from html import unescape
from pathlib import Path

from promote_scene import archives_for, glb_summary, scene_files, sog_count

ROOT = Path(__file__).resolve().parents[1]


def file_count(path):
    """Read counts from delivered headers without decoding geometry."""
    try:
        if path.suffix == ".sog":
            return sog_count(path)
        if path.suffix == ".spz":
            with path.open("rb") as stream:
                compressed = stream.read(2) == b"\x1f\x8b"
            with gzip.open(path, "rb") if compressed else path.open("rb") as stream:
                magic, _version, count = struct.unpack("<III", stream.read(12))
            return count if magic == 0x5053474E else None
        if path.suffix == ".ply":
            with path.open("rb") as stream:
                for _ in range(10000):
                    line = stream.readline()
                    if not line or line.strip() == b"end_header":
                        break
                    match = re.fullmatch(rb"element vertex (\d+)\s*", line)
                    if match:
                        return int(match[1])
        if path.is_file() and path.suffix in {".glb", ".obj", ".fbx"}:
            return 0
    except (OSError, ValueError, EOFError, struct.error):
        pass
    return None


def recorded_metric(result, keys, name, unit):
    for key in keys:
        value = result.get(key)
        if type(value) is int and value >= 0:
            return value
    for check in result.get("checks", []):
        if check.get("name") == name:
            match = re.match(rf"(\d+) {unit}\b", check.get("detail", ""))
            if match:
                return int(match[1])
    return None


def freshness(result, path, checked_at):
    if result is None:
        return True, "missing"
    size = recorded_metric(result, ("size_bytes", "bytes"), "size", "bytes")
    count = recorded_metric(result, ("count",), "count", "splats")
    if size is not None or count is not None:
        stale = not path.is_file() or (
            size is not None and path.stat().st_size != size
        ) or (count is not None and file_count(path) != count)
        return stale, "content"
    return path.is_file() and checked_at is not None and path.stat().st_mtime > checked_at, "mtime"


def generation_date(path):
    text = unescape(re.sub(r"<[^>]+>", " ", path.read_text()))
    match = re.search(
        r"generated\s*:?\s*(\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})|\s+\d{2}:\d{2}\s+UTC)?)",
        text, re.IGNORECASE,
    )
    return match[1] if match else None


def gallery_status(root=ROOT, stale_days=None, now=None):
    snapshot = root / "checks.json"
    checks = json.loads(snapshot.read_text()) if snapshot.is_file() else {}
    results = {r["scene"]: r for r in checks.get("results", []) if r.get("platform") == "web-mobile"}
    checked_at = snapshot.stat().st_mtime if snapshot.is_file() else None
    now = time.time() if now is None else now
    aged = checked_at is not None and stale_days is not None and now - checked_at > stale_days * 86400
    rows = []
    for entry in json.loads((root / "scenes.json").read_text()):
        stem = entry["stem"]
        files = scene_files(root, stem)
        primary = root / entry["mesh"] if entry.get("mesh") else next(
            (p for p in files if p.suffix in {".sog", ".spz", ".ply"}), root / "scenes" / f"{stem}.sog"
        )
        if primary.is_file() and primary not in files:
            files.append(primary)
        result = results.get(stem)
        checked_file = root / result["file"] if result and result.get("file") else primary
        stale, rule = freshness(result, checked_file, checked_at)
        page = root / entry["provenance"] if entry.get("provenance") else None
        present = bool(page and page.is_file())
        count = entry.get("splats")
        if primary.is_file():
            count = file_count(primary)
        triangles = entry.get("triangles")
        if entry.get("mesh") and primary.is_file() and primary.suffix.lower() == ".glb":
            try:
                triangles = glb_summary(primary)[0]
            except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
                triangles = None
        passed = result.get("passed") if result else None
        rows.append({
            "stem": stem, "title": entry.get("title", ""), "kind": "mesh" if entry.get("mesh") else "splat",
            "splats": count, "triangles": triangles, "size_bytes": sum(p.stat().st_size for p in files),
            "file": str(primary.relative_to(root)), "file_present": primary.is_file(),
            "web_mobile": "PASS" if passed is True else "FAIL" if passed is False else "UNKNOWN",
            "stale": stale, "checks_rule": rule, "checks_aged": aged,
            "provenance_present": present, "provenance_generated": generation_date(page) if present else None,
            "captured": entry.get("captured") or None, "archive_count": len(archives_for(root, stem)),
        })
    return rows


def table(rows):
    lines = [
        "| Stem | Title | Kind | Splats / Triangles | Bytes on disk | Web-mobile | Checks | Provenance | "
        "Generated | Captured | Archives |",
        "|---|---|---|---:|---:|---|---|---|---|---|---:|",
    ]
    for row in rows:
        freshness = "STALE" if row["stale"] else "CURRENT"
        if row["checks_rule"] == "mtime":
            freshness += "; mtime"
        if row["checks_aged"]:
            freshness += "; aged"
        if not row["file_present"]:
            freshness += "; file missing"
        count = row["triangles"] if row["kind"] == "mesh" else row["splats"]
        values = [row["stem"], row["title"], row["kind"], count, row["size_bytes"], row["web_mobile"],
                  freshness, "yes" if row["provenance_present"] else "no", row["provenance_generated"],
                  row["captured"], row["archive_count"]]
        cells = [str(v).replace("|", "\\|").replace("\n", " ") if v is not None else "—" for v in values]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--stale-days", type=int,
                        help="also flag checks older than N days; does not alter the file-staleness exit status")
    args = parser.parse_args(argv)
    if args.stale_days is not None and args.stale_days < 0:
        parser.error("--stale-days must be nonnegative")
    rows = gallery_status(root, args.stale_days)
    print(json.dumps(rows, indent=2) if args.json else table(rows))
    return int(any(row["stale"] and row["checks_rule"] != "mtime" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
