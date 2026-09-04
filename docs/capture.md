# Field capture checklist

What a day at Wilson's Creek taught, written for the next site. Every line
here cost a scan or an hour on 2026-09-04; none of it is theory.

## Before you leave

- **Permit.** National Park Service units: no permit for eight or fewer people
  on foot in areas open to the public, no models, sets or props, no added staff
  cost (54 U.S.C. 100905 as amended 2025). Drones are prohibited in NPS units
  regardless. Missouri State Parks and historic sites: commercial productions
  file a Commercial Production Application with the facility manager at least
  one month ahead; whether one person taking stills counts is the manager's
  call, so ask. The Department of Conservation has different, no-fee rules
  and manages different land.
- **Subject list.** Name each rigid subject you will orbit. "The walls" is not
  a subject; "the springhouse" is. Forty-three close-ups of stone walls solved
  perfectly and trained into a cloud that is not a building.
- **Phone.** Stock camera, Smart HDR left on (the trainer can correct
  exposure drift; it cannot invent detail), 24 MP stills, not video. Charge it;
  a subject is 60 to 170 frames.

## At the subject

- **Orbit, don't approach.** Two radii and two heights around the subject,
  frames every 10 to 15 degrees, each frame overlapping the last by more than
  half. A thin single-pass walk-around is exactly what fragments the solve.
- **Keep the whole subject in frame** for most shots; close-ups are for texture
  and come after the orbit, not instead of it.
- **Wind is the enemy, not light.** Grass and leaves that move between frames
  become long thin splats that get sharper with more training. Shoot the rigid
  thing; expect to mask everything else out at training time. Bare ground,
  gravel and stone are fine. If the subject itself moves (a tree in wind),
  shoot on a still morning or accept a soft canopy.
- **Three unregistered frames is normal; twenty-four is a coverage gap.** The
  field check in the car (solve at low resolution, count registered views)
  tells you which before you leave the site.
- **Do not change lens or zoom mid-orbit.** Mixed image sizes (a 12 MP frame in
  a 24 MP set) solve, but every change costs matches.

## Back at the desk, in order

1. Solve with the global mapper (`tools/solve_subject.py --global`). The
   incremental mapper fragmented a connected 145-view orbit into four pieces;
   global mapping registered all 145 in 37 seconds.
2. Read the gravity and the crop centre from the solve
   (`tools/scene_up.py`). If most photographs point up into a canopy, compare
   the camera estimate with ground in the trained cloud:
   `python tools/scene_up.py captures/oak --from-cloud exports/oak.ply`.
   Omit `captures/oak` to run without a solve or pycolmap; SOG v2 is also
   supported (its image decoder requires Pillow). The estimator itself uses
   only NumPy and runs on CPU. `--alpha-min` defaults to 0.2.
   With a solve, the existing top-level `up` remains the camera estimate;
   `from_cloud.up` is the ground estimate. Without a solve, top-level `up`
   also contains the ground estimate. A rejected estimate is `null` with a
   reason. Inspect `from_cloud.inliers` (fraction of finite splats above the
   opacity floor), `extent` (two robust in-plane widths), and `agreement_deg`
   (angle to each camera candidate; null without cameras) before using it.
3. Make subject masks (`tools/make_subject_masks.py --prompt "<subject>"`).
   Class-based masks fail on subjects that share a colour with their
   surroundings (an olive cannon on grass reads as "plant"); name the subject
   instead. Check the preview.
4. Train with the mask as an **alpha channel** on the images, not as a
   `masks/` folder: the folder means "ignore these pixels", which lets stray
   splats grow unpunished; alpha means "render nothing here".
5. Clean and export (`tools/clean_export.py`), then write the provenance
   sheet (`tools/provenance.py`). The sheet is generated from the files;
   do not type numbers into it.

### Gravity and cloud coordinates

Both gravity estimates are in the catalog/viewer frame **before gravity
alignment**: file `(x, y, z)` becomes `(x, -y, -z)`, matching the loader in
`clean_export.py` and the viewer's 180-degree X rotation. The shared PLY/SOG
decoders used by `scene_up.py` return file coordinates, so the tool applies
that conversion once. Do not flip its reported vector again. The viewer
then rotates catalog `up` onto +Y.

Brush can rescale cloud coordinates relative to the solve. Plane widths
are therefore in cloud units, not guaranteed metres or solve units; camera
focus and orbit radius remain in solve units. Translation and a positive
uniform scale do not change gravity direction. A separately rotated export
must share the solve's orientation for camera agreement to be meaningful.

The cloud method assumes the dominant broad plane is ground with the subject
on one side. It uses a fixed sample and RANSAC seed, prefers dense planar
neighbourhoods with low density gradients, and rejects weak, narrow or thick
support. A dominant wall, ceiling or table can still be mistaken for ground;
the confidence values measure plane support, not semantic certainty. It does
not independently classify trunks. A weighted median projected onto the
normal determines its sign; if that median is exactly on the plane, off-plane
mass resolves the sign, or the tool reports that orientation is unavailable.

## What the numbers looked like on the first day

| subject | frames | registered | trainer, 1920 px, 30k | verdict |
|---|---|---|---|---|
| cannon | 60 | 57 | 620 s on the 5090; the M1 Max took ~112 min | good after subject crop; masking under test |
| oak | 169 | 145 (global) / 50 (incremental) | 676 s | canopy recognizable, sky shell around it; needs isolation |
| springhouse walls | 43 | 43 | 831 s | not a building; capture lesson |

Times vary by a third between identical runs on a shared card; quote them as
ranges.

## Subject capture plans
Run `python3 tools/capture_plan.py --type object --size 2` for rings, overlap arithmetic and a checklist.
Use `--height`, `--out plan.md` or `--json`; review the labelled operator defaults before capture.
