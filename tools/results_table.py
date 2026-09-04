#!/usr/bin/env python3
"""Generate the results page and SVG from sanitized job records (standard library only)."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"(?m)^Generated at: .* UTC$")
ARMS = ("unmasked", "masks-folder", "alpha-matched")


def megapixels(job):
    return job["max_res"] * (job["max_res"] * 3 / 4) / 1_000_000


def load_runs(index_path, jobs_dir):
    index = json.loads(index_path.read_text())
    rows = []
    for job_id, run in sorted(index["runs"].items()):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", job_id):
            raise ValueError("invalid job id")
        job = json.loads((jobs_dir / f"{job_id}.json").read_text())
        for key in ("max_res", "steps", "images", "seconds_per_1k_steps", "wall_seconds", "splats", "peak_rss_mb"):
            value = job[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {key}")
        if job["rc"] != 0 or job["seed"] != index["seed"] or job["max_splats"] != index["cap"]:
            raise ValueError("failed job or inconsistent training settings")
        rows.append((job_id, run, job))
    return index, rows


def chart(rows):
    """Plot all cannon 30k arms, with distinct markers and explicit units."""
    xmax = max(megapixels(j) for _, _, j in rows) * 1.15
    ymax = max(j["seconds_per_1k_steps"] for _, _, j in rows) * 1.2
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 460" role="img"',
        ' aria-labelledby="title desc">',
        '<title id="title">Cannon 30k training cost by resolution</title>',
        '<desc id="desc">Measured seconds per 1k steps versus megapixels; short probes excluded.</desc>',
        '<rect width="800" height="460" fill="#fff"/>',
        '<g font-family="sans-serif" font-size="13" fill="#222">',
    ]
    for tick in range(6):
        x, y = 85 + tick * 130, 345 - tick * 60
        parts.extend(
            [
                f'<path d="M{x} 45V345 M85 {y}H735" stroke="#ddd" fill="none"/>',
                f'<text x="{x}" y="367" text-anchor="middle">{xmax * tick / 5:.1f}</text>',
                f'<text x="75" y="{y + 4}" text-anchor="end">{ymax * tick / 5:.1f}</text>',
            ]
        )
    parts.extend(
        [
            '<path d="M85 45V345H735" stroke="#222" fill="none"/>',
            '<text x="410" y="395" text-anchor="middle">Frame size (megapixels, 4:3)</text>',
            '<text transform="translate(22 195) rotate(-90)" text-anchor="middle">Seconds per 1k steps (s)</text>',
        ]
    )
    for _, run, job in rows:
        x = 85 + megapixels(job) / xmax * 650
        y = 345 - job["seconds_per_1k_steps"] / ymax * 300
        arm = run["arm"]
        if arm == "unmasked":
            marker = f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="#222"/>'
            dy = -14
        elif arm == "masks-folder":
            marker = f'<rect x="{x - 5:.2f}" y="{y - 5:.2f}" width="10" height="10" fill="#777"/>'
            dy = 12
        else:
            marker = f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="none" stroke="#222"/>'
            dy = 30
        parts.extend([marker, f'<text x="{x + 12:.2f}" y="{y + dy:.2f}">{job["max_res"]} ({arm})</text>'])
    parts.extend(
        [
            '<text x="85" y="433">● unmasked   ■ masks-folder   ○ alpha-matched; measured 30k runs only</text>',
            "</g></svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def cell(value):
    """Keep recorded text inside a Markdown table cell."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace("|", "&#124;").replace("\n", " ")


def isolation_section(path):
    data = json.loads(path.read_text())
    lines = [
        "## Isolation",
        "",
        "Cannon training arms; alpha-weighted coordinate medians define the centre and the weighted median "
        "L-infinity distance defines MAD; fractions count finite splats equally, with inclusive radius bounds.",
        "",
        "| Arm | Within 3x MAD | Longest axis > 0.25 world units | Longest axis > 1.0 world units |",
        "|---|---:|---:|---:|",
    ]
    for arm in ARMS:
        stats = data["arms"][arm]
        values = (
            [stats["within_mad"]["3"], stats["long_axis_fraction"]["0.25"], stats["long_axis_fraction"]["1.0"]]
            if stats
            else [None] * 3
        )
        formatted = ["unavailable" if v is None else f"{100 * v:.2f}%" for v in values]
        lines.append(f"| {arm} | " + " | ".join(formatted) + " |")
    lines.extend(
        [
            "",
            "Higher within-3x-MAD fractions indicate tighter subject isolation "
            "(the complement of the checker's floater fraction); "
            "unavailable means no cloud measurement is recorded.",
            "",
        ]
    )
    return lines


def cleaning_section(root):
    catalog = json.loads((root / "scenes.json").read_text())
    checks = json.loads((root / "checks.json").read_text())["results"]
    lines = [
        "## Cleaning",
        "",
        "Recorded delivery outcomes from checks.json; subject profiles come from scenes.json "
        "(default object, as in the checker), and flags come from provenance sidecars without inferred defaults.",
        "",
        "| Scene | Subject profile | Splats | Bytes | Verdict (platform) | Cleaning flags | Cleaning source |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for scene in catalog:
        stem = scene["stem"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", stem):
            raise ValueError("invalid scene stem")
        sidecar = root / "provenance" / f"{stem}.json"
        inputs = json.loads(sidecar.read_text()).get("inputs", {}) if sidecar.is_file() else {}
        flags = inputs.get("cleaning") or []
        flags = flags if isinstance(flags, str) else "; ".join(flags)
        matches = [r for r in checks if r["scene"] == stem] or [{}]
        for record in matches:
            count, size = record.get("count"), record.get("size_bytes")
            verdict = {True: "PASS", False: "FAIL", None: "unavailable"}[record.get("passed")]
            values = [
                stem,
                scene.get("subject", "object"),
                f"{count:,}" if count is not None else "unavailable",
                f"{size:,}" if size is not None else "unavailable",
                f"{verdict} ({record.get('platform', 'unavailable')})",
                flags or "unavailable",
                inputs.get("cleaning_source") or "unavailable",
            ]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    lines.extend(
        [
            "",
            "Verdicts are recorded checker results, not a visual-quality rating; "
            "places retain surroundings, and missing flags do not mean no cleaning occurred.",
            "",
        ]
    )
    return lines


def generate(index_path, jobs_dir, image_src):
    index, rows = load_runs(index_path, jobs_dir)
    full = [
        (i, r, j)
        for i, r, j in rows
        if r["subject"] == "cannon" and r["arm"] in ARMS and j["steps"] == 30000 and not r.get("probe")
    ]
    sweep = sorted((row for row in full if row[1]["arm"] == "unmasked"), key=lambda row: row[2]["max_res"])
    baseline = next(j["splats"] for _, _, j in sweep if j["max_res"] == 1920)
    lines = [
        "# Training results",
        "",
        f"{index['trainer']}; seed {index['seed']}; splat cap {index['cap']:,}.",
        "",
        "Frame pixels = max_res * (max_res * 3 / 4), assuming 4:3 frames; megapixels = pixels / 1,000,000.",
        "",
        "## Resolution sweep",
        "",
        "Cannon, unmasked, 30,000 steps. All table values are measured or calculated from records.",
        "",
        "| Job | max_res (px) | Images | Megapixels | s / 1k steps | s / 1k / MP | Wall (s) | Splats | Peak RSS (MB) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for job_id, _, j in sweep:
        mp = megapixels(j)
        lines.append(
            f"| {job_id} | {j['max_res']} | {j['images']} | {mp:.4f} | "
            f"{j['seconds_per_1k_steps']:.2f} | {j['seconds_per_1k_steps'] / mp:.2f} | "
            f"{j['wall_seconds']:.1f} | {j['splats']:,} | {j['peak_rss_mb']:.1f} |"
        )
    high = [j for _, _, j in sweep if j["max_res"] >= 2856]
    costs = [j["seconds_per_1k_steps"] / megapixels(j) for j in high]
    flat = len(costs) >= 2 and max(costs) / min(costs) <= 1.05
    binds = all(j["splats"] >= index["cap"] for _, _, j in sweep)
    reading = "Per-megapixel cost is approximately flat from 2856 px upward" if flat else "Per-megapixel cost varies"
    reading += "; the cap binds at every resolution." if binds else "; the cap does not bind at every resolution."
    lines.extend(["", reading, ""])
    if costs:
        native_mp = 5712 * 4284 / 1_000_000
        rate = sum(costs) / len(costs)
        lines.extend(
            [
                f"**Native-resolution extrapolation (not a measurement):** assuming 5712 * 4284 "
                f"({native_mp:.4f} MP), the mean measured cost at ≥2856 px ({rate:.4f} s / 1k / MP) "
                f"predicts {native_mp * rate:.2f} s / 1k steps, or {native_mp * rate * 30:.1f} s "
                f"({native_mp * rate / 2:.1f} min) for 30k steps.",
                "The target dimensions are an explicit assumption, absent from the job records; "
                "this linear projection does not measure native wall time or account for overhead changes.",
                "",
            ]
        )
    lines.extend(
        [
            f'<img src="{image_src}" alt="Measured cannon 30k seconds per 1k steps against megapixels, '
            'labelled by resolution and arm">',
            "",
            "## Masking A/B",
            "",
            "Cannon, 1920 px, 30,000 steps.",
            "",
            "Unmasked supervises the entire image, including the background.",
            "",
            'Masks-folder zeroes loss outside the subject ("don\'t care"), leaving stray splats there unpunished.',
            "",
            'Alpha-matched uses the mask as the RGBA alpha target ("render nothing here"), supervising '
            "transparency outside the subject.",
            "",
            "| Arm | Job | Images | Wall (s) | Raw splats | Cleaned splats | Quantile | SOG (bytes) | "
            "Raw reduction | Cleaned reduction |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for job_id, run, j in sorted(full, key=lambda row: (ARMS.index(row[1]["arm"]), row[0])):
        if j["max_res"] != 1920:
            continue
        clean = run.get("cleaned", {})
        count = clean.get("splats")
        cleaned = f"{count:,}" if count is not None else "unavailable"
        size = clean.get("sog_bytes")
        size_text = f"{size:,}" if size is not None else "unavailable"
        reduction = f"{100 * (1 - count / baseline):.2f}%" if count is not None else "unavailable"
        lines.append(
            f"| {run['arm']} | {job_id} | {j['images']} | {j['wall_seconds']:.1f} | "
            f"{j['splats']:,} | {cleaned} | {clean.get('quantile', 'unavailable')} | {size_text} | "
            f"{100 * (1 - j['splats'] / baseline):.2f}% | {reduction} |"
        )
    lines.extend(
        [
            "",
            f"Both reduction columns use the unmasked raw baseline ({baseline:,} splats): "
            "100 * (1 - count / baseline). SOG sizes are recorded exports, not estimates; "
            "unavailable means not recorded.",
            "",
            "Image counts differ for the alpha arm; cleaning quantiles also differ, so cleaned "
            "counts are not a controlled "
            "comparison of supervision alone. Smaller raw scenes plus cleaning and SOG packing "
            "yield smaller deliveries; "
            "these records do not establish visual quality or a compression ratio by themselves.",
            "",
            "## Probes",
            "",
            "Short runs only; excluded from the 30k comparisons and chart.",
            "",
            "| Job | Subject | Arm | Steps | max_res (px) | Images | s / 1k steps | Wall (s) | "
            "Splats | Peak RSS (MB) |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for job_id, run, j in rows:
        if run.get("probe") or j["steps"] < 30000:
            lines.append(
                f"| {job_id} | {run['subject']} | {run['arm']} | {j['steps']:,} | {j['max_res']} | "
                f"{j['images']} | {j['seconds_per_1k_steps']:.2f} | {j['wall_seconds']:.1f} | "
                f"{j['splats']:,} | {j['peak_rss_mb']:.1f} |"
            )
    lines.extend(["", *isolation_section(index_path.parent / "isolation.json"), *cleaning_section(ROOT)])
    lines.extend(
        [
            "",
            "---",
            "",
            "Generated by `tools/results_table.py` from `docs/results/runs.json` and "
            "`docs/results/jobs/<job-id>.json` (job IDs above identify each source), "
            "plus `docs/results/isolation.json`, `checks.json`, `scenes.json` and `provenance/<stem>.json`.",
            "Generated at: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "",
        ]
    )
    return "\n".join(lines), chart(full)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/results.md")
    args = parser.parse_args(argv)
    svg_path = ROOT / "docs/results/sweep.svg"
    image_src = Path(os.path.relpath(svg_path, args.out.resolve().parent)).as_posix()
    page, svg = generate(ROOT / "docs/results/runs.json", ROOT / "docs/results/jobs", image_src)
    if args.check:
        with tempfile.TemporaryDirectory() as directory:
            for name, destination, expected in [("results.md", args.out, page), ("sweep.svg", svg_path, svg)]:
                candidate = Path(directory) / name
                candidate.write_text(expected)
                if not destination.is_file() or STAMP.sub("", destination.read_text()) != STAMP.sub(
                    "", candidate.read_text()
                ):
                    print(f"Stale or missing {name}; run tools/results_table.py to regenerate.")
                    return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.out.is_file() or STAMP.sub("", args.out.read_text()) != STAMP.sub("", page):
        args.out.write_text(page)
    if not svg_path.is_file() or svg_path.read_text() != svg:
        svg_path.write_text(svg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
