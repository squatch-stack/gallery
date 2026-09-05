# One-subject operator runbook

Use this checklist from the repository root. Paths below deliberately use `~`,
`<subject>`, and `<job>`; replace the placeholders before running a command.
Keep raw captures and solves outside the repository. Never promote a candidate
you have not viewed.

## 1. Plan and capture

- [ ] Generate a plan for the subject's maximum horizontal size in metres.

```sh
python3 tools/capture_plan.py --type object --size 2 --height 1.5 --out ~/Documents/<subject>-plan.md
```

The planner reads `tools/capture_defaults.json` and `docs/capture.md`. It writes
the plan named by `--out` and also prints it. `--type` accepts `object`,
`building`, `interior`, or `site`; `--json` selects machine-readable output.

Refusals: argparse reports missing required arguments, invalid subject types,
or `size and height must be finite positive metres`. Correct the measurements
or arguments; do not treat a generated plan as permission for access or drones.

- [ ] Follow the plan and `docs/capture.md`: capture two radii and multiple
  heights, keep the whole rigid subject in most frames, do not change lens or
  zoom, and check the solve before leaving the site.

### Aerial subjects (DJI Air 3S)

Use the [drone mission guide](drone.md) for permitted aerial subjects. The
planner is offline and has no terrain model; it does not authorize a flight.

```sh
python3 tools/drone_plan.py --subject "<subject>" --place "<place>" --authorization "<recorded permission>" --lat 37 --lon -93 --radius 40 --subject-height 20 --out out/mission
python3 tools/drone_plan.py --from-export export.kmz --write-skeleton
python3 tools/drone_plan.py --diff export.kmz --against out/mission.kmz
```

The first command writes JSON and the operator card, but refuses KMZ until a
controller-export skeleton exists. Read the card, record the DJI Fly version
and aircraft when extracting the skeleton, and validate import on the ground.
`--unverified-schema` permits a stamped synthetic schema for inspection only.
`--mode orbit` adds a convergent ring and nadir grid; default `double-grid`
adds orthogonal nadir grids and four oblique sides. Review timed interval,
speed, shutter, RTH height and takeoff offset. Interval mode requires starting
the timer by hand. Place screening has no override. Refusals also cover unsafe
geometry, clearance/GSD, overlap/blur, home distance and battery duration.
`--allow-multi-battery` splits at pass boundaries; `--force` replaces outputs.
Frame counts include turns; use stage 6 to measure the 256 MiB payload limit.

## 2. Solve the photographs

- [ ] Put stills in `~/Documents/<subject>/images/`, then run the global mapper.

```sh
~/.venvs/photogram/bin/python tools/solve_subject.py ~/Documents/<subject> --global --out sparse-global
```

The solver reads `images/`, recreates `database.db`, and writes a binary COLMAP
model below `sparse-global/`. It prints registered/total views, sparse points,
and mean reprojection error. Useful reruns are `--features N`, `--image-size N`,
`--guided`, `--sequential`, `--relaxed`, and `--remap`; `--remap` reuses the
database and only remaps.

Use `--help` for arguments and defaults; argparse refuses missing subjects,
unknown options, and invalid integer values.
Filesystem, image, pycolmap, or mapper exceptions are fatal. A fragmented solve
is visible as multiple models or poor registration, not a nonzero refusal.
Person: decide whether registration and coverage are adequate. Re-capture a
coverage gap; do not hide it by selecting a small fragment.

## 3. Measure orientation and crop centre

- [ ] Read gravity, camera convergence, and orbit radius from the solve.

```sh
~/.venvs/photogram/bin/python tools/scene_up.py ~/Documents/<subject> sparse-global --axis -y
```

This reads the chosen COLMAP model and prints JSON containing `up`, `focus`, and
`orbit_radius`. Save `up` for the catalog/promotion and consider `focus` for
`clean_export.py --center`. Portrait captures may need `--axis +y`, `--axis
-x`, or `--axis +x`.

Use `--help` for arguments and defaults; argparse refuses missing subjects and
invalid axes. Missing or invalid models or a singular focus calculation raises
an exception. Person:
preview the scene and choose the axis that makes it level rather than blindly
selecting the smallest spread.

## 4. Make and inspect subject masks (GPU)

- [ ] Generate masks with a prompt that names the actual subject.

```sh
~/.venv-masks/bin/python tools/make_subject_masks.py ~/Documents/<subject> --prompt "<subject>" --side 1024 --box-threshold 0.3 --text-threshold 0.25 --grow 8
```

The tool reads `images/`, model weights available to its environment, and the
text prompt. It writes full-resolution PNG masks to `masks/` and a contact sheet
to `masks-preview.jpg`; `--limit N` and `--max-boxes N` constrain a trial.

Model/import/device/image errors are fatal. A missed prompt is deliberately not
a refusal: the tool writes an all-white mask and prints a warning. Person:
inspect `masks-preview.jpg`. Regenerate with a better prompt or thresholds if
the subject is clipped, background is retained, or warnings reveal misses.

## 5. Run the standard pipeline

- [ ] Preview the orchestrator without writing anything.

```sh
python3 tools/subject_run.py ~/Documents/<subject> ~/Documents/<job> --stem <subject> --prompt "<subject>" --max-side 1920 --crop-quantile 0.9 --min-coverage 0.03 --steps 30000 --max-splats 1500000 --seed 42 --python ~/.venvs/photogram/bin/python --gpugate ~/bin/gpugate-mac --dry-run
```

- [ ] Run through broker submission when the preview is correct.

```sh
python3 tools/subject_run.py ~/Documents/<subject> ~/Documents/<job> --stem <subject> --prompt "<subject>" --max-side 1920 --crop-quantile 0.9 --min-coverage 0.03 --steps 30000 --max-splats 1500000 --seed 42 --python ~/.venvs/photogram/bin/python --gpugate ~/bin/gpugate-mac --submit
```

The orchestrator reads the solve, images, and masks. If masks are absent it runs
the GPU masker unless `--skip-masks` was supplied. It calls
`make_scene_payload.py`, submits `scene.tar`, consumes the newest matching
`gpugate-*/metrics.json` and `point_cloud.ply`, calls `clean_export.py`, and
prints a `promote_scene.py --dry-run` preview. It continually writes
`<job>/run.json`; cleaned `.sog`/`.spz` files go under `<job>/scenes/` and the
raw cleaned PLY under `<job>/archive/`.

If a complete broker result is already under `<job>/gpugate-*/`, rerunning
resumes at cleaning. Without `--submit`, it builds the payload, records
`awaiting_submission`, and prints the broker command for a separate submission.
`--limit N` must be at least 8.

Refusals and responses:

- `masks/ is required for the alpha payload`: generate masks or remove the
  inappropriate `--skip-masks`.
- `scene.tar ... limit is 256 MiB; refusing submission`: follow the printed
  `--limit` estimate, reduce `--max-side`, or use `--host-composite`.
- A printed minimum of 8 means view reduction cannot solve the size problem;
  reduce resolution or use host compositing.
- `submission succeeded but no ... metrics.json and point_cloud.ply appeared`:
  inspect the broker output/job directory; cleaning cannot proceed without both.
- Any child exit status stops the run and is recorded in `run.json`; fix that
  stage, preserve its evidence, and rerun to resume.

## 6. Payload-only and host-composited side path

Use this only when operating stages separately or diagnosing the orchestrator.
The normal embedded-image alpha payload is:

```sh
~/.venvs/photogram/bin/python tools/make_scene_payload.py ~/Documents/<subject> ~/Documents/<job>/scene.tar --sparse sparse-global --embed-images --max-side 1920 --alpha-from-masks --min-coverage 0.03 --limit 68
```

It reads the selected sparse model, registered images, and masks; filters
degenerate masks; and writes `scene.tar`. It prints fragmentation, dropped-view,
downscale, mask-count, and payload-size information. It refuses `--limit` below
8 and exits if a registered image lacks a mask. Choose `--model N` only after
reviewing a fragmented solve.

For a payload too large at useful quality, ship original names plus masks and
make the broker host composite alpha:

```sh
python3 tools/subject_run.py ~/Documents/<subject> ~/Documents/<job> --stem <subject> --prompt "<subject>" --host-composite --max-side 1920 --steps 30000 --max-splats 1500000 --seed 42 --python ~/.venvs/photogram/bin/python --gpugate ~/bin/gpugate-mac --submit
```

This changes the payload from embedded RGBA images to names plus masks and adds
`--alpha_from_masks=true` to the broker command. It still records alpha
supervision. The host must have the full-resolution photos in its configured
photos directory.

## 7a. Prune by views (CPU)

Use the archived trained PLY and its matching COLMAP solve in the original file
frame. NumPy and Pillow handle pruning on CPU; pycolmap loads the solve. Masks
must correspond to registered raw image grids; uniform camera rescaling is
supported and printed. No GPU is used.

```sh
~/.venvs/photogram/bin/python tools/prune_by_views.py ~/Documents/<job>/gpugate-<job>/point_cloud.ply --solve ~/Documents/<subject> --masks ~/Documents/<subject>/masks --views 24 --view-select spread --holdout 5 --out-ply out/<subject>-pruned.ply --keep-out out/<subject>-keep.npz --report out/<subject>-prune.json --angles-out out/<subject>-angles.txt
```

Held-out recall, not the splat count, is the acceptance signal. The operator
still views the candidate, especially thin rims and far-side detail, before
passing this PLY into cleaning. Cleanliness checks alone cannot detect
amputation. The report measures held-out silhouette precision and recall using
projected-centre occupancy on the mask-scale grid, before and after pruning;
this is a coverage proxy, not a photometric render. A recall decrease requires
review even when precision and floater/fog metrics improve. `--holdout 0`
explicitly disables this evidence and reports unavailable metrics.

The jury uses deterministic spread sampling about the solve's convergence
point. Out-of-frame, occluded, and uncertain mask-edge splats abstain. Judged
splats must meet both the inside count and the fraction of views that could
judge them; insufficiently judged splats use the printed `--unjudged` policy
(default keep). Depth buffers use opaque splats, with tolerance for depth noise,
extent, and splat reach. Coarse buffers bias toward abstention and keeping.
The optional post-vote 27-cell neighbourhood counts self; alpha weighting
prevents transparent needles from providing strong mutual support.

Defaults and their sources/rationales are in `tools/prune_defaults.json`;
`--defaults PATH` merges an override file of the same shape. `inside_fraction`,
`min_views`, and `outlier_min_neighbours` are provisional pending cannon/oak
held-out recall tuning. The JSON report records `prune_by_views`, exact flags,
resolved thresholds, input hashes, jury/holdout names, and per-stage removed
mass with the preceding stage's remaining mass as denominator. Preserve this
report as cleaning evidence for the candidate's provenance sidecar.

The tool refuses mismatched frames or mask aspect ratios, missing masks,
unsupported camera models, invalid thresholds, and removal of every splat
before writing outputs. Fully white and near-empty masks are excluded with
warnings. Only fixed-width binary PLY can produce a byte-exact PLY subset:
all properties, including `f_rest_*`, are retained without frame conversion.
SOG supports report/keep-mask output only: re-export from the archived PLY.
Review angles use the viewer flip and scene-up alignment; distance is fixed by
`--sheet-distance`, not inferred from the solve's camera range.

Prior art: [Clean-GS, arXiv:2601.00913](https://arxiv.org/abs/2601.00913).
This tool independently uses repository primitives and standard multi-view
geometry. Its implementation does not use that project's CC BY-NC-SA code.

## 7. Clean and check a candidate

- [ ] If cleaning outside the orchestrator, use the measured centre/up and an
  explicit delivery budget as appropriate.

```sh
~/.venvs/photogram/bin/python tools/clean_export.py ~/Documents/<job>/gpugate-<job>/point_cloud.ply --stem <subject> --out ~/Documents/<job>/scenes --archive ~/Documents/<job>/archive --alpha-min 0.05 --crop-quantile 0.9 --crop-margin 1.1 --crop-shape cylinder --up 0,1,0 --center 0,0,0 --max-aspect 167 --aspect-measure minmax --scale-ceiling 0.05 --sh-clusters 1024 --target-count 500000
```

It reads a trained PLY and writes cleaned SOG/SPZ deliveries plus an archived
PLY. It prints crop geometry, aspect statistics, pruning counts, and sizes.
`--crop-radius` can replace quantile/margin sizing. Refusals cover malformed
vectors, nonpositive target counts, zero `--up`, invalid quantile/margin/radius,
unknown shapes, and a dense centre with no splats at alpha 0.5 or above.

- [ ] Check the candidate using the profile that matches the subject.

```sh
~/.venvs/photogram/bin/python tools/check_deliverable.py ~/Documents/<job>/scenes/<subject>.sog --platform web-mobile --subject object --json
```

The checker reads the candidate and repository policy/catalog context and emits
counts, sizes, cleanliness metrics, and per-rule evidence. `--subject place`
reports rather than enforces the floater rule. Unknown/unreadable inputs fail
closed. It refuses a missing target or using a target together with `--all`.

- [ ] For a cleaning sweep, generate the owner comparison report.

```sh
python3 tools/candidate_report.py out/candidates --out out/candidates.md
```

This reads each candidate manifest plus `scenes.json` and `checks.json`, then
writes current/candidate metrics, attempts, and an exact promotion preview.
Missing/malformed files or unknown stems raise errors. Person: open the SOG in
the viewer and compare it with the current scene; a PASS is not visual approval.

## 8. Mesh reconstruction side path

Choose a mesh for rigid surfaces such as trunks, stone, and architecture;
foliage is a poor fit. Use the inspected subject masks from stage 4. This is
an alternative reconstruction path to the splat training and cleaning stages.

**Measured on the oak, 2026-09-04, so "foliage is a poor fit" is evidence and
not advice.** Apple's photogrammetry at full detail with ground-patch masks
over 169 photographs registered **84**, against COLMAP's 145 on the same
photographs, and produced 55,836 vertices and 99,999 triangles. The trunk came
out solid, with bark relief and root flare, standing on fragments of ground.
The canopy did not come out at all: no branches, no leaves, nothing above the
first fork. Moving leaves give a photogrammetry solver nothing stable to match,
so it discards those frames and meshes what stayed still.

Read that as a rule for choosing the deliverable. A tree ships as a splat,
where the canopy is at least soft rather than absent. Stone, timber, metal and
architecture ship as a mesh, which is also what a client can measure and drop
into their own software.

The same run also showed why a mesh needs its textures resized before delivery:
a 10.8 MB OBJ arrived with a 72.2 MB colour map, a 47.7 MB normal map, a
35.2 MB roughness map and a 137.5 MB displacement map, and the first conversion
produced a **66.3 MB** GLB for a 100,000-triangle mesh against a 20 MB budget.

- [ ] In the sibling posekit repository, reconstruct at full detail with masks,
  then convert the OBJ to a textured GLB. These are external-tool examples
  (quoted as text so this repository's CLI tests do not validate posekit flags).
  Resolve `<subject>` and `<job>` from that repository's working directory.

```text
.build/release/posekit <subject>/images --out <job>/mesh --sequential --model full --masks <subject>/masks
.venv/bin/python tools/obj_to_glb.py <job>/mesh/model.obj <job>/mesh/model.glb
```

The first command writes the mesh under `<job>/mesh`; the second converts
`model.obj` to `model.glb`. Keep the OBJ's material and texture assets available
for conversion. Inspect the converted GLB for complete surfaces, textures,
orientation, and retained background before considering promotion.

- [ ] Back in this repository, check the GLB candidate outside `scenes/`.
  Use `--subject place` for architecture that retains its surroundings.

```sh
python3 tools/check_deliverable.py <job>/mesh/model.glb --platform web-mobile --subject object --json
```

The checker records `triangles` (stored primitive topology, not scene instances)
and `texture_bytes` (embedded image bytes), plus total `size_bytes`. Splat
cleanliness proxies do not apply to meshes. Malformed GLBs and textures that
are not embedded in buffer views fail the format check. A new stem can fail
provenance before promotion creates its catalog entry and provenance page;
inspect every rule rather than treating that as a geometry failure.

The first budget a textured mesh can hit is total delivery bytes: web-mobile
allows 20,000,000 bytes, not 20 MiB. `springhouse-outside` is 26,858,880 bytes
and fails that budget; `cannon-mesh` is 17,549,488 bytes and passes the recorded
web-mobile check. There is no triangle budget in this checker; a zero internal
splat count does not make a large mesh cheap. Reduce texture resolution or
encoding size, and mesh detail as needed, then convert, inspect, and check again.
The current snapshot lacks triangle/texture measurements for these examples;
the results page reports those missing measurements as unavailable.

- [ ] Preview promotion as a sibling `-mesh` stem, as with `cannon-mesh`
  beside `cannon`. Replace the ellipses with quoted title, blurb, and an honest
  source summary; use a source commit that documents the reconstruction.

```sh
python3 tools/promote_scene.py <job>/mesh/model.glb --stem <stem>-mesh --title ... --blurb ... --source ... --source-commit HEAD --cleaning "posekit --model full --masks" --up 0,1,0 --dry-run
```

For replacement, use the existing stem and add `--replace` to that preview.
Review the GLB, orientation, metadata diff, source evidence, cleaning detail,
and deliverable verdict. Only after approval repeat without `--dry-run`.
Replacement archives the displaced scene and provenance; a sibling preserves
the existing splat entry. Mesh promotion copies the GLB, records its mesh path
and triangle count, generates app-export provenance, and runs the web-mobile
check. It requires an identified mesher and detail setting in `--cleaning`,
plus `--source` and `--source-commit` or recoverable existing app-export evidence.
Do not use `--provenance-from` for meshes or invent splat training statistics.
An existing stem without `--replace`, a candidate inside current scene files,
or missing mesh/source evidence is refused. After promotion, refresh gallery
evidence using stage 11; a promotion dry run only previews changes and does
not perform the deliverable check.

## 9. Provenance and promotion decision

For a solved/trained subject, promotion can generate provenance from the solve
and metrics. First preview the exact change:

```sh
~/.venvs/photogram/bin/python tools/promote_scene.py ~/Documents/<job>/scenes/<subject>.sog --stem <subject> --title "<subject>" --blurb "A scan of <subject>." --up 0,1,0 --place "<place>" --captured 2026-09-04 --provenance-from ~/Documents/<subject> --trained ~/Documents/<job>/gpugate-<job>/metrics.json --supervision alpha --provenance-python ~/.venvs/photogram/bin/python --replace --dry-run
```

Person: review the candidate itself, metadata, diff, provenance evidence, and
deliverable verdict. If it should replace the current gallery scene, repeat the
printed command without `--dry-run`. Otherwise keep the current scene.

```sh
~/.venvs/photogram/bin/python tools/promote_scene.py ~/Documents/<job>/scenes/<subject>.sog --stem <subject> --title "<subject>" --blurb "A scan of <subject>." --up 0,1,0 --place "<place>" --captured 2026-09-04 --provenance-from ~/Documents/<subject> --trained ~/Documents/<job>/gpugate-<job>/metrics.json --supervision alpha --provenance-python ~/.venvs/photogram/bin/python --replace
```

Promotion reads the candidate, catalog, solve, metrics, and current scene. It
archives replaced scene/provenance files under `archive/replaced/`, copies the
SOG, updates `scenes.json`, generates provenance, and runs the web-mobile check.
Key refusals cover invalid/missing SOG metadata, unsafe stems, incomplete
promotion arguments, missing provenance pairs, candidates inside current scene
files, an existing scene without `--replace`, and unavailable provenance.

If a reviewed replacement is wrong, preview and then perform a revert:

```sh
python3 tools/promote_scene.py --revert <subject> --from archive/replaced/<subject>-2026-09-04 --dry-run
```

Omit `--from` to select the newest archive. Repeat without `--dry-run` after
review. Revert refuses missing/foreign archives, mismatched archived entries,
or promotion flags mixed with `--revert`; it archives the displaced version,
restores files/catalog metadata, and reruns the deliverable check.

## 10. App-exported scene side path (no solve)

Use this when the delivered scene came from an app and raw solve inputs are not
available. Create provenance from explicit evidence first:

```sh
python3 tools/provenance.py <subject> --app-export --source "<capture and export summary; state gaps>" --source-commit <commit> --cleaning "<exact cleaning excerpt>" --note "<evidence limitation>" --title "<subject>" --place "<place>" --out provenance/<subject>.html
```

The tool reads the catalog entry, named git commit messages, and delivered scene
files, then writes the provenance page and JSON sidecar with hashes. It refuses
an unknown catalog stem, missing `--source`/`--source-commit`, cleaning text not
found in a supplied commit message, invalid scene formats, or no delivered
files. `--source-commit`, `--cleaning`, and `--note` are repeatable.

Preview replacement with `promote_scene.py --replace --dry-run` and
`--cleaning "<flags>"`, then repeat without `--dry-run` only after visual
approval. Promotion preserves app-export evidence through the sidecar; without
it, exact cleaning flags are required. Do not invent solve/training statistics.

## 11. Refresh gallery evidence

- [ ] After any delivered file, catalog, or provenance change, refresh checks.

```sh
PYTHON=~/.venvs/photogram/bin/python tools/refresh_checks.sh
```

This runs all web-mobile deliverable checks to a temporary JSON file and replaces
`checks.json` only after the output is complete and matches the catalog. Checker
exit 1 (valid failing checks) is saved; execution/invalid-output failures leave
the previous snapshot intact. `--help` prints usage without refreshing the snapshot.

- [ ] Inspect inventory, freshness, provenance, and archives.

```sh
python3 tools/gallery_status.py --stale-days 30 --json
```

It reads `scenes.json`, deliveries, `checks.json`, provenance, and replacement
archives and writes only stdout. Stale content exits 1; aged mtime-only evidence
is reported but does not alone change exit status. Negative `--stale-days` is
refused.

- [ ] Regenerate the measured-results page only when its sanitized job records
  or delivery/check/provenance records intentionally change; otherwise check freshness.

```sh
python3 tools/results_table.py --check
```

The tool reads `docs/results/runs.json` and job JSON, and compares
`docs/results.md` plus `docs/results/sweep.svg`. It prints `Stale or missing ...`
and exits 1 on disagreement. Without `--check`, it rewrites both outputs;
invalid IDs, missing metrics, failed jobs, or inconsistent settings raise errors.

- [ ] Run accessibility checks after page changes.

```sh
python3 tools/a11y_check.py --all --statement docs/accessibility.md --json
```

It reads `viewer.html` and `index.html`, optionally rewrites the statement, and
prints per-rule JSON. Any failed measured rule exits 1. File/read failures raise
errors; a PASS remains a limited static self-assessment, not a full audit.

## 12. Gates and handoff

The full local and CI gate is:

```sh
tools/gate.sh
```

It runs Ruff (`E,F,B,RUF`, line length 120), pytest, all web-mobile deliverable
checks, static accessibility, results freshness, and tracked-text path hygiene.
Deliverable failures are currently advisory in CI even though the tool prints
evidence. Missing Ruff prints its install hint and exits; each other blocking
stage stops the script on a nonzero status.

Enable the repository's quick pre-push hook once per clone:

```sh
git config core.hooksPath .githooks
```

The hook runs `tools/gate.sh --quick`: Ruff, path hygiene, and pytest. CI runs
the complete gate on pull requests and pushes to `main` with Python 3.12.

For a direct hygiene check:

```sh
python3 tools/scan_paths.py
```

It reads tracked text files and reports repository-relative line numbers for
machine-specific home paths or private IPv4 addresses. Findings exit 1; git or
I/O failures exit 2. `--help` prints usage without scanning.
