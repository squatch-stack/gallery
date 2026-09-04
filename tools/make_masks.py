"""Semantic masks that tell a splat trainer which pixels to ignore.

    .venv-masks/bin/python tools/make_masks.py <subject> [--drop grass,tree,plant,sky,field,flower]
                                                [--keep-classes ...] [--side 1024] [--polarity white-keep]

Runs Mask2Former (Swin-L, ADE20K semantic) on every registered image of a
photo subject and writes <subject>/masks/<image>.png, a 1-bit mask at the
image's full resolution. Pixels of the dropped classes are the ones the
trainer should ignore: wind-blown grass and foliage change between photographs
and a photometric loss explains that motion with long thin splats; masking the
vegetation out of the loss removes the incentive.

Polarity follows Brush 0.3.0 (white = train on this pixel, black = ignore);
pass --polarity black-keep for the opposite convention. Inference runs at
--side pixels on the long edge (default 1024) and the label map is upsampled
with nearest-neighbour to the image size, then eroded/dilated slightly so a
mask edge never bites into the subject.

ADE20K ids used by default: sky 2, tree 4, grass 9, plant 17, field 29,
flower 66, palm 72. Everything else (earth, road, rock, wall, the subject) is
kept. Use --keep-classes to protect a class that is the subject (the oak: keep
tree, drop grass and sky).
"""
import argparse
import pathlib
import time

import numpy as np
import torch
from PIL import Image, ImageFilter, ImageOps
from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation

ADE = {"wall": 0, "building": 1, "sky": 2, "floor": 3, "tree": 4, "ceiling": 5, "road": 6,
       "grass": 9, "sidewalk": 11, "person": 12, "earth": 13, "mountain": 16, "plant": 17,
       "water": 21, "rock": 34, "field": 29, "sand": 46, "path": 52, "flower": 66, "palm": 72,
       "car": 20, "fence": 32}
MODEL = "facebook/mask2former-swin-large-ade-semantic"


# EXIF orientation -> the transpose that undoes exif_transpose(), so a mask made
# on the upright image can be put back onto the raw pixel grid that COLMAP and
# Brush read (neither applies EXIF orientation).
UNDO = {2: Image.FLIP_LEFT_RIGHT, 3: Image.ROTATE_180, 4: Image.FLIP_TOP_BOTTOM,
        5: Image.TRANSPOSE, 6: Image.ROTATE_90, 7: Image.TRANSVERSE, 8: Image.ROTATE_270}


def registered_names(subject):
    names = subject / "names.txt"
    if names.is_file():
        return [n.strip() for n in names.read_text().splitlines() if n.strip()]
    return sorted(p.name for p in (subject / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", type=pathlib.Path)
    ap.add_argument("--drop", default="grass,tree,plant,sky,field,flower,palm")
    ap.add_argument("--keep-classes", default="")
    ap.add_argument("--side", type=int, default=1024)
    ap.add_argument("--polarity", choices=["white-keep", "black-keep"], default="white-keep")
    ap.add_argument("--grow", type=int, default=6, help="pixels to grow the ignored region at full res")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    drop = {ADE[c] for c in a.drop.split(",") if c} - {ADE[c] for c in a.keep_classes.split(",") if c}
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print("device", device, "drop ids", sorted(drop), flush=True)
    proc = AutoImageProcessor.from_pretrained(MODEL)
    model = Mask2FormerForUniversalSegmentation.from_pretrained(MODEL).to(device).eval()

    out = a.subject / "masks"
    out.mkdir(exist_ok=True)
    names = registered_names(a.subject)
    if a.limit:
        names = names[:a.limit]
    t0 = time.time()
    stats = []
    for i, n in enumerate(names, 1):
        src = a.subject / "images" / n
        raw = Image.open(src)
        orient = raw.getexif().get(0x0112, 1)
        W, H = raw.size                       # raw pixel grid, as COLMAP sees it
        img = ImageOps.exif_transpose(raw).convert("RGB")   # upright, for the model
        uw, uh = img.size
        s = a.side / max(uw, uh)
        small = img.resize((round(uw * s), round(uh * s)), Image.BILINEAR)
        inputs = proc(images=small, return_tensors="pt").to(device)
        with torch.no_grad():
            outp = model(**inputs)
        seg = proc.post_process_semantic_segmentation(outp, target_sizes=[small.size[::-1]])[0].cpu().numpy()
        ignore = np.isin(seg, list(drop))
        m = Image.fromarray((~ignore).astype(np.uint8) * 255).resize((uw, uh), Image.NEAREST)
        if orient in UNDO:                    # back onto the raw grid
            m = m.transpose(UNDO[orient])
        assert m.size == (W, H), (m.size, (W, H))
        ids, counts = np.unique(seg, return_counts=True)
        top = sorted(zip(counts, ids, strict=True), reverse=True)[:3]
        top_s = " ".join(f"{model.config.id2label[int(i)]}:{c/seg.size*100:.0f}%" for c, i in top)
        if a.grow > 0:  # grow the ignored region: erode the keep region
            m = m.filter(ImageFilter.MinFilter(2 * a.grow + 1))
        if a.polarity == "black-keep":
            m = Image.eval(m, lambda v: 255 - v)
        m = m.convert("1")
        m.save(out / (pathlib.Path(n).stem + ".png"))
        frac = float(ignore.mean())
        stats.append(frac)
        print(f"[{i}/{len(names)}] {n} orient {orient} ignored {frac*100:5.1f}%  top: {top_s}  "
              f"({time.time()-t0:.0f}s)", flush=True)
    print(f"done: {len(names)} masks -> {out}; mean ignored {np.mean(stats)*100:.1f}%, "
          f"min {np.min(stats)*100:.1f}%, max {np.max(stats)*100:.1f}%", flush=True)


if __name__ == "__main__":
    main()
