"""Subject masks: keep only the named object, ignore everything else.

    .venv-masks/bin/python tools/make_subject_masks.py <subject> --prompt "cannon" \
        [--side 1024] [--box-threshold 0.3] [--text-threshold 0.25] [--grow 8] [--limit N]

Class-based masking failed on the Brookline cannon because the gun is painted
olive green and ADE20K calls it "plant"; a green subject on a green lawn cannot
be isolated by dropping vegetation classes. This does the inverse: Grounding
DINO finds the object named in --prompt (zero-shot, text-conditioned boxes),
SAM 2 turns each box into a pixel mask, and the union of those masks is the
region the trainer supervises. Everything else is ignored, which is the
"subject isolation" a buyer expects and removes wind-blown grass from the loss
at the source. Ground contact is lost with it: the trainer sees a floating
subject, which is what the gallery's subject crop shows anyway.

Writes <subject>/masks/<stem>.png on the RAW pixel grid (COLMAP and Brush do
not apply EXIF orientation), white = train on this pixel. An image where the
prompt is not found gets an all-white mask and a warning, so a miss never
silently deletes a view from training.
"""
import argparse
import pathlib
import time

import numpy as np
import torch
from PIL import Image, ImageFilter, ImageOps

DINO = "IDEA-Research/grounding-dino-base"
SAM2 = "facebook/sam2.1-hiera-large"
SAM1 = "facebook/sam-vit-large"
UNDO = {2: Image.FLIP_LEFT_RIGHT, 3: Image.ROTATE_180, 4: Image.FLIP_TOP_BOTTOM,
        5: Image.TRANSPOSE, 6: Image.ROTATE_90, 7: Image.TRANSVERSE, 8: Image.ROTATE_270}


def load_sam(device):
    import transformers
    if hasattr(transformers, "Sam2Model"):
        proc = transformers.Sam2Processor.from_pretrained(SAM2)
        model = transformers.Sam2Model.from_pretrained(SAM2).to(device).eval()
        return "sam2", proc, model
    proc = transformers.SamProcessor.from_pretrained(SAM1)
    model = transformers.SamModel.from_pretrained(SAM1).to(device).eval()
    return "sam1", proc, model


def registered_names(subject):
    names = subject / "names.txt"
    if names.is_file():
        return [n.strip() for n in names.read_text().splitlines() if n.strip()]
    return sorted(p.name for p in (subject / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", type=pathlib.Path)
    ap.add_argument("--prompt", required=True, help='e.g. "cannon" or "stone building"')
    ap.add_argument("--side", type=int, default=1024)
    ap.add_argument("--box-threshold", type=float, default=0.3)
    ap.add_argument("--text-threshold", type=float, default=0.25)
    ap.add_argument("--grow", type=int, default=8, help="pixels to grow the kept region at full res")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-boxes", type=int, default=3)
    a = ap.parse_args()

    from transformers import GroundingDinoForObjectDetection, GroundingDinoProcessor
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dproc = GroundingDinoProcessor.from_pretrained(DINO)
    dino = GroundingDinoForObjectDetection.from_pretrained(DINO).to(device).eval()
    sam_kind, sproc, sam = load_sam(device)
    print("device", device, "sam", sam_kind, "prompt", repr(a.prompt), flush=True)

    out = a.subject / "masks"
    out.mkdir(exist_ok=True)
    names = registered_names(a.subject)
    if a.limit:
        names = names[:a.limit]
    text = a.prompt.strip().rstrip(".") + "."
    t0 = time.time()
    kept = []
    for i, n in enumerate(names, 1):
        raw = Image.open(a.subject / "images" / n)
        orient = raw.getexif().get(0x0112, 1)
        W, H = raw.size
        img = ImageOps.exif_transpose(raw).convert("RGB")
        uw, uh = img.size
        s = a.side / max(uw, uh)
        small = img.resize((round(uw * s), round(uh * s)), Image.BILINEAR)

        inputs = dproc(images=small, text=text, return_tensors="pt").to(device)
        with torch.no_grad():
            dout = dino(**inputs)
        res = dproc.post_process_grounded_object_detection(
            dout, inputs.input_ids, threshold=a.box_threshold, text_threshold=a.text_threshold,
            target_sizes=[small.size[::-1]])[0]
        boxes = res["boxes"].cpu()
        scores = res["scores"].cpu()
        order = torch.argsort(scores, descending=True)[: a.max_boxes]
        boxes = boxes[order]
        keep = np.zeros((small.size[1], small.size[0]), dtype=bool)
        if len(boxes):
            sin = sproc(images=small, input_boxes=[[b.tolist() for b in boxes]], return_tensors="pt").to(device)
            with torch.no_grad():
                sout = sam(**sin, multimask_output=False)
            if sam_kind == "sam2":
                masks = sproc.post_process_masks(sout.pred_masks.cpu(), sin["original_sizes"].cpu())[0]
            else:
                masks = sproc.post_process_masks(sout.pred_masks.cpu(), sin["original_sizes"].cpu(),
                                                 sin["reshaped_input_sizes"].cpu())[0]
            m = masks.numpy()
            keep = m.reshape(-1, m.shape[-2], m.shape[-1]).any(axis=0)
            tag = " ".join(f"{float(sc):.2f}" for sc in scores[order])
        else:
            keep[:] = True
            tag = "NO DETECTION - kept everything"
        mimg = Image.fromarray(keep.astype(np.uint8) * 255).resize((uw, uh), Image.NEAREST)
        if orient in UNDO:
            mimg = mimg.transpose(UNDO[orient])
        assert mimg.size == (W, H), (mimg.size, (W, H))
        if a.grow > 0:
            mimg = mimg.filter(ImageFilter.MaxFilter(2 * a.grow + 1))
        mimg.convert("1").save(out / (pathlib.Path(n).stem + ".png"))
        frac = float(keep.mean())
        kept.append(frac)
        print(f"[{i}/{len(names)}] {n} kept {frac*100:5.1f}%  boxes {len(boxes)} [{tag}]  "
              f"({time.time()-t0:.0f}s)", flush=True)
    print(f"done: {len(names)} masks -> {out}; mean kept {np.mean(kept)*100:.1f}%, "
          f"min {np.min(kept)*100:.1f}%, max {np.max(kept)*100:.1f}%", flush=True)


if __name__ == "__main__":
    main()
