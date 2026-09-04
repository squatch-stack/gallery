"""splat-brush: train a Gaussian splat with Brush from a COLMAP solve, using
the full-resolution photos already verified on the host.

Contract, in the shape of hdc-holo's bench/RECIPE.md (the prior art the
host's own docs/splat/07-job-contract.md adopted). The host's triage of the
request (report 20260904-65465a) found two defects in the first version of
this file, both fixed here: names.txt was used as a path without a boundary
check (path traversal through pathlib's absolute-operand rule), and a
max_res of 0 was documented as "native" while it actually selected Brush's
own default of 1920. There is no sentinel now: --max_res is always passed to
Brush explicitly, and native means naming the photos' real long side.

  Command     : python3 splat_brush_job.py --max_res 5712 [--steps 30000]
                [--max_splats 1500000] [--seed 42] [--photos_dir <host path>]
  Environment : the Brush CLI (brush-cli 0.3.0) already verified on this host
                (docs/splat/17-brush-alternative.md), found via $BRUSH; the
                run refuses to start if that path is not a file. Python
                stdlib only - nothing to install, no torch.
  Input       : scene.tar in the working directory (tens of MB), containing
                <root>/sparse/0/{cameras,images,points3D}.bin (a COLMAP solve
                computed on the Mac at native resolution) and <root>/names.txt
                (one registered image filename per line). Photos are NOT
                uploaded: they are read by name from --photos_dir, the host's
                verified copy of the capture. names.txt is untrusted input:
                each line must be a bare filename, and the resolved path must
                stay under the resolved photos directory. The tar MAY instead
                carry <root>/images/ itself, for scenes the host does not
                hold; then --photos_dir is ignored. A tar holding more than
                one sparse/ is refused rather than silently picked from.
  Artifacts   : point_cloud.ply (standard 3DGS PLY), metrics.json
                (steps, splat count, wall seconds, resolution, sha256 of the
                PLY, the exact argv), train.log (Brush output).
  Budgets     : VRAM at native (5712) is unmeasured; --max_res 2856 and 1920
                are the fallbacks. Wall clock: the manifest declares 1800 s
                under a 3600 s broker ceiling. The only prior timing on this
                card (10k steps in 103 s) was 42 synthetic views at 25k
                gaussians, a LOWER bound from a scene sixty times smaller;
                the first job measures seconds_per_1k_steps at real scale.
  Isolation   : reads ./scene.tar and --photos_dir (read-only), writes ./;
                no network, no HOME, GPU only.
  Verification: the same solve with the same --steps/--seed/--max_res trains
                on the submitting Mac (Brush v0.3.0, Metal). Seed passed
                explicitly on both sides; agreement means final splat count
                within a few percent (Metal vs Vulkan reduce floats in a
                different order and densify/prune are threshold tests on
                those sums, so exact equality is the wrong test), the PLY
                loading in both viewers, no qualitative difference in the
                subject. Same result first, then compare clocks.
"""
import argparse
import hashlib
import json
import os
import pathlib
import resource
import shutil
import subprocess
import sys
import tarfile
import time


def brush_binary():
    b = os.environ.get("BRUSH") or shutil.which("brush_app")
    if not b or not pathlib.Path(b).is_file():
        sys.exit("BRUSH must name the host's Brush CLI binary; got %r" % b)
    return b


def scene_root(where):
    roots = sorted({d.parent for d in pathlib.Path(where).rglob("sparse")
                    if (d / "0").is_dir() or (d / "cameras.bin").is_file()})
    if not roots:
        sys.exit("scene.tar holds no sparse/ directory")
    if len(roots) > 1:
        sys.exit("scene.tar holds %d scenes (%s); send one" % (len(roots), roots))
    return roots[0]


def link_photos(root, photos_dir):
    """Populate root/images/ from the host's photo directory, by name.

    names.txt is client-supplied. A line is accepted only if it is a single
    path component (no separators, not . or .., no leading -), and only if
    the resolved target lies under the resolved photo directory - the second
    check matters when the photo directory is itself a symlink.
    """
    src = pathlib.Path(photos_dir).resolve()
    if not src.is_dir():
        sys.exit("--photos_dir %s is not a directory" % photos_dir)
    lines = [n.strip() for n in (root / "names.txt").read_text().splitlines()]
    names = sorted({n for n in lines if n})
    dst = root / "images"
    dst.mkdir(exist_ok=True)
    rejected, missing, linked = [], [], 0
    for n in names:
        if n != os.path.basename(n) or n in (".", "..") or n.startswith("-"):
            rejected.append(n)
            continue
        p = (src / n).resolve()
        if p.parent != src or not p.is_file():
            if p.parent != src:
                rejected.append(n)
            else:
                missing.append(n)
            continue
        os.symlink(p, dst / n)
        linked += 1
    if rejected:
        sys.exit("%d name(s) in names.txt are not bare filenames inside %s, e.g. %s"
                 % (len(rejected), src, rejected[:3]))
    if missing:
        sys.exit("%d of %d named photos absent from %s, e.g. %s"
                 % (len(missing), len(names), src, missing[:3]))
    return linked


def splat_count(ply):
    with open(ply, "rb") as f:
        for line in f:
            if line.startswith(b"element vertex"):
                return int(line.split()[2])
            if line.strip() == b"end_header":
                break
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=30000)
    ap.add_argument("--max_splats", type=int, default=1_500_000)
    ap.add_argument("--max_res", type=int, required=True,
                    help="longest image side Brush trains at; name the real "
                         "value (5712 for this capture) - there is no 'native' sentinel")
    ap.add_argument("--seed", type=int, default=42,
                    help="Brush's own default; both lanes must pass the same value")
    ap.add_argument("--photos_dir", default=os.environ.get("PHOTOS_DIR", ""),
                    help="host directory holding the full-resolution photos")
    a = ap.parse_args()
    if a.max_res <= 0:
        sys.exit("--max_res must be a positive pixel count; 0 is not 'native', "
                 "it would fall through to Brush's default of 1920")
    brush = brush_binary()

    work = pathlib.Path.cwd()
    with tarfile.open(work / "scene.tar") as t:
        t.extractall(work / "scene", filter="data")
    root = scene_root(work / "scene")

    if (root / "images").is_dir() and any((root / "images").iterdir()):
        n_images = len(list((root / "images").iterdir()))
        photos = "uploaded"
    elif a.photos_dir:
        n_images = link_photos(root, a.photos_dir)
        photos = a.photos_dir
    else:
        sys.exit("scene.tar carries no images/ and no --photos_dir was given")

    argv = [brush, str(root),
            "--total-steps", str(a.steps),
            "--max-splats", str(a.max_splats),
            "--max-resolution", str(a.max_res),
            "--seed", str(a.seed),
            "--export-every", str(a.steps),
            "--export-path", str(work),
            "--export-name", "point_cloud.ply"]
    t0 = time.time()
    with open(work / "train.log", "wb") as log:
        rc = subprocess.call(argv, stdout=log, stderr=subprocess.STDOUT)
    wall = time.time() - t0

    ply = work / "point_cloud.ply"
    if not ply.is_file():
        # --export-name defaults to a template with an iteration placeholder;
        # whether a literal name is honoured on the final step is unverified.
        candidates = sorted(work.rglob("*.ply"), key=lambda q: q.stat().st_mtime)
        candidates = [c for c in candidates if "scene" not in c.parts]
        if candidates:
            print("point_cloud.ply absent; adopting newest export", candidates[-1])
            shutil.copy2(candidates[-1], ply)
    rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # ru_maxrss is kilobytes on Linux and bytes on macOS.
    peak_rss_mb = round(rss_kb / (1048576 if sys.platform == "darwin" else 1024), 1)
    metrics = {"recipe": "splat-brush", "rc": rc, "wall_seconds": round(wall, 1),
               "seconds_per_1k_steps": round(wall / max(a.steps, 1) * 1000, 2),
               "peak_rss_mb": peak_rss_mb,
               "steps": a.steps, "max_splats": a.max_splats, "max_res": a.max_res,
               "seed": a.seed, "images": n_images, "photos": photos, "argv": argv}
    if ply.is_file():
        metrics["splats"] = splat_count(ply)
        metrics["ply_bytes"] = ply.stat().st_size
        metrics["ply_sha256"] = hashlib.sha256(ply.read_bytes()).hexdigest()
    (work / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))
    if rc != 0 or not ply.is_file():
        sys.exit("brush failed (rc=%s); see train.log" % rc)


if __name__ == "__main__":
    main()
