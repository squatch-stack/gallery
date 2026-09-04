# Brush patches

`dispatch-2d-v0.3.0.patch` lifts Brush 0.3.0's ~16.7 megapixel image ceiling.
Brush rasterizes one compute workgroup per 16x16 tile and launched the whole
tile grid on one axis; WebGPU allows 65,535 workgroups per axis, so a
5712x4284 photograph (357 x 268 = 95,676 tiles) failed at dispatch on every
backend. The patch launches the tile grid on two axes in the forward and
backward rasterizers, and makes the one indirect launch that can also exceed
the limit (the intersection tile-offset pass) two-dimensional. Eleven files,
35 insertions, 21 deletions; training math unchanged.

Written by a Codex lane against the v0.3.0 source, built with cargo 1.98,
and verified on an M1 Max (Metal) with the Brookline cannon solve:

    stock 0.3.0,  --max-resolution 5712   fails in ~4 s (dispatch validation error)
    patched,      --max-resolution 5712   200 steps in 179 s incl. loading 57 x 24 MP stills, PLY written
    patched vs stock, --max-resolution 1920, 200 steps   20 s vs 21 s, identical vertex counts

`REPORT-v0.3.0.md` is the lane's audit of every launch that scales with image
size or intersections.

**Upstream already fixed this.** Brush `main` (inspected at `8b7f5c6c`, 2026-09)
carries the fix in commit `40d3137d` ("fix: support resolutions >2048 by
handling WebGPU workgroup dispatch limit", #363), and the kernels have since
moved to CubeCL (#411), so this patch applies only to the v0.3.0 release
binary. The right route to native-resolution training is a build of upstream
main; this patch is the backport for anyone pinned to 0.3.0. No pull request
is needed.
