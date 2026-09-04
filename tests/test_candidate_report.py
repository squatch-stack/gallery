"""Tests for the candidate review report."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "candidate_report", Path(__file__).parents[1] / "tools/candidate_report.py"
)
candidate_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate_report)


def result(count, passed):
    return {
        "count": count, "size_bytes": count * 10, "passed": passed,
        "metrics": {"floater_fraction": 0.001, "fog_fraction": 0.002,
                    "translucent_fraction": 0.003, "nonfinite_count": 0},
    }


def test_builds_comparison_attempts_and_exact_promotion(tmp_path):
    scene = {"stem": "sample", "title": "A Sample", "place": "Somewhere",
             "captured": "2026-01-02", "splats": 600, "blurb": "It's real.",
             "up": [0, 1, 0]}
    (tmp_path / "scenes.json").write_text(json.dumps([scene]))
    (tmp_path / "checks.json").write_text(json.dumps({"results": [
        {"scene": "sample", **result(600, False)}
    ]}))
    folder = tmp_path / "out/candidates/sample"
    folder.mkdir(parents=True)
    (folder / "sample.sog").write_bytes(b"candidate")
    manifest = {"stem": "sample", "attempts": [
        {"flags": "--crop-quantile 0.8", "result": result(550, False)},
        {"flags": "--crop-quantile 0.7", "result": result(490, True)},
    ], "selected": {"flags": "--crop-quantile 0.7", "result": result(490, True)}}
    (folder / "candidate.json").write_text(json.dumps(manifest))

    report = candidate_report.build_report(tmp_path / "out/candidates", root=tmp_path)
    assert "| Current | 600 | 6,000" in report
    assert "| Candidate | 490 | 4,900" in report
    assert "tools/promote_scene.py out/candidates/sample/sample.sog" in report
    assert "--blurb 'It'\"'\"'s real.'" in report
    assert report.count("--replace --dry-run") == 1
