#!/usr/bin/env python3
"""Build an owner-review report for cleaned scene candidates."""

import argparse
import json
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("floater_fraction", "fog_fraction", "translucent_fraction")


def percent(value):
    return "—" if value is None else f"{100 * value:.4f}%"


def row(label, result, flags="—"):
    metrics = result.get("metrics") or {}
    values = [
        label,
        f"{result.get('count'):,}" if result.get("count") is not None else "—",
        f"{result.get('size_bytes'):,}" if result.get("size_bytes") is not None else "—",
        *(percent(metrics.get(name)) for name in METRICS),
        str(metrics.get("nonfinite_count", "—")),
        flags,
        "PASS" if result.get("passed") else "FAIL",
    ]
    return "| " + " | ".join(values) + " |"


def promotion_command(root, candidate, scene):
    candidate = candidate if candidate.is_absolute() else root / candidate
    command = [
        "python3", "tools/promote_scene.py", str(candidate.relative_to(root)),
        "--stem", scene["stem"], "--title", scene["title"],
        "--blurb", scene["blurb"], "--place", scene.get("place", ""),
        "--captured", scene.get("captured", ""),
    ]
    if scene.get("up") is not None:
        command += ["--up", ",".join(str(value) for value in scene["up"])]
    command += ["--replace", "--dry-run"]
    return shlex.join(command)


def build_report(candidates, root=ROOT):
    candidates = candidates if candidates.is_absolute() else root / candidates
    catalog = {item["stem"]: item for item in json.loads((root / "scenes.json").read_text())}
    checks = {item["scene"]: item for item in json.loads((root / "checks.json").read_text())["results"]}
    lines = ["# Candidate deliveries", ""]
    for manifest_path in sorted(candidates.glob("*/candidate.json")):
        manifest = json.loads(manifest_path.read_text())
        stem = manifest["stem"]
        scene = catalog[stem]
        candidate = manifest_path.parent / f"{stem}.sog"
        lines += [
            f"## {scene['title']} (`{stem}`)", "",
            "| Version | Splats | Bytes | Floaters | Fog | Translucent | Nonfinite | Flags | Result |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|",
            row("Current", checks[stem]),
            row("Candidate", manifest["selected"]["result"], manifest["selected"]["flags"]),
            "", "Attempts:", "",
            "| Flags | Splats | Bytes | Floaters | Fog | Translucent | Result |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for attempt in manifest["attempts"]:
            result = attempt["result"]
            metrics = result["metrics"]
            lines.append("| " + " | ".join([
                attempt["flags"], f"{result['count']:,}", f"{result['size_bytes']:,}",
                percent(metrics["floater_fraction"]), percent(metrics["fog_fraction"]),
                percent(metrics["translucent_fraction"]),
                "PASS" if result["passed"] else "FAIL",
            ]) + " |")
        lines += ["", "Promotion preview:", "", f"```sh\n{promotion_command(root, candidate, scene)}\n```", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(args.candidates) + "\n")


if __name__ == "__main__":
    main()
