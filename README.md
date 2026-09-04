# Field Scans

[![Gates](https://github.com/squatch-stack/gallery/actions/workflows/gates.yml/badge.svg)](https://github.com/squatch-stack/gallery/actions/workflows/gates.yml)

Gaussian-splat scans of real places, captured on foot and processed end to
end by Squatch Stack tooling — cleaned and packed to SOG by the
[holo](https://github.com/squatch-stack/hdc-holo) exporters, rendered with
[Spark](https://sparkjs.dev) on three.js.

Live: https://squatch-stack.github.io/gallery/ (custom domain squatch.cc
pending DNS).

- `scenes/` — web-delivery splats: `.sog` (viewer) and `.spz` (interop)
- `viewer.html` — Spark viewer; `?scene=<stem>` from `scenes.json`
- `tools/clean_export.py` — opacity floor, alpha-weighted median crop centre, fog cull,
  and export, all through holo's own writers

Raw captures are archived off-repo.

Catalog cards show the saved web-mobile checks in `checks.json`. After changing
a delivered file, catalog entry, or provenance page, run `./tools/refresh_checks.sh`.
It tries `.venv-check/bin/python`, then
`~/Documents/HDC-VSA-Gaussian-Splatting/.venv/bin/python`, then
`.venv-masks/bin/python`, then `python3`; set `PYTHON` to override
(requires numpy and Pillow). Failed scene checks are saved too;
an execution error leaves the previous snapshot intact. Run
`python3 tools/check_deliverable.py --all` with the same dependencies for the table.

For scenes without accessible raw solve inputs, `tools/provenance.py --app-export`
accepts a catalog stem, `--source`, one or more `--source-commit` references, and
`--out`. It quotes the source messages and hashes the delivered files; optional
`--cleaning` excerpts must occur in those messages. See `out/generate_provenance.py`
for the source selections used for the nine additional sheets.

## Capturing a site

The field checklist, written from the first day's mistakes: [docs/capture.md](docs/capture.md).
For the complete field-to-gallery command sequence, use the [operator runbook](docs/runbook.md).

## Results

[Measured training results: resolution sweep, masking A/B, and short probes](docs/results.md).

## Gates

- `python3 tools/gallery_status.py [--json] [--stale-days N]` shows inventory, saved verdicts, provenance, and freshness.
- `python3 tools/promote_scene.py --revert <stem> [--from archive/replaced/<dir>] [--dry-run]` undoes a replacement.

Run the complete local gate suite with `tools/gate.sh`. It checks Python style
and correctness with Ruff, runs the test suite, validates every delivered scene,
checks static accessibility, confirms the generated results page is current, and
scans tracked text for machine-specific home paths and private-network IPv4
addresses. The deliverable and accessibility checks print detailed evidence when
they fail. In CI the deliverable check is advisory for now: seven of the ten
scenes were exported before the checker existed and fail its budgets; they
will be re-exported, and the gate becomes blocking when they pass.

Enable the quick pre-push gate once per clone:

```sh
git config core.hooksPath .githooks
```

The hook runs `tools/gate.sh --quick`, which covers Ruff, path hygiene, and pytest.
CI runs the complete suite on pull requests and pushes to `main` using Python 3.12;
its gate-only dependencies do not include `pycolmap` or `torch`.

## License

Apache-2.0, the same terms as the studio's other public repos. Scans published here are CC BY 4.0 unless a scene says otherwise.
