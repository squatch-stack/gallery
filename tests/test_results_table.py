"""Small file-backed fixtures for results calculations and freshness checks."""

import importlib.util
import json
from pathlib import Path
import re

import pytest

SPEC = importlib.util.spec_from_file_location("results_table", Path(__file__).parents[1] / "tools/results_table.py")
results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(results)


@pytest.fixture
def records(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "ROOT", tmp_path)
    directory = tmp_path / "docs/results"
    jobs = directory / "jobs"
    jobs.mkdir(parents=True)
    runs = {}
    definitions = [
        ("base", "unmasked", 1920, 30000, 1000, 27.648),
        ("medium", "unmasked", 2856, 30000, 1000, 61.177536),
        ("large", "unmasked", 4608, 30000, 1000, 159.25248),
        ("folder", "masks-folder", 1920, 30000, 400, 20),
        ("alpha", "alpha-matched", 1920, 30000, 200, 19),
        ("probe", "unmasked", 4608, 3000, 50, 99),
    ]
    for job_id, arm, resolution, steps, splats, rate in definitions:
        runs[job_id] = {
            "subject": "cannon",
            "arm": arm,
            "steps_label": "deliberately ignored",
            "notes": str(tmp_path),
            "cleaned": {"quantile": 0.9, "splats": splats // 2, "sog_bytes": None if arm == "masks-folder" else 1234},
        }
        job = {
            "rc": 0,
            "max_res": resolution,
            "steps": steps,
            "splats": splats,
            "seconds_per_1k_steps": rate,
            "wall_seconds": rate * steps / 1000,
            "images": 56 if arm == "alpha-matched" else 57,
            "peak_rss_mb": 100,
            "seed": 42,
            "max_splats": 1000,
            "host": "private-" + "machine",
            "path": str(tmp_path),
        }
        (jobs / f"{job_id}.json").write_text(json.dumps(job))
    index = directory / "runs.json"
    index.write_text(json.dumps({"trainer": "Brush 0.3.0", "seed": 42, "cap": 1000, "runs": runs}))
    return tmp_path, index, jobs


def test_calculations_and_separation(records):
    _, index, jobs = records
    page, svg = results.generate(index, jobs, "results/sweep.svg")
    assert "| base | 1920 | 57 | 2.7648 | 27.65 | 10.00 | 829.4 | 1,000 | 100.0 |" in page
    assert "| masks-folder | folder | 57 | 600.0 | 400 | 200 | 0.9 | unavailable | 60.00% | 80.00% |" in page
    assert "| alpha-matched | alpha | 56 | 570.0 | 200 | 100 | 0.9 | 1,234 | 80.00% | 90.00% |" in page
    assert "extrapolation (not a measurement)" in page
    assert "244.71 s / 1k steps" in page
    assert "approximately flat from 2856" in page
    assert "cap binds at every resolution" in page
    main, probes = page.split("## Probes")
    assert "| probe |" not in main
    assert "| probe | cannon | unmasked | 3,000 |" in probes
    assert "99.00" not in svg
    assert svg.count(" (unmasked)</text>") == 3
    assert "Frame size (megapixels, 4:3)" in svg
    assert "Seconds per 1k steps (s)" in svg


def test_output_does_not_expose_private_metadata(records):
    root, index, jobs = records
    page, svg = results.generate(index, jobs, "results/sweep.svg")
    for output in (page, svg):
        assert str(root) not in output
        assert "private-" + "machine" not in output
        assert not re.search(r"/(?:Users|home)/|\b(?:\d{1,3}\.){3}\d{1,3}\b", output)


def test_check_and_repeatability(records):
    root, _, jobs = records
    output = root / "docs/results.md"
    svg = root / "docs/results/sweep.svg"
    args = ["--out", str(output)]
    assert results.main([*args, "--check"]) == 1
    assert results.main(args) == 0
    original, chart = output.read_text(), svg.read_text()
    assert results.main(args) == 0
    assert results.STAMP.sub("", output.read_text()) == results.STAMP.sub("", original)
    assert svg.read_text() == chart
    output.write_text(results.STAMP.sub("Generated at: 2000-01-01 00:00:00 UTC", original))
    assert results.main([*args, "--check"]) == 0
    output.write_text(output.read_text() + "stale\n")
    assert results.main([*args, "--check"]) == 1
    assert output.read_text().endswith("stale\n")
    assert results.main(args) == 0
    svg.write_text(chart.replace("seconds", "minutes"))
    assert results.main([*args, "--check"]) == 1
    assert results.main(args) == 0
    job_path = jobs / "base.json"
    job = json.loads(job_path.read_text())
    job["wall_seconds"] += 1
    job_path.write_text(json.dumps(job))
    assert results.main([*args, "--check"]) == 1
    assert results.main(args) == 0
    svg.unlink()
    assert results.main([*args, "--check"]) == 1


def test_custom_output_links_to_shared_chart(records):
    root, _, _ = records
    output = root / "preview/page.md"
    assert results.main(["--out", str(output)]) == 0
    assert 'src="../docs/results/sweep.svg"' in output.read_text()
