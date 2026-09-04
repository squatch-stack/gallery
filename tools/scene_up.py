"""The gravity direction of a COLMAP-solved scene, for scenes.json.

    python tools/scene_up.py captures/cannon [sparse-dir] [--axis -y|+y|-x|+x]
    python tools/scene_up.py [captures/cannon] --from-cloud exports/cannon.ply

COLMAP solves carry no gravity, so a splat renders at whatever tilt the first
camera had. Handheld photographs are taken roughly level, so the mean of the
solved cameras' own up axes is a good estimate of world up. This prints that
vector in the frame the gallery viewer uses: the viewer flips the splat
180 degrees about x on load (y and z negate), then aligns `entry.up` to +y,
so the value here is the world-up estimate with y and z negated.

The camera estimate was validated against the cannon's hand-measured catalog
up. --from-cloud instead fits a dominant ground plane with NumPy-only RANSAC;
it needs no solve unless camera comparisons are desired. Cloud-frame tests
are synthetic; the actual cannon PLY has not been validated by this method.
"""
import argparse
import json
import pathlib
import sys


AXES = {"-y": (0, -1, 0), "+y": (0, 1, 0), "-x": (-1, 0, 0), "+x": (1, 0, 0)}
FRAME = "catalog/viewer before gravity alignment: file (x, -y, -z)"


def estimate_cloud_up(positions, alpha, candidates=None, alpha_min=0.2):
    """Fit ground in catalog coordinates, using bounded NumPy-only RANSAC.

    Local neighbour imbalance / radius proxies the density gradient. Restrict
    seeds to dense, planar, low-gradient neighbourhoods; score support against
    the whole sample so a tiny dense patch cannot win merely by being dense.
    Extent is the 5th-to-95th percentile width along both in-plane PCA axes,
    in the input cloud's units (Brush may rescale the solve).
    """
    import numpy as np

    if __package__:
        from .check_deliverable import weighted_median
    else:
        from check_deliverable import weighted_median

    result = {"up": None, "inliers": 0.0, "extent": None,
              "agreement_deg": dict.fromkeys(AXES), "reason": None}

    def reject(reason):
        result["reason"] = reason
        return result

    p, a = np.asarray(positions, dtype=float), np.asarray(alpha, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or a.shape != (len(p),):
        raise ValueError("expected positions (N, 3) and alpha (N,)")
    if not 0 <= alpha_min < 1:
        raise ValueError("alpha_min must be finite and in [0, 1)")
    valid = np.isfinite(p).all(axis=1) & np.isfinite(a) & (a > alpha_min) & (a <= 1)
    p, a = p[valid], a[valid]
    if len(p) < 64:
        return reject("fewer than 64 finite splats above the alpha threshold")
    # Centre and normalise isotropically: no preferred world axis or unit.
    origin = np.median(p, axis=0)
    size = float(np.percentile(np.linalg.norm(p - origin, axis=1), 90))
    if size <= 0:
        return reject("cloud has zero extent")
    p = (p - origin) / size
    rng = np.random.default_rng(17)
    sample = p[np.sort(rng.choice(len(p), min(len(p), 4096), replace=False))]
    gradient, density, planar = [], [], []
    for start in range(0, len(sample), 128):
        delta = sample[None, :, :] - sample[start:start + 128, None, :]
        distance2 = np.sum(delta * delta, axis=2)
        indices = np.argpartition(distance2, 24, axis=1)[:, :25]
        near = np.take_along_axis(delta, indices[:, :, None], axis=1)
        radius2 = np.max(np.sum(near * near, axis=2), axis=1)
        mean = near.mean(axis=1)
        centred = near - mean[:, None, :]
        eigen = np.linalg.eigvalsh(np.einsum("bki,bkj->bij", centred, centred) / 25)
        gradient.extend(np.linalg.norm(mean, axis=1) / np.sqrt(np.maximum(radius2, 1e-24)))
        density.extend(1 / np.maximum(radius2, 1e-24))
        planar.extend((eigen[:, 0] < 0.04 * eigen[:, 1]) & (eigen[:, 1] > 0.08 * eigen[:, 2]))
    gradient, density, planar = np.asarray(gradient), np.asarray(density), np.asarray(planar)
    eligible = planar & (density >= np.percentile(density, 25))
    if eligible.sum() < 32:
        return reject("no dense near-planar region")
    eligible &= gradient <= np.percentile(gradient[eligible], 70)
    seeds = sample[eligible]
    tolerance = 0.01
    best_count, best = 0, None
    for _ in range(512):
        triple = seeds[rng.choice(len(seeds), 3, replace=False)]
        normal = np.cross(triple[1] - triple[0], triple[2] - triple[0])
        length = np.linalg.norm(normal)
        if length < 1e-10:
            continue
        normal /= length
        mask = np.abs(np.sum((sample - triple[0]) * normal, axis=1)) <= tolerance
        count = int(mask.sum())
        if count > best_count:
            best_count, best = count, mask
    if best is None or best_count < max(64, 0.2 * len(sample)):
        return reject("no dominant plane with at least 20% support")
    # Two fixed least-squares refinements, then confidence on ALL eligible
    # input splats, not just the bounded RANSAC sample.
    for _ in range(2):
        center = sample[best].mean(axis=0)
        delta = sample[best] - center
        eigen, basis = np.linalg.eigh(delta.T @ delta / len(delta))
        normal = basis[:, 0]
        best = np.abs(np.sum((sample - center) * normal, axis=1)) <= tolerance
    signed = np.sum((p - center) * normal, axis=1)
    inside = np.abs(signed) <= tolerance
    result["inliers"] = float(inside.mean())
    footprint = (p[inside] - center) @ basis[:, 1:]
    widths = np.diff(np.percentile(footprint, [5, 95], axis=0), axis=0)[0]
    result["extent"] = (widths * size).tolist()
    if result["inliers"] < 0.2 or widths.min() < 0.15 or eigen[0] > 0.02 * eigen[1]:
        return reject("plane support is too small, narrow, or thick")
    # Project first, then take a weighted median: coordinate-wise medians
    # are not rotation-equivariant. Exact ground can put the median ON the
    # plane; in that case use off-plane mass to resolve the sign.
    side = weighted_median(signed, a)
    off_plane = ~inside
    if abs(side) <= 1e-8:
        if off_plane.sum() < 16:
            return reject("plane found but no subject above it to orient the normal")
        side = weighted_median(signed[off_plane], a[off_plane])
    if side < 0:
        normal = -normal
    result["up"] = normal.tolist()
    for name, candidate in (candidates or {}).items():
        if candidate is not None:
            vector = np.asarray(candidate, dtype=float)
            length = np.linalg.norm(vector)
            if np.isfinite(vector).all() and length > 0:
                result["agreement_deg"][name] = float(
                    np.degrees(np.arccos(np.clip(normal @ (vector / length), -1, 1)))
                )
    return result


def cloud_estimate(path, candidates=None, alpha_min=0.2):
    if __package__:
        from .check_deliverable import read_ply, read_sog
    else:
        from check_deliverable import read_ply, read_sog

    path = pathlib.Path(path).expanduser()
    loaders = {".ply": read_ply, ".sog": read_sog}
    if path.suffix.lower() not in loaders:
        raise ValueError("expected a PLY or SOG file")
    _, (positions, _scale, alpha, _raw) = loaders[path.suffix.lower()](path)
    # These shared decoders return FILE coordinates, unlike clean_export's
    # loader. Match that loader and viewer.html with exactly one x180 flip.
    positions = positions * [1, -1, -1]
    return estimate_cloud_up(positions, alpha, candidates, alpha_min)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("subject", nargs="?", help="subject directory containing the solve")
    parser.add_argument("sparse", nargs="?", default="sparse", help="model directory (default: sparse)")
    parser.add_argument("--axis", choices=AXES, default="-y", help="camera up axis (default: -y)")
    parser.add_argument("--from-cloud", type=pathlib.Path, help="estimate gravity from a PLY or SOG v2 cloud")
    parser.add_argument("--alpha-min", type=float, default=0.2, help="cloud opacity floor (default: 0.2)")
    # argparse treats -x/-y as options; preserve the documented '--axis -y' spelling.
    argv = list(sys.argv[1:] if argv is None else argv)
    for i in range(len(argv) - 1):
        if argv[i] == "--axis" and argv[i + 1] in AXES:
            argv[i:i + 2] = ["--axis=" + argv[i + 1]]
            break
    args = parser.parse_args(argv)
    if not args.subject and not args.from_cloud:
        parser.error("provide a subject directory or --from-cloud")
    if not 0 <= args.alpha_min < 1:
        parser.error("--alpha-min must be finite and in [0, 1)")
    return args


def camera_estimate(args):
    import numpy as np
    import pycolmap

    subject = pathlib.Path(args.subject).expanduser()
    sparse = subject / args.sparse
    sub = sparse / "0" if (sparse / "0").is_dir() else sparse
    rec = pycolmap.Reconstruction(sub)
    Rs = [im.cam_from_world().rotation.matrix() for im in rec.images.values()]  # world -> camera
    # Which camera axis pointed up depends on how the phone was held: -y for
    # landscape frames (COLMAP's camera y points down), +/-x for portrait
    # frames stored sideways with an EXIF rotation. Try the four candidates
    # and keep the one the views agree on most.
    # Default to -y: it is the axis that reproduced the cannon's hand-measured,
    # visually levelled up vector. The other three are printed so a scene that
    # renders tilted can be re-run with --axis; the lowest spread is NOT a
    # reliable pick (it chose -x for the cannon, which the gallery disproves).
    axis_name = args.axis
    ups = np.array([R.T @ np.array(AXES[axis_name], dtype=float) for R in Rs])
    up = ups.mean(axis=0)
    up /= np.linalg.norm(up)
    spread = float(np.degrees(np.mean([np.arccos(np.clip(u @ up, -1, 1)) for u in ups])))
    # Where the cameras converge: the least-squares point closest to every
    # optical axis. For an orbit around a subject that is the subject, and the
    # mean camera distance to it is the orbit radius, both in the solve's own
    # (arbitrary) units - a far better crop centre than the splat mass, which
    # for a building on a lawn sits in the lawn.
    A = np.zeros((3, 3))
    b = np.zeros(3)
    centers = []
    for im in rec.images.values():
        pose = im.cam_from_world()
        R = pose.rotation.matrix()
        c = -R.T @ pose.translation
        d = R.T @ np.array([0.0, 0.0, 1.0])
        d /= np.linalg.norm(d)
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ c
        centers.append(c)
    focus = np.linalg.solve(A, b)
    orbit = float(np.mean([np.linalg.norm(c - focus) for c in centers]))
    alt = {n: None for n in ("-y", "+y", "-x", "+x")}
    for name, axis in AXES.items():
        u = np.array([R.T @ np.array(axis, dtype=float) for R in Rs]).mean(axis=0)
        u /= np.linalg.norm(u)
        alt[name] = [round(float(v), 4) for v in (u[0], -u[1], -u[2])]
    viewer = np.array([up[0], -up[1], -up[2]])
    return {"subject": subject.name, "views": len(Rs), "camera_up_axis": axis_name,
                      "spread_deg": round(spread, 1), "up": [round(float(v), 4) for v in viewer],
                      "up_by_axis": alt,
                      "focus": [round(float(v), 3) for v in focus], "orbit_radius": round(orbit, 3)}


def main(argv=None):
    import zipfile

    args = parse_args(argv)
    result = {"subject": args.from_cloud.stem if args.from_cloud else None, "views": 0,
              "camera_up_axis": args.axis, "spread_deg": None, "up": None,
              "up_by_axis": dict.fromkeys(AXES), "focus": None, "orbit_radius": None}
    if args.subject:
        result = camera_estimate(args)
    result["frame"] = FRAME
    if args.from_cloud:
        try:
            result["from_cloud"] = cloud_estimate(args.from_cloud, result["up_by_axis"], args.alpha_min)
        except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError, ImportError, zipfile.BadZipFile) as exc:
            print(f"scene_up: {exc}", file=sys.stderr)
            return 1
        if not args.subject:
            result["up"] = result["from_cloud"]["up"]
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
