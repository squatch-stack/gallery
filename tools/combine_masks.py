"""Keep a subject whole and add a bounded patch of ground on its raw pixel grid.

Directories are relative to <subject>. Pixel centres use integer coordinates;
the anchor is ((xmin + xmax) / 2, ymax), and height is ymax - ymin + 1.
Feathering ramps inward from the disc edge, never beyond the requested radius.
Existing ground-mask edges also ramp inward (8-neighbour pixel distance).
No EXIF orientation is applied and no degenerate views are dropped here.
"""

import argparse
import math
from pathlib import Path


def combine(subject, ground, radius, feather=0):
    """Return uint8 alpha; input arrays are binary and already on the same grid."""
    import numpy as np
    from PIL import Image, ImageFilter

    alpha = np.zeros(subject.shape, dtype=np.uint8)
    ys, xs = np.nonzero(subject)
    if len(xs):
        height = int(ys.max() - ys.min() + 1)
        cx, cy = (int(xs.min()) + int(xs.max())) / 2, int(ys.max())
        y, x = np.ogrid[:subject.shape[0], :subject.shape[1]]
        inset = radius * height - np.hypot(x - cx, y - cy)
        if feather:
            strength = np.clip(inset / feather, 0, 1)
            # Erosion shells give a linear ramp at the original ground edges.
            # Image borders replicate: a full-frame ground mask has no edge
            # within the photograph, so only the disc boundary is feathered.
            remaining = Image.fromarray(ground.astype(np.uint8) * 255)
            distance = ground.astype(np.float64)
            for _ in range(math.ceil(feather) - 1):
                remaining = remaining.filter(ImageFilter.MinFilter(3))
                distance += np.asarray(remaining) > 0
            strength = np.minimum(strength, np.clip(distance / feather, 0, 1))
            alpha = np.rint(255 * strength).astype(np.uint8)
            alpha[~ground] = 0
        else:
            alpha[ground & (inset >= 0)] = 255
    alpha[subject] = 255
    return alpha


def pngs(directory):
    return {p.stem: p for p in sorted(directory.iterdir()) if p.is_file() and p.suffix.lower() == ".png"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("subject", type=Path)
    ap.add_argument("--subject-masks", required=True, type=Path)
    ap.add_argument("--ground-masks", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--radius", required=True, type=float, help="disc radius as a fraction of subject bbox height")
    ap.add_argument("--feather", type=float, default=0, help="inward linear ground feather width in pixels")
    a = ap.parse_args(argv)
    for name in ("radius", "feather"):
        if not math.isfinite(getattr(a, name)) or getattr(a, name) < 0:
            ap.error(f"--{name} must be finite and nonnegative")
    subject_dir, ground_dir, out = (a.subject / p for p in (a.subject_masks, a.ground_masks, a.out))
    for directory in (subject_dir, ground_dir):
        if not directory.is_dir():
            ap.error(f"mask directory does not exist: {directory}")
    if out.resolve() in (subject_dir.resolve(), ground_dir.resolve()):
        ap.error("--out must differ from both input mask directories")

    import numpy as np
    from PIL import Image

    subjects, grounds = pngs(subject_dir), pngs(ground_dir)
    stems = set(subjects) | set(grounds)
    names = a.subject / "names.txt"
    if names.is_file():
        stems.update(Path(n.strip()).stem for n in names.read_text().splitlines() if n.strip())
    elif (a.subject / "images").is_dir():
        stems.update(p.stem for p in (a.subject / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    out.mkdir(parents=True, exist_ok=True)
    missing_subject = missing_ground = skipped = written = suspicious = 0
    sums = np.zeros(4)
    pixels = np.zeros(4)
    total_pixels = 0
    for stem in sorted(stems):
        if stem not in subjects or stem not in grounds:
            missing_subject += stem not in subjects
            missing_ground += stem not in grounds
            skipped += 1
            print(f"{stem}: skipped (subject missing={stem not in subjects}, ground missing={stem not in grounds})")
            continue
        with Image.open(subjects[stem]) as im:
            subject = np.asarray(im.convert("L")) > 0
            size = im.size
        with Image.open(grounds[stem]) as im:
            ground = np.asarray(im.convert("L").resize(size, Image.Resampling.NEAREST)) > 0
        coverage = float(subject.mean())
        # Diagnostic only: payload owns its configurable degeneracy/drop rule.
        if subject.all() or coverage < 0.01:
            suspicious += 1
            tag = "fully white" if subject.all() else "nearly empty (<1%)"
            print(f"{stem}: WARNING subject {tag}; retained, review before payload filtering")
        alpha = combine(subject, ground, a.radius, a.feather)
        Image.fromarray(alpha).save(out / f"{stem}.png")
        kept = alpha.astype(np.float64) / 255
        kept[subject] = 0
        counts = np.array([subject.sum(), kept.sum(), subject.sum() + kept.sum(), ground.sum()])
        fractions = counts / subject.size
        sums += fractions
        pixels += counts
        total_pixels += subject.size
        written += 1
        print(f"{stem}: subject {fractions[0]:.2%}, kept ground {fractions[1]:.2%}, "
              f"union {fractions[2]:.2%}, input ground {fractions[3]:.2%}")
    print(f"totals: written={written}, skipped={skipped}, missing subject={missing_subject}, "
          f"missing ground={missing_ground}, suspicious subject={suspicious}")
    for label, values in (("mean per-view", sums / max(written, 1)),
                          ("pixel-weighted", pixels / max(total_pixels, 1))):
        print(f"{label} alpha coverage: subject {values[0]:.2%}, kept ground {values[1]:.2%}, "
              f"union {values[2]:.2%}, input ground {values[3]:.2%}")


if __name__ == "__main__":
    main()
