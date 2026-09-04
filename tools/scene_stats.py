#!/usr/bin/env python3
"""Report isolation and world-space scale statistics using the buyer checker's decode."""

import argparse
import json
from pathlib import Path
import zipfile

if __package__:
    from .check_deliverable import read_ply, read_sog, weighted_median
else:
    from check_deliverable import read_ply, read_sog, weighted_median


def statistics(arrays):
    """Use the checker's finite population, L-infinity distance and median convention."""
    import numpy as np

    pos, scale, alpha, raw = arrays
    finite = np.isfinite(pos).all(1) & np.isfinite(scale).all(1) & np.isfinite(alpha) & np.isfinite(raw).all(1)
    p, s, a = pos[finite], scale[finite], alpha[finite]
    result = {
        "splats": len(pos),
        "finite_splats": int(finite.sum()),
        "nonfinite_count": int((~finite).sum()),
        "center": None,
        "mad": None,
        "within_mad": dict.fromkeys(("1", "3", "5", "10")),
        "long_axis_fraction": dict.fromkeys(("0.25", "1.0")),
        "long_axis_percentiles": dict.fromkeys(("p50", "p90", "p99")),
    }
    if not len(p):
        return result
    longest = s.max(axis=1)
    result["long_axis_fraction"] = {str(t): float(np.mean(longest > t)) for t in (0.25, 1.0)}
    result["long_axis_percentiles"] = dict(
        zip(
            ("p50", "p90", "p99"),
            np.percentile(longest, [50, 90, 99]).tolist(),
            strict=True,
        )
    )
    if a.sum() > 0:
        center = np.array([weighted_median(p[:, i], a) for i in range(3)])
        distance = np.max(np.abs(p - center), axis=1)
        mad = weighted_median(distance, a)
        result.update(center=center.tolist(), mad=mad)
        result["within_mad"] = {str(k): float(np.mean(distance <= k * mad)) for k in (1, 3, 5, 10)}
    return result


def scene_stats(path):
    path = Path(path)
    loaders = {".ply": read_ply, ".sog": read_sog}
    if path.suffix.lower() not in loaders:
        raise ValueError("expected a PLY or SOG file")
    _, arrays = loaders[path.suffix.lower()](path)
    return statistics(arrays)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cloud", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = scene_stats(args.cloud)
    except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"scene_stats: {exc}\n")
    if args.json:
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print(f"Splats: {result['splats']:,}; finite: {result['finite_splats']:,}")
        print(f"Alpha-weighted median centre: {result['center']}")
        print(f"Weighted median L-infinity distance (MAD): {result['mad']}")
        for k, value in result["within_mad"].items():
            print(f"Fraction within {k}x MAD: {value}")
        for threshold, value in result["long_axis_fraction"].items():
            print(f"Fraction with longest axis > {threshold} world units: {value}")
        print("Longest-axis percentiles (world units): " + json.dumps(result["long_axis_percentiles"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
