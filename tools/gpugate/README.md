# gpugate `splat-brush` recipe request

Filed 2026-09-04 as request `fb20023ce7` on the LAN GPU broker: train
Gaussian splats with Brush on the RTX 5090 from a solve computed on the
Mac, reading the full-resolution photos the host already holds.

- `splat_brush_job.py` — the driver the host was asked to pin (stdlib only).
- `splat-brush.toml` — the contract sketch, in the shape of hdc-holo's
  `bench/RECIPE.md`; not the broker's own recipe schema.
- `purpose.txt` — the purpose text as filed.

## What the host's triage changed

The request was approved and scaffolded the same day (host report
`20260904-65465a`), with two defects found in the driver as filed. Both are
fixed in the copy here, mirroring the host's scaffold:

1. **Path traversal.** `names.txt` arrives inside the client's tar and was
   used as a path directly; pathlib discards the left operand when the right
   one is absolute, so a line of `/etc/shadow` read outside the photos
   directory and wrote a symlink outside the job directory. Names must now be
   bare filenames, and each resolved path must sit directly under the
   resolved photos directory.
2. **`max_res = 0` was not native.** Omitting `--max-resolution` selects
   Brush's own default of 1920, so the planned 1920 / 2856 / native sweep
   would have had two identical arms. The flag is now always passed and
   `--max_res` is required; native means naming 5712.

Smaller: the driver refuses a tar holding more than one scene, and refuses to
start if `$BRUSH` is not a file. From the host's second review (report
`20260904-e60167`): Brush's default seed is 42, so the driver defaults to 42
and both lanes must pass the seed explicitly; the verification clause asks
for agreement (splat count within a few percent, loads in both viewers, no
qualitative difference), not bit-identical counts, because Metal and Vulkan
reduce floats in different orders; the metrics gain `seconds_per_1k_steps`
and `peak_rss_mb`; and if `point_cloud.ply` is absent the newest export is
adopted, since Brush's `--export-name` is a template. The wall clock is
1800 s as scaffolded.

First results (2026-09-04). The 1920 px / 30k / 1.5M / seed 42 baseline ran
in 619.5 s on the 5090 (20.65 s per 1k steps, peak RSS 2.3 GB) against about
112 minutes for the identical run on the M1 Max; both hit the 1.5M cap. A
native (5712 px) probe failed in 4 s with a wgpu validation error — Brush
0.3.0 dispatches one compute workgroup per 256 pixels and wgpu caps a
dispatch dimension at 65,535, so the trainer cannot rasterize an image above
about 16.7 megapixels on any backend. 24.5 MP stills therefore cannot be
trained at native size with this trainer; the largest safe 4:3 size is about
4700 px on the long side, and 4096 is a clean choice. The remaining gate is host configuration: the Brush
directory and the photos directory must be in the broker's
`allowed_ro_roots`, then the recipe pinned.

The copies sent named the host's local path to the Brush binary as a default;
these public copies take it from `$BRUSH` instead. Payloads for the recipe
come from `../make_scene_payload.py`.
