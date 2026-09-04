#!/usr/bin/env python3
"""Offline field plans; numerical policy lives in capture_defaults.json."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DEFAULTS_PATH = Path(__file__).with_name("capture_defaults.json")
GUIDE = "docs/capture.md"
TYPES = ("object", "building", "interior", "site")


def load_defaults():
    return json.loads(DEFAULTS_PATH.read_text())


def ring_geometry(radius, clearance, hfov, target, max_step):
    """Local planar footprint/chord proxy, not a guarantee of scene correspondence.

    Angles are radians internally. Clearance is distance to nearest subject surface.
    Constants in this function are geometric identities, not capture defaults.
    """
    footprint = 2 * clearance * math.tan(hfov / 2)
    allowed_chord = (1 - target) * footprint
    allowed_angle = 2 * math.asin(min(1, allowed_chord / (2 * radius)))
    stations = math.ceil(math.tau / min(allowed_angle, math.radians(max_step)))
    step = math.tau / stations
    chord = 2 * radius * math.sin(step / 2)
    return {
        "stations": stations, "angular_step_deg": math.degrees(step),
        "footprint_m": footprint, "station_chord_m": chord,
        "overlap_fraction": 1 - chord / footprint,
        "max_overlap_step_deg": math.degrees(allowed_angle),
    }


def make_plan(subject_type, size, height=None):
    defaults = load_defaults()
    v = {key: item["value"] for key, item in defaults.items()}
    if subject_type not in TYPES:
        raise ValueError("unknown subject type")
    if not math.isfinite(size) or size <= 0 or (height is not None and (not math.isfinite(height) or height <= 0)):
        raise ValueError("size and height must be finite positive metres")
    height_assumed = height is None
    height = height if height is not None else size * v["height_to_size_ratio"]
    hfov = 2 * math.atan(v["equivalent_sensor_width_mm"] / (2 * v["focal_length_equivalent_mm"]))
    vfov = 2 * math.atan(math.tan(hfov / 2) * v["image_height_px"] / v["image_width_px"])
    levels = v["min_height_levels"] + math.ceil(max(size, height) / v["metres_per_extra_level"]) - 1
    heights = [height * (index + 1) / (levels + 1) for index in range(levels)]
    # Enclose the subject in a cylinder; aim horizontally toward its axis.
    clearance = v["framing_margin"] * max(
        (size / 2) / math.tan(hfov / 2),
        max(max(h, height - h) for h in heights) / math.tan(vfov / 2),
    )
    base_radius = size / 2 + clearance
    rings = []
    for multiplier in v["radius_multipliers"]:
        radius = base_radius * multiplier
        for camera_height in heights:
            rings.append({
                "radius_from_centre_m": radius, "distance_from_surface_m": radius - size / 2,
                "camera_height_m": camera_height,
                **ring_geometry(radius, radius - size / 2, hfov, v["overlap_target"], v["max_step_deg"]),
            })
    warnings = [
        f"Vegetation in wind becomes thin splats: isolate the rigid subject with subject masks and train with alpha. "
        f"[{GUIDE} > At the subject; Back at the desk, in order]",
        f"Thin orbits fragment solves: capture both radii and all heights; add rings if coverage has gaps. "
        f"[{GUIDE} > At the subject]",
        f"{v['symmetry_policy']} [operator default; {GUIDE} > Back at the desk, in order recommends global mapping, "
        "and does not discuss symmetric subjects]",
    ]
    if subject_type == "interior":
        warnings.extend([
            f"{v['interior_start']} [operator default; {GUIDE} > At the subject supplies only the overview principle]",
            "Interior rings below are geometric coverage targets only: walls may make them impossible. "
            "Split the space into accessible rigid subjects and re-plan; validate the connected pass before leaving.",
        ])
    if subject_type in ("building", "site"):
        warnings.append(
            f"Name each rigid subject; close-ups of walls alone are not a building. "
            f"[{GUIDE} > Before you leave; What the numbers looked like on the first day]"
        )
    if subject_type == "site":
        warnings.append("Site size describes one rigid subject envelope; repeat this plan for each named subject.")
    warnings.append("Check access and camera-height feasibility first; elevated rings require safe permitted access. "
                    "If inaccessible, record missing coverage; this plan does not authorize climbing or drones.")
    checklist = [
        f"Check permission/access, name rigid subjects, charge phone; use stock camera, Smart HDR on, stills. "
        f"[{GUIDE} > Before you leave]",
        f"Check wind and lighting; choose a still morning for moving subjects. [{GUIDE} > At the subject]",
        f"{v['exposure_lock']} [operator default; {GUIDE} > Before you leave allows Smart HDR]",
        f"Keep whole subject in frame for most shots; take texture close-ups after the orbits. "
        f"[{GUIDE} > At the subject]",
        f"Do not change lens or zoom mid-orbit; keep image sizes consistent. [{GUIDE} > At the subject]",
        f"{v['preserve_files']} [operator default]",
        f"{v['provenance_evidence']} [operator default]",
        f"{v['scale_reference']} [operator default]",
        f"Solve at low resolution and count registered views before leaving; fill coverage gaps. "
        f"[{GUIDE} > At the subject]",
        f"Generate the provenance sheet from files with tools/provenance.py; do not type measured numbers into it. "
        f"[{GUIDE} > Back at the desk, in order]",
    ]
    total_stations = sum(r["stations"] for r in rings)
    return {
        "subject_type": subject_type, "size_m": size, "height_m": height, "height_assumed": height_assumed,
        "geometry": {
            "horizontal_fov_deg": math.degrees(hfov), "vertical_fov_deg": math.degrees(vfov),
            "overlap_target": v["overlap_target"], "guide_overlap_floor": v["guide_overlap_floor"],
            "model": "Landscape pinhole; size is maximum horizontal diameter, height is vertical extent. "
                     "Subject is enclosed in a cylinder; camera points horizontally toward its axis. "
                     "Footprint/chord overlap is a planning proxy; occlusion and texture require field validation.",
            "arithmetic": "HFOV = 2 atan(equivalent_sensor_width_mm / (2 focal_length_equivalent_mm)); "
                          "VFOV = 2 atan(tan(HFOV/2) * image_height_px/image_width_px); "
                          "levels = min_height_levels + ceil(max(size,height)/metres_per_extra_level) - 1; "
                          "h_i = height*(i+1)/(levels+1); "
                          "clearance = framing_margin * max((size/2)/tan(HFOV/2), max(h_i,height-h_i)/tan(VFOV/2)); "
                          "r = (size/2 + clearance)*radius_multiplier; d = r-size/2; "
                          "W = 2*d*tan(HFOV/2); allowed_step = 2 asin(min(1,(1-target)*W/(2*r))); "
                          "N = ceil(2*pi/min(allowed_step,max_step)); step = 2*pi/N; "
                          "chord = 2*r*sin(step/2); overlap = 1-chord/W. Angles in radians for arithmetic.",
        },
        "rings": rings, "total_stations": total_stations,
        "total_photographs": total_stations * v["photos_per_station"],
        "time_minutes": total_stations / v["stations_per_minute"],
        "stations_per_minute": v["stations_per_minute"],
        "count_note": f"Guide typical range: {v['field_frame_range']} frames (Before you leave); not a cap. "
                      "Totals exclude detail, scale and provenance photographs. "
                      "Time excludes setup/access and those extras.",
        "warnings": warnings, "checklist": checklist, "defaults": defaults,
        "operator_defaults": {key: item for key, item in defaults.items() if item["source"] == "operator default"},
    }


def render_markdown(plan):
    g = plan["geometry"]
    lines = [
        f"# Capture plan: {plan['subject_type']}", "",
        f"Size: {plan['size_m']:g} m; height: {plan['height_m']:g} m "
        f"({'assumed' if plan['height_assumed'] else 'measured'}).", "",
        f"{plan['total_photographs']} photographs at {plan['total_stations']} stations; "
        f"{plan['time_minutes']:.1f} minutes at {plan['stations_per_minute']:g} stations/minute.",
        plan["count_note"], "", "## Geometry and overlap arithmetic", "", g["model"], "",
        f"HFOV = {g['horizontal_fov_deg']:.4f} degrees; VFOV = {g['vertical_fov_deg']:.4f} degrees. "
        f"Target overlap = {g['overlap_target']:.0%}, above guide floor {g['guide_overlap_floor']:.0%} "
        f"[{GUIDE} > At the subject].", "", g["arithmetic"], "",
        "| Ring | Centre radius m | Surface distance m | Camera height m | Stations | Step deg | "
        "W m | Chord m | Overlap |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, r in enumerate(plan["rings"], start=1):
        lines.append(
            f"| {index} | {r['radius_from_centre_m']:.3f} | {r['distance_from_surface_m']:.3f} | "
            f"{r['camera_height_m']:.3f} | {r['stations']} | {r['angular_step_deg']:.3f} | "
            f"{r['footprint_m']:.3f} | {r['station_chord_m']:.3f} | {r['overlap_fraction']:.2%} |"
        )
    lines.extend(["", "## Warnings", "", *(f"- {w}" for w in plan["warnings"]),
                  "", "## Field checklist", "", *(f"- [ ] {c}" for c in plan["checklist"]),
                  "", "## Defaults and sources", ""])
    for key, item in plan["defaults"].items():
        lines.append(f"- `{key}` = {json.dumps(item['value'])}; source: {item['source']}. {item['rationale']}")
    lines.extend(["", "Operator defaults for owner review: " + ", ".join(plan["operator_defaults"]) + ".", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", required=True, choices=TYPES, dest="subject_type")
    parser.add_argument("--size", required=True, type=float, help="maximum horizontal subject diameter in metres")
    parser.add_argument("--height", type=float, help="vertical extent in metres; defaults to size")
    parser.add_argument("--out", type=Path, help="write the selected format to this file as well as stdout")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = parser.parse_args()
    try:
        plan = make_plan(args.subject_type, args.size, args.height)
    except ValueError as exc:
        parser.error(str(exc))
    output = json.dumps(plan, indent=2, allow_nan=False) + "\n" if args.json else render_markdown(plan)
    if args.out:
        args.out.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
