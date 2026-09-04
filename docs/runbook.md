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

This tool has no argparse refusal text and does not implement `--help`.
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

There are no friendly refusals or working `--help`; missing or invalid models,
an invalid axis, or a singular focus calculation raises an exception. Person:
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

## 8. Provenance and promotion decision

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

## 9. App-exported scene side path (no solve)

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

## 10. Refresh gallery evidence

- [ ] After any delivered file, catalog, or provenance change, refresh checks.

```sh
PYTHON=~/.venvs/photogram/bin/python tools/refresh_checks.sh
```

This runs all web-mobile deliverable checks to a temporary JSON file and replaces
`checks.json` only after the output is complete and matches the catalog. Checker
exit 1 (valid failing checks) is saved; execution/invalid-output failures leave
the previous snapshot intact. The script has no `--help` mode.

- [ ] Inspect inventory, freshness, provenance, and archives.

```sh
python3 tools/gallery_status.py --stale-days 30 --json
```

It reads `scenes.json`, deliveries, `checks.json`, provenance, and replacement
archives and writes only stdout. Stale content exits 1; aged mtime-only evidence
is reported but does not alone change exit status. Negative `--stale-days` is
refused.

- [ ] Regenerate the measured-results page only when its sanitized job records
  intentionally change; otherwise check freshness.

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

## 11. Gates and handoff

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
I/O failures exit 2. It has no `--help` mode.
