#!/usr/bin/env python3
"""Solve camera poses for one photo subject with pycolmap.

    ~/.venvs/photogram/bin/python tools/solve_subject.py \
        ~/Documents/squatch-captures/photo-subjects/cannon [--global]

Writes <subject>/sparse/0 as a binary COLMAP model and prints the field
check that decides whether the capture solved: registered-image count and
mean reprojection error. EXIF supplies the focal-length prior; images are
capped for feature extraction so a 48 MP still does not become the slow
step.

Knobs, learned on the Brookline oak (169 views, a thin walk-around whose
match graph was connected but which the default incremental mapper broke
into four fragments):

  --global      global structure-from-motion (GLOMAP) instead of the
                incremental mapper: it solves the whole view graph at once
                and does not snap a thin chain at its weakest link.
  --relaxed     incremental mapper with lower registration thresholds
                (min matches 8, abs-pose inliers 15 / ratio 0.15).
  --features N  SIFT features per image (default 8192); more features turn
                weak pairs into verified ones on foliage and bark.
  --image-size N  long side used for extraction (default 2400).
  --guided      guided matching: a second matching pass constrained by the
                estimated two-view geometry, more inliers per pair.
  --sequential  match time-ordered neighbours (overlap 15) instead of all
                O(n^2) pairs; only a speed option, it cannot find pairs
                exhaustive matching would not.
  --remap       reuse the existing database (skip extraction and matching)
                and only run the mapper: minutes instead of tens of minutes
                when the question is the mapper, not the matches.
  --out DIR     write the model under <subject>/DIR (default sparse).
"""

import argparse
import time
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("subject", help="subject directory containing images/")
    parser.add_argument("--global", dest="global_mapping", action="store_true", help="use the global mapper")
    parser.add_argument("--relaxed", action="store_true", help="lower incremental registration thresholds")
    parser.add_argument("--features", type=int, default=8192, help="SIFT features per image (default: 8192)")
    parser.add_argument("--image-size", type=int, default=2400, help="extraction long side (default: 2400)")
    parser.add_argument("--guided", action="store_true", help="enable guided matching")
    parser.add_argument("--sequential", action="store_true", help="match neighbours with overlap 15")
    parser.add_argument("--remap", action="store_true", help="reuse database; skip extraction and matching")
    parser.add_argument("--out", default="sparse", help="model directory below subject (default: sparse)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    import pycolmap

    subject = Path(args.subject).expanduser()
    images = subject / "images"
    db = subject / "database.db"
    sparse = subject / args.out
    sparse.mkdir(exist_ok=True)

    t0 = time.time()
    if not args.remap:
        if db.exists():
            db.unlink()
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = "SIMPLE_RADIAL"
        sift = pycolmap.FeatureExtractionOptions()
        sift.max_image_size = args.image_size
        sift.sift.max_num_features = args.features
        pycolmap.extract_features(db, images, reader_options=reader,
                                  extraction_options=sift)
        print(f"features: {time.time() - t0:.0f}s", flush=True)

        t1 = time.time()
        matching = pycolmap.FeatureMatchingOptions()
        matching.guided_matching = bool(args.guided)
        if args.sequential:
            options = pycolmap.SequentialPairingOptions()
            options.overlap = 15
            pycolmap.match_sequential(db, pairing_options=options,
                                      matching_options=matching)
        else:
            pycolmap.match_exhaustive(db, matching_options=matching)
        print(f"matching: {time.time() - t1:.0f}s", flush=True)

    t2 = time.time()
    if args.global_mapping:
        result = pycolmap.global_mapping(db, images, sparse)
        if isinstance(result, pycolmap.Reconstruction):
            (sparse / "0").mkdir(exist_ok=True)
            result.write_binary(sparse / "0")
            maps = {0: result}
        elif isinstance(result, dict):
            maps = result
        else:
            maps = {d.name: pycolmap.Reconstruction(d) for d in sorted(sparse.iterdir())
                    if (d / "cameras.bin").is_file()}
        how = "global"
    else:
        opt = pycolmap.IncrementalPipelineOptions()
        if args.relaxed:
            opt.min_num_matches = 8
            opt.mapper.abs_pose_min_num_inliers = 15
            opt.mapper.abs_pose_min_inlier_ratio = 0.15
            opt.mapper.init_min_num_inliers = 50
            opt.mapper.max_reg_trials = 5
        maps = pycolmap.incremental_mapping(db, images, sparse, options=opt)
        how = "relaxed incremental" if args.relaxed else "incremental"
    print(f"mapping ({how}): {time.time() - t2:.0f}s, {len(maps)} model(s)", flush=True)

    total = len(list(images.iterdir()))
    for idx, rec in maps.items():
        err = rec.compute_mean_reprojection_error()
        print(f"  model {idx}: {rec.num_reg_images()}/{total} images registered, "
              f"{rec.num_points3D():,} points, "
              f"mean reprojection error {err:.2f} px", flush=True)
    print(f"total {time.time() - t0:.0f}s -> {sparse}", flush=True)


if __name__ == "__main__":
    main()
