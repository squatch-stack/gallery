#!/usr/bin/env python3
"""Clean a raw Gaussian-splat PLY and export web-delivery formats.

    .venv/bin/python tools/clean_export.py "in.ply" --stem brookline-station \
        --out scenes --archive archive

Cleaning, in order:
  1. opacity floor — splats below --alpha-min carry almost no light and
     a surprising share of the bytes;
  2. mass-centered crop — captures carry background floaters out to the
     format's clamp box (Scaniverse: +-240 m), so the subject is found by
     alpha-weighted quantiles, not by the bounding box;
  3. scale ceiling — a splat wider than a fraction of the cropped extent
     is fog from the optimizer, not scene;
  4. optional contribution budget — keep the top --target-count splats by
     alpha times the product of their two largest scale axes.

Exports: cleaned PLY (archive, lossless), SPZ v3 (interop), SOG with SH
palette (web delivery; Spark reads it). All through holo's own writers —
this doubles as a real-data exercise of the studio's exporters.
"""

import argparse
import os
import sys


sys.path.insert(0, os.path.expanduser("~/Documents/HDC-VSA-Gaussian-Splatting"))


def parse_vector(value):
    """Parse a finite scene-space vector before loading a potentially large file."""
    import numpy as np

    try:
        vector = np.array([float(v) for v in value.split(",")])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected three finite numbers x,y,z") from exc
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise argparse.ArgumentTypeError("expected three finite numbers x,y,z")
    return vector


def crop_mask(pos, alpha, center, quantile=0.90, margin=1.4, radius=0.0, shape="box", up=None):
    """Return membership, world bounds, local half-extents and basis columns.

    Curved shapes use independent weighted quantiles in their local frame.
    A cylinder's third local axis is up; the first two span its elliptical
    footprint. Explicit radius overrides quantiles AND margin for every shape.
    Zero half-extents constrain that coordinate to the centre plane exactly.
    """
    import numpy as np

    basis = np.eye(3)
    if shape == "box":
        # Preserve the historical operations, including inclusive bounds and
        # radius overriding margin, so existing selections stay byte-identical.
        if radius > 0:
            lo, hi = center - radius, center + radius
        else:
            shared = weighted_quantile(np.abs(pos - center).max(axis=1), alpha, quantile)
            lo, hi = center - margin * shared, center + margin * shared
        inside = np.all((pos >= lo) & (pos <= hi), axis=1)
        return inside, lo, hi, (hi - lo) / 2, basis

    delta = pos - center
    if shape == "cylinder":
        if up is None:
            extents = [weighted_quantile(np.abs(delta[:, i]), alpha, quantile) for i in range(3)]
            vertical = basis[:, int(np.argmin(extents))]
        else:
            vertical = np.asarray(up, dtype=float)
            vertical = vertical / np.max(np.abs(vertical))
            vertical = vertical / np.linalg.norm(vertical)
        # Project the least-aligned world axis into the horizontal plane.
        # This is deterministic and remains well-conditioned near axis alignment.
        seed = basis[:, int(np.argmin(np.abs(vertical)))]
        horizontal = seed - np.dot(seed, vertical) * vertical
        horizontal /= np.linalg.norm(horizontal)
        basis = np.column_stack((horizontal, np.cross(vertical, horizontal), vertical))
    elif shape != "ellipsoid":
        raise ValueError(f"unknown crop shape: {shape}")
    local = delta @ basis
    half = (np.full(3, radius) if radius > 0 else margin * np.array([
        weighted_quantile(np.abs(local[:, i]), alpha, quantile) for i in range(3)
    ]))
    normalised = np.zeros_like(local)
    np.divide(local, half, out=normalised, where=half > 0)
    normalised[:, half == 0] = np.where(local[:, half == 0] == 0, 0.0, np.inf)
    if shape == "ellipsoid":
        inside = np.sum(normalised**2, axis=1) <= 1
        world_half = half
    else:
        inside = (np.sum(normalised[:, :2]**2, axis=1) <= 1) & (np.abs(local[:, 2]) <= half[2])
        world_half = np.sqrt(np.sum((basis[:, :2] * half[:2])**2, axis=1)) + np.abs(basis[:, 2]) * half[2]
    return inside, center - world_half, center + world_half, half, basis


def contribution_keep(scale, alpha, target_count):
    """Return an input-order mask and the fraction of footprint mass removed.

    Stable sorting resolves equal scores in favor of earlier input splats.
    The denominator is the mass remaining after all preceding cleaning.
    """
    import numpy as np

    keep = np.ones(len(alpha), dtype=bool)
    if len(alpha) <= target_count:
        return keep, 0.0
    axes = np.sort(scale, axis=1)
    score = alpha.astype(np.float64) * axes[:, -1] * axes[:, -2]
    order = np.argsort(-score, kind="stable")
    keep[order[target_count:]] = False
    total = score.sum(dtype=np.float64)
    fraction = float(score[~keep].sum(dtype=np.float64) / total) if total > 0 else 0.0
    return keep, fraction


def load_gaussian_ply_any_order(path):
    """A 3DGS PLY with properties in any header order (Brush sorts them
    alphabetically, which puts x/y/z last and trips order-assuming
    parsers). Indexes fields by name; same conventions as holo's loader:
    SH_C0 color, sigmoid opacity, exp scales, y-up flip on the way in.
    Returns (pos, scale, rgba, quat, sh)."""
    import numpy as np

    from holo.capture import SH_C0, _to_y_up, sh_flip_x180

    with open(path, "rb") as f:
        assert f.readline().strip() == b"ply"
        props, n = [], 0
        while True:
            parts = f.readline().strip().split()
            if parts[0] == b"end_header":
                break
            if parts[0] == b"format":
                assert parts[1] == b"binary_little_endian", parts
            elif parts[0] == b"element":
                if parts[1] == b"vertex":
                    n = int(parts[2])
            elif parts[0] == b"property":
                props.append(parts[2].decode())
        dt = np.dtype([(p, "<f4") for p in props])
        rec = np.frombuffer(f.read(n * dt.itemsize), dtype=dt, count=n)

    pos = np.stack([rec["x"], rec["y"], rec["z"]], 1).astype(np.float64)
    color = np.clip(
        0.5 + SH_C0 * np.stack([rec["f_dc_0"], rec["f_dc_1"], rec["f_dc_2"]], 1),
        0, 1).astype(np.float32)
    alpha = (1.0 / (1.0 + np.exp(-rec["opacity"])))[:, None].astype(np.float32)
    scale = np.exp(np.stack(
        [rec["scale_0"], rec["scale_1"], rec["scale_2"]], 1)).astype(np.float64)
    quat = np.stack([rec[f"rot_{i}"] for i in range(4)], 1).astype(np.float64)
    quat /= np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-9)
    pos, quat = _to_y_up(pos, quat)

    rest = sorted((p for p in props if p.startswith("f_rest_")),
                  key=lambda p: int(p.split("_")[-1]))
    sh = None
    if rest:
        k = len(rest) // 3
        flat = np.stack([rec[p] for p in rest], 1).astype(np.float32)
        sh = sh_flip_x180(flat.reshape(n, 3, k))
    return pos, scale, np.concatenate([color, alpha], 1), quat, sh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--out", default="scenes")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--alpha-min", type=float, default=0.02)
    ap.add_argument("--crop-quantile", type=float, default=0.90)
    ap.add_argument("--crop-margin", type=float, default=1.4)
    ap.add_argument("--crop-shape", choices=["box", "ellipsoid", "cylinder"], default="box",
                    help="box uses the legacy shared radius; curved shapes use per-axis weighted extents")
    ap.add_argument("--up", type=parse_vector,
                    help="cylinder axis x,y,z in loaded scene/viewer coordinates (scenes.json up); "
                         "default is the axis with smallest alpha-weighted extent")
    ap.add_argument("--center", default="",
                    help="crop centre as x,y,z in scene units (e.g. the cameras' "
                         "convergence point from tools/scene_up.py); default is "
                         "the alpha-weighted median of the splats")
    ap.add_argument("--crop-radius", type=float, default=0.0,
                    help="absolute half-extent on every crop axis in scene units "
                         "(metres for COLMAP scenes); overrides quantile and margin for every shape")
    ap.add_argument("--max-aspect", type=float, default=0.0,
                    help="drop splats whose aspect ratio (see --aspect-measure) exceeds this; "
                         "167 = a healthy gsplat scene's p90, but the 23rd-27th percentile of a "
                         "Brush scene (measured on the cannon's three arms), so it discards three "
                         "quarters of a Brush cloud; 0 = off")
    ap.add_argument("--aspect-measure", choices=["minmax", "second"], default="minmax",
                    help="minmax = longest/shortest axis (a healthy gsplat scene keeps p90 near 170); "
                         "second = longest/second-longest (a line, not a disc)")
    ap.add_argument("--scale-ceiling", type=float, default=0.05,
                    help="max splat axis as a fraction of the cropped extent")
    ap.add_argument("--sh-clusters", type=int, default=1024)
    ap.add_argument("--target-count", type=int,
                    help="positive splat budget after cleaning; keep highest alpha-weighted footprints")
    args = ap.parse_args()
    import numpy as np

    from holo.capture import load_ply_sh, load_scene_file, load_spz_sh, save_spz
    from holo.sog import save_sog
    if args.target_count is not None and args.target_count <= 0:
        ap.error("--target-count must be positive")
    if args.up is not None and not np.any(args.up):
        ap.error("--up must be nonzero")
    if not np.isfinite(args.crop_quantile) or not 0 <= args.crop_quantile <= 1:
        ap.error("--crop-quantile must be between 0 and 1")
    if not np.isfinite(args.crop_margin) or args.crop_margin <= 0:
        ap.error("--crop-margin must be finite and positive")
    if not np.isfinite(args.crop_radius) or args.crop_radius < 0:
        ap.error("--crop-radius must be finite and nonnegative")
    if args.center and args.center != "dense":
        try:
            explicit_center = parse_vector(args.center)
        except argparse.ArgumentTypeError as exc:
            ap.error(f"--center: {exc}")

    try:
        pos, scale, rgba, quat = load_scene_file(args.scene)
        if args.scene.endswith(".ply"):
            sh = load_ply_sh(args.scene)
        elif args.scene.endswith(".spz"):
            sh = load_spz_sh(args.scene)
        else:
            sh = None
    except AssertionError:
        pos, scale, rgba, quat, sh = load_gaussian_ply_any_order(args.scene)
    n0 = len(pos)

    finite = (
        np.isfinite(pos).all(axis=1)
        & np.isfinite(scale).all(axis=1)
        & np.isfinite(rgba).all(axis=1)
        & np.isfinite(quat).all(axis=1)
    )
    keep = finite & (rgba[:, 3] >= args.alpha_min)
    pos, scale, rgba, quat = pos[keep], scale[keep], rgba[keep], quat[keep]
    if sh is not None:
        sh = sh[keep]
    a = rgba[:, 3]
    if not len(pos) or a.sum() <= 0:
        ap.error("no positive alpha mass remains after the opacity/finite filter")

    if args.center == "dense":
        # The subject is where opaque splats crowd together; grass, sky and
        # trees are diffuse. Take the densest cell of a fine histogram of
        # the opaque splats as the centre, then refine to their local median.
        opaque = pos[a >= 0.5]
        if not len(opaque):
            ap.error("--center dense requires splats with alpha >= 0.5")
        span = np.percentile(opaque, 95, axis=0) - np.percentile(opaque, 5, axis=0)
        cell = max(float(span.max()) / 200.0, 1e-6)
        cells = np.floor(opaque / cell).astype(np.int64)
        _, inv, counts = np.unique(cells, axis=0, return_inverse=True, return_counts=True)
        seed = opaque[inv == np.argmax(counts)].mean(axis=0)
        near = opaque[np.linalg.norm(opaque - seed, axis=1) < 20 * cell]
        center = np.median(near, axis=0)
        print(f"dense centre {center.round(3)} (cell {cell:.3f}, "
              f"{int(counts.max())} opaque splats in the densest cell)")
    elif args.center:
        center = explicit_center
    else:
        center = np.array([weighted_quantile(pos[:, i], a, 0.5) for i in range(3)])
    inside, lo, hi, half, basis = crop_mask(
        pos, a, center, args.crop_quantile, args.crop_margin, args.crop_radius, args.crop_shape, args.up,
    )
    print(f"crop shape {args.crop_shape}; local half-extents {half}; world extents {hi - lo}; "
          f"bounds {lo} to {hi}")
    if args.crop_shape == "cylinder":
        print(f"cylinder up {basis[:, 2]}; {'explicit --up' if args.up is not None else 'smallest weighted extent'}")

    extent = float((hi - lo).max())
    small = scale.max(axis=1) <= args.scale_ceiling * extent
    mask = inside & small
    if args.max_aspect > 0:
        # A needle is one long axis against the OTHER TWO; a flat disc (one
        # tiny axis, two normal) is an ordinary splat and must survive, so the
        # ratio is largest over second-largest, never largest over smallest.
        axes = np.sort(scale, axis=1)
        if args.aspect_measure == "second":
            aspect = axes[:, 2] / np.maximum(axes[:, 1], 1e-12)
        else:
            aspect = axes[:, 2] / np.maximum(axes[:, 0], 1e-12)
        kept = aspect[mask]
        p50, p90, p99 = np.percentile(kept, [50, 90, 99]) if kept.size else (0, 0, 0)
        needles = mask & (aspect > args.max_aspect)
        mask &= ~needles
        print(f"aspect ({args.aspect_measure}) p50 {p50:,.0f} p90 {p90:,.0f} p99 {p99:,.0f}; "
              f"cap {args.max_aspect:g}: dropped {int(needles.sum()):,} needle splats")

    pos, scale, rgba, quat = pos[mask], scale[mask], rgba[mask], quat[mask]
    if sh is not None:
        sh = sh[mask]

    cleaned_count = len(pos)
    if not cleaned_count:
        ap.error("no splats remain after crop/scale/aspect cleaning; no exports written")
    if args.target_count is not None:
        selected, removed_fraction = contribution_keep(scale, rgba[:, 3], args.target_count)
        pos, scale, rgba, quat = pos[selected], scale[selected], rgba[selected], quat[selected]
        if sh is not None:
            sh = sh[selected]
        print(f"target-count {args.target_count:,}: removed {cleaned_count - len(pos):,} splats; "
              f"removed-mass fraction {removed_fraction:.12g} "
              f"({removed_fraction:.6%} of pre-reduction alpha-weighted area)")

    print(f"{os.path.basename(args.scene)}: {n0:,} -> {len(pos):,} splats "
          f"(alpha floor cut {n0 - int(keep.sum()):,}; crop+fog cut "
          f"{int(keep.sum()) - cleaned_count:,}; contribution cut "
          f"{cleaned_count - len(pos):,}); extent {extent:.2f} m "
          f"at {center.round(2)}")

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.archive, exist_ok=True)
    archive_ply = os.path.join(args.archive, f"{args.stem}.ply")
    out_spz = os.path.join(args.out, f"{args.stem}.spz")
    out_sog = os.path.join(args.out, f"{args.stem}.sog")

    save_ply(archive_ply, pos, scale, rgba, quat)
    save_spz(out_spz, pos, scale, rgba, quat)
    save_sog(out_sog, pos, scale, rgba, quat, sh=sh,
             sh_clusters=args.sh_clusters)

    for path in (archive_ply, out_spz, out_sog):
        print(f"  {path}  {os.path.getsize(path) / 1e6:.1f} MB")


def weighted_quantile(*args, **kwargs):
    from holo.capture import weighted_quantile as implementation
    return implementation(*args, **kwargs)


def save_ply(*args, **kwargs):
    from holo.capture import save_ply as implementation
    return implementation(*args, **kwargs)


if __name__ == "__main__":
    main()
