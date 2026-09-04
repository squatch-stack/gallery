"""Package a photo subject as a scene.tar for the gpugate `splat-brush` recipe.

    ~/.venvs/photogram/bin/python tools/make_scene_payload.py \
        ~/Documents/squatch-captures/photo-subjects/cannon out/cannon-scene.tar

By default the tar carries only the COLMAP solve (sparse/0/*.bin, native
resolution) and names.txt, the registered image filenames: the 5090 host
reads the full-resolution photos by name from its own verified copy of the
capture, so nothing large travels. Pass --embed-images to ship the photos
too (for a scene the host does not hold), optionally downscaled with
--max-side; the camera intrinsics are rescaled to match.

Masks (from tools/make_subject_masks.py) can travel two ways, with opposite
meanings in Brush: --masks ships a masks/ folder, which zeroes the loss
outside the subject ("don't care") and let stray splats grow into needles on
the cannon; --embed-images --alpha-from-masks writes RGBA PNGs whose alpha
IS the mask, which Brush matches as a target ("render nothing here") and
which produced a clean isolated subject. Use the alpha form. For host-side
compositing, --masks --alpha-from-masks filters degenerate views but keeps
the original names and resolution; submit with alpha_from_masks=true.

--model picks a sub-model when COLMAP fragmented the solve; the default is
the one with the most registered images, and the choice is printed.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

import pycolmap


def pick_model(sparse, wanted):
    models = {}
    for d in sorted(sparse.iterdir()):
        if (d / "cameras.bin").is_file():
            models[d.name] = pycolmap.Reconstruction(d)
    if not models:
        sys.exit("no COLMAP model under %s" % sparse)
    if wanted is not None:
        if str(wanted) not in models:
            sys.exit("no sub-model %s; have %s" % (wanted, sorted(models)))
        return str(wanted), models[str(wanted)]
    name = max(models, key=lambda k: models[k].num_images())
    if len(models) > 1:
        sizes = {k: v.num_images() for k, v in models.items()}
        print("fragmented solve %s; shipping the largest, model %s" % (sizes, name))
    return name, models[name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", type=pathlib.Path, help="dir with images/ and sparse/")
    ap.add_argument("out", type=pathlib.Path, help="scene.tar to write")
    ap.add_argument("--limit", type=int, help="keep at most N evenly spaced registered views (minimum 8)")
    ap.add_argument("--model", type=int, default=None)
    ap.add_argument("--sparse", default="sparse",
                    help="solve directory under the subject (e.g. sparse-global)")
    ap.add_argument("--embed-images", action="store_true")
    ap.add_argument("--masks", action="store_true",
                    help="include <subject>/masks/<stem>.png for each registered image "
                         "(Brush: white = train on this pixel, black = ignore)")
    ap.add_argument("--max-side", type=int, default=0,
                    help="with --embed-images: downscale so the long side is this")
    ap.add_argument("--alpha-from-masks", action="store_true",
                    help="filter degenerate masks; with --embed-images write RGBA PNGs whose alpha is the mask. "
                         "Brush then MATCHES transparency outside the subject (match_alpha_weight) "
                         "instead of ignoring those pixels, which is what isolates a subject; a "
                         "masks/ folder only zeroes the loss there and lets stray splats grow free")
    ap.add_argument("--min-coverage", type=float, default=0.01,
                    help="with --alpha-from-masks: drop views whose mask covers less than this "
                         "fraction of the frame (a segmenter miss); fully opaque masks are always dropped")
    a = ap.parse_args()

    if a.limit is not None and a.limit < 8:
        ap.error("--limit must be at least 8")

    name, rec = pick_model(a.subject / a.sparse, a.model)
    names = sorted(im.name for im in rec.images.values())
    cam = next(iter(rec.cameras.values()))
    print("model %s: %d registered images, %d points, camera %dx%d"
          % (name, len(names), rec.num_points3D(), cam.width, cam.height))

    if a.alpha_from_masks:
        # A mask the segmenter gave up on comes back fully white ("supervise
        # everything"). Under masks/ that fallback is merely conservative;
        # as an alpha TARGET it inverts, asking the model to render the whole
        # frame, grass included, against every other view saying render
        # nothing there. Drop such views from the solve rather than ship them.
        # The mirror failure is a mask that found almost nothing: as an alpha
        # target it asks for an empty frame where the subject is in view.
        from PIL import Image
        import numpy as np
        bad = {}
        for n in names:
            m = np.asarray(Image.open(a.subject / "masks" / (pathlib.Path(n).stem + ".png")).convert("L"))
            cov = float((m > 0).mean())
            if cov >= 0.995 or cov < a.min_coverage:
                bad[n] = cov
        if bad:
            for im in list(rec.images.values()):
                if im.name in bad:
                    rec.deregister_frame(im.frame_id)
            names = [n for n in names if n not in bad]
            print("dropped %d view(s) with a degenerate mask (%s) -> %d views"
                  % (len(bad), ", ".join("%s %.1f%%" % (n, 100 * c) for n, c in bad.items()), len(names)))

    if a.limit is not None:
        keep = set(names)
        if len(names) > a.limit:
            keep = {names[round(i * (len(names) - 1) / (a.limit - 1))] for i in range(a.limit)}
            for im in list(rec.images.values()):
                if im.name not in keep:
                    rec.deregister_frame(im.frame_id)
        dropped = len(names) - len(keep)
        names = [n for n in names if n in keep]
        print("limit: dropped %d view(s) -> %d views" % (dropped, len(names)))

    root_name = a.subject.name
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / root_name
        (root / "sparse" / "0").mkdir(parents=True)
        (root / "names.txt").write_text("\n".join(names) + "\n")

        if a.embed_images:
            (root / "images").mkdir()
            from PIL import Image
            # A solve can hold several cameras on different grids (the oak:
            # 142 views at 5712x4284 and 3 at 4032x3024), so each camera is
            # rescaled from ITS OWN image's downscaled size, never from the
            # first file in the directory.
            new_size = {}
            for n in names:
                src = a.subject / "images" / n
                cam_id = rec.find_image_with_name(n).camera_id
                if a.alpha_from_masks:
                    # Raw pixel grid throughout (no EXIF transpose): COLMAP's
                    # intrinsics and the masks are both on that grid.
                    im = Image.open(src).convert("RGB")
                    if a.max_side > 0:
                        sc = a.max_side / max(im.size)
                        im = im.resize((round(im.width * sc), round(im.height * sc)), Image.LANCZOS)
                    m = Image.open(a.subject / "masks" / (pathlib.Path(n).stem + ".png")).convert("L")
                    m = m.resize(im.size, Image.NEAREST)
                    # Brush premultiplies RGB by alpha before the loss
                    # (brush-dataset scene.rs, view_to_sample_image), so the
                    # colour under alpha=0 never reaches training. Zero it:
                    # the PNG shrinks ~6x and nothing is lost.
                    import numpy as np
                    rgb = np.asarray(im)
                    rgb = rgb * (np.asarray(m)[..., None] > 0)
                    im = Image.fromarray(rgb.astype(np.uint8))
                    im.putalpha(m)
                    dst = root / "images" / (pathlib.Path(n).stem + ".png")
                    im.save(dst, optimize=True)
                    new_size[cam_id] = im.size
                    continue
                dst = root / "images" / n
                if a.max_side > 0:
                    subprocess.run(["sips", "-Z", str(a.max_side), "-s", "formatOptions", "82",
                                    str(src), "--out", str(dst)], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    new_size[cam_id] = Image.open(dst).size
                else:
                    shutil.copy2(src, dst)
            if a.max_side > 0:
                for cid, (w, h) in new_size.items():
                    rec.camera(cid).rescale(w, h)
                print("images downscaled; intrinsics rescaled per camera: %s"
                      % ", ".join("cam %d -> %dx%d" % (cid, w, h) for cid, (w, h) in sorted(new_size.items())))
        if a.masks:
            mdir = a.subject / "masks"
            (root / "masks").mkdir()
            missing = []
            for n in names:
                m = mdir / (pathlib.Path(n).stem + ".png")
                if m.is_file():
                    shutil.copy2(m, root / "masks" / m.name)
                else:
                    missing.append(n)
            if missing:
                sys.exit("masks missing for %d registered images, e.g. %s" % (len(missing), missing[:3]))
            print("masks: %d included" % len(names))
        if a.embed_images and a.alpha_from_masks:
            for im in rec.images.values():
                im.name = pathlib.Path(im.name).stem + ".png"
        rec.write_binary(root / "sparse" / "0")

        a.out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(a.out, "w") as t:
            t.add(root, arcname=root_name)
    print("wrote %s (%.1f MB)" % (a.out, a.out.stat().st_size / 1048576))


if __name__ == "__main__":
    main()
