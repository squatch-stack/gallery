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
    (directory / "isolation.json").write_text(json.dumps({"arms": dict.fromkeys(results.ARMS)}))
    (tmp_path / "scenes.json").write_text(
        json.dumps(
            [
                {"stem": "fixture", "subject": "place"},
                {"stem": "missing"},
            ]
        )
    )
    (tmp_path / "checks.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "scene": "fixture",
                        "count": 480000,
                        "size_bytes": 1234,
                        "passed": False,
                        "platform": "web-mobile",
                    },
                ]
            }
        )
    )
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance/fixture.json").write_text(
        json.dumps(
            {
                "inputs": {
                    "cleaning": ["--target-count 480000", "--crop-shape cylinder"],
                    "cleaning_source": "candidate",
                }
            }
        )
    )
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
    assert output.read_text() == original
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


def test_new_sections(records):
    root, index, jobs = records
    isolation = index.parent / "isolation.json"
    data = json.loads(isolation.read_text())
    data["arms"]["alpha-matched"] = {
        "within_mad": {"3": 0.75},
        "long_axis_fraction": {"0.25": 0.25, "1.0": 0.125},
    }
    isolation.write_text(json.dumps(data))
    page, _ = results.generate(index, jobs, "results/sweep.svg")
    assert "| alpha-matched | 75.00% | 25.00% | 12.50% |" in page
    assert "| unmasked | unavailable | unavailable | unavailable |" in page
    assert "| fixture | place | 480,000 | not applicable | not applicable | 1,234 | FAIL (web-mobile) | " in page
    assert "--target-count 480000; --crop-shape cylinder | candidate |" in page
    assert ("| missing | object | unavailable | not applicable | not applicable | unavailable | "
            "unavailable (unavailable) | unavailable |") in page
    assert "L-infinity" in page
    assert str(root) not in page


@pytest.mark.parametrize("source", ["isolation", "checks", "catalog", "sidecar"])
def test_new_sources_freshness(records, source):
    root, index, _ = records
    args = ["--out", str(root / "docs/results.md")]
    assert results.main(args) == 0
    assert results.main([*args, "--check"]) == 0
    paths = {
        "isolation": index.parent / "isolation.json",
        "checks": root / "checks.json",
        "catalog": root / "scenes.json",
        "sidecar": root / "provenance/fixture.json",
    }
    path = paths[source]
    data = json.loads(path.read_text())
    if source == "isolation":
        data["arms"]["unmasked"] = {
            "within_mad": {"3": 0.5},
            "long_axis_fraction": {"0.25": 0, "1.0": 0},
        }
    elif source == "checks":
        data["results"][0]["passed"] = True
    elif source == "catalog":
        data[0]["subject"] = "object"
    else:
        data["inputs"]["cleaning"].append("--alpha-min 0.05")
    path.write_text(json.dumps(data))
    assert results.main([*args, "--check"]) == 1
    assert results.main(args) == 0
    assert results.main([*args, "--check"]) == 0


@pytest.fixture
def mesh_records(records):
    root, _, _ = records
    catalog_path = root / "scenes.json"
    catalog = json.loads(catalog_path.read_text())
    catalog.append({"stem": "mesh", "mesh": "scenes/mesh.glb", "triangles": 999})
    catalog_path.write_text(json.dumps(catalog))
    checks_path = root / "checks.json"
    checks = json.loads(checks_path.read_text())
    checks["results"].append({
        "scene": "mesh", "file": "scenes/mesh.glb", "count": 0,
        "triangles": 123456, "texture_bytes": 2345678, "size_bytes": 3456789,
        "passed": True, "platform": "web-mobile",
    })
    checks_path.write_text(json.dumps(checks))
    return records


def test_mesh_cleaning_evidence(mesh_records):
    _, index, jobs = mesh_records
    page, _ = results.generate(index, jobs, "results/sweep.svg")
    assert ("| mesh | object | not applicable | 123,456 | 2,345,678 | 3,456,789 | "
            "PASS (web-mobile) | unavailable | unavailable |") in page


@pytest.mark.parametrize("value", [None, 0])
def test_mesh_missing_and_zero_metrics(mesh_records, value):
    root, _, _ = mesh_records
    path = root / "checks.json"
    data = json.loads(path.read_text())
    record = data["results"][-1]
    for key in ("triangles", "texture_bytes"):
        if value is None:
            record.pop(key)
        else:
            record[key] = value
    path.write_text(json.dumps(data))
    expected = "unavailable" if value is None else "0"
    assert f"| mesh | object | not applicable | {expected} | {expected} |" in "\n".join(
        results.cleaning_section(root)
    )


@pytest.mark.parametrize("metric", ["triangles", "texture_bytes"])
def test_mesh_metrics_freshness(mesh_records, metric):
    root, _, _ = mesh_records
    args = ["--out", str(root / "docs/results.md")]
    assert results.main(args) == 0
    assert results.main([*args, "--check"]) == 0
    path = root / "checks.json"
    data = json.loads(path.read_text())
    data["results"][-1][metric] += 1
    path.write_text(json.dumps(data))
    assert results.main([*args, "--check"]) == 1
    assert results.main(args) == 0
    assert results.main([*args, "--check"]) == 0


@pytest.mark.parametrize("local_only", [False, True])
def test_local_reconstructed_row(records, tmp_path, local_only):
    import os
    from tools import local_run_metrics

    _, index, jobs = records
    job_dir = tmp_path / "local-job"
    dataset = job_dir / "dataset"
    (dataset / "images").mkdir(parents=True)
    for i in range(3):
        (dataset / "images" / f"{i}.jpg").write_bytes(b"fixture")
    ply = job_dir / "oak-30000.ply"
    ply.write_bytes(b"ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nend_header\n0\n0\n")
    end = dataset.stat().st_ctime + 120
    os.utime(ply, (end, end))
    local_run_metrics.main([
        str(job_dir), "--dataset", str(dataset), "--out", str(jobs / "local.json"),
        "--max-res", "1920", "--seed", "0", "--max-splats", "2000",
    ])
    data = json.loads(index.read_text())
    if local_only:
        data["runs"] = {}
    data["runs"]["local"] = {"subject": "oak", "arm": "unmasked"}
    index.write_text(json.dumps(data))
    _, rows = results.load_runs(index, jobs)
    assert any(i == "local" for i, _, _ in rows)
    page, svg = results.generate(index, jobs, "results/sweep.svg")
    assert ("| local[^local-wall] | oak | Brush 0.3.0 (Mac, Metal) | 30,000 | 1920 | 0 | 2,000 | 3 | "
            "4.00 | 120.0 | 2 | unavailable |") in page
    assert "[^local-wall]: Wall time is an estimate" in page
    assert "includes dataset build time" in page
    assert "local" not in svg
    path = jobs / "local.json"
    local = json.loads(path.read_text())
    local["rc"] = 1
    path.write_text(json.dumps(local))
    with pytest.raises(ValueError, match="failed job"):
        results.load_runs(index, jobs)
