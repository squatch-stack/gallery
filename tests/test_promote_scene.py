"""Tests for promoting SOG candidates into a temporary gallery."""

import importlib.util
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "promote_scene", Path(__file__).parents[1] / "tools/promote_scene.py"
)
promote_scene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promote_scene)


def fake_sog(path, count=42):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("meta.json", json.dumps({"version": 2, "count": count}))


@pytest.fixture
def gallery(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "scenes.json").write_text("[]\n")
    return tmp_path


def test_promotes_sog_and_sibling(gallery, tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.sog"
    fake_sog(candidate, 73)
    candidate.with_suffix(".spz").write_bytes(b"spz")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(promote_scene.subprocess, "run", run)
    assert promote_scene.main([
        str(candidate), "--stem", "new-scene", "--title", "New Scene",
        "--blurb", "A scan.", "--place", "Here", "--captured", "2026-09-04",
        "--up", "0,1,0",
    ], root=gallery) == 0
    entry = json.loads((gallery / "scenes.json").read_text())[0]
    assert entry == {"stem": "new-scene", "title": "New Scene", "place": "Here",
                     "captured": "2026-09-04", "splats": 73,
                     "blurb": "A scan.", "up": [0.0, 1.0, 0.0]}
    assert (gallery / "scenes/new-scene.sog").is_file()
    assert (gallery / "scenes/new-scene.spz").read_bytes() == b"spz"
    assert "check_deliverable.py" in run.call_args.args[0][1]


def test_dry_run_changes_nothing(gallery, tmp_path, capsys):
    candidate = tmp_path / "candidate.sog"
    fake_sog(candidate)
    before = (gallery / "scenes.json").read_text()
    assert promote_scene.main([
        str(candidate), "--stem", "preview", "--title", "Preview",
        "--blurb", "Only a preview.", "--dry-run", "--up", "0,1,0",
    ], root=gallery) == 0
    assert (gallery / "scenes.json").read_text() == before
    assert not (gallery / "scenes/preview.sog").exists()
    assert '+    "stem": "preview"' in capsys.readouterr().out


def test_replace_archives_files_and_preserves_unknown_keys(gallery, tmp_path, monkeypatch):
    old = {"stem": "same", "title": "Old", "splats": 2, "custom": {"keep": True}}
    (gallery / "scenes.json").write_text(json.dumps([old]))
    fake_sog(gallery / "scenes/same.sog", 2)
    (gallery / "scenes/same.spz").write_bytes(b"old")
    candidate = tmp_path / "fresh.sog"
    fake_sog(candidate, 99)
    monkeypatch.setattr(promote_scene.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    assert promote_scene.main([
        str(candidate), "--stem", "same", "--title", "Fresh",
        "--blurb", "Replacement.", "--replace", "--up", "0,1,0",
    ], root=gallery) == 0
    entry = json.loads((gallery / "scenes.json").read_text())[0]
    assert entry["custom"] == {"keep": True}
    today = promote_scene.dt.datetime.now().astimezone().date().isoformat()
    archive = gallery / "archive/replaced" / f"same-{today}"
    assert (archive / "same.sog").is_file()
    assert (archive / "same.spz").read_bytes() == b"old"
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 99


def test_app_export_replacement_regenerates_from_sidecar(gallery, tmp_path, monkeypatch, capsys):
    old = {"stem": "same", "title": "Original", "splats": 2,
           "provenance": "provenance/same.html"}
    (gallery / "scenes.json").write_text(promote_scene.catalog_text([old]))
    fake_sog(gallery / "scenes/same.sog", 2)
    (gallery / "provenance").mkdir()
    (gallery / "provenance/same.html").write_text("old page")
    (gallery / "provenance/same.json").write_text(json.dumps({
        "schema_version": 1,
        "argv": [],
        "mode": "app-export",
        "date": "2026-09-04T00:00:00+00:00",
        "inputs": {
            "source": "Old operator source",
            "source_commit": ["abc123"],
            "cleaning": ["old flags"],
            "note": ["Old evidence note"],
        },
    }))
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "same.sog"
    fake_sog(candidate, 99)
    (candidate_dir / "attempt.json").write_text(json.dumps({"cleaning_flags": "--alpha-min 0.07"}))

    commands = []

    def run(command, check=False):
        commands.append(command)
        if "provenance.py" in command[1]:
            out = Path(command[command.index("--out") + 1])
            source = command[command.index("--source") + 1]
            cleaning = command[command.index("--cleaning") + 1]
            out.write_text(f"{source}\n{cleaning}\n")
            out.with_suffix(".json").write_text("{}\n")
        return Mock(returncode=0)

    monkeypatch.setattr(promote_scene.subprocess, "run", run)
    assert replace(gallery, candidate) == 0
    page = (gallery / "provenance/same.html").read_text()
    assert "Old operator source" in page
    assert "--alpha-min 0.07" in page
    assert "old flags" not in page
    provenance_command = next(command for command in commands if "provenance.py" in command[1])
    assert provenance_command[provenance_command.index("--source-commit") + 1] == "abc123"
    assert "using cleaning flags from" in capsys.readouterr().out
    archive = promote_scene.archives_for(gallery, "same")[-1]
    assert (archive / "provenance/same.json").is_file()


def test_app_export_replacement_refuses_without_sidecar_or_cleaning(gallery, tmp_path, capsys):
    old = {"stem": "same", "title": "Original", "splats": 2,
           "provenance": "provenance/same.html"}
    (gallery / "scenes.json").write_text(promote_scene.catalog_text([old]))
    fake_sog(gallery / "scenes/same.sog", 2)
    (gallery / "provenance").mkdir()
    (gallery / "provenance/same.html").write_text(
        "<p><b>Operator source summary:</b> Old source</p>"
        "<p><b>Source commit:</b> abc123</p>"
    )
    candidate = tmp_path / "candidate.sog"
    fake_sog(candidate, 99)
    before = (gallery / "scenes/same.sog").read_bytes()
    with pytest.raises(SystemExit):
        replace(gallery, candidate)
    assert (gallery / "scenes/same.sog").read_bytes() == before
    assert "--cleaning \"<flags>\"" in capsys.readouterr().err


def test_solved_replacement_still_uses_solve_provenance_path(gallery, tmp_path, monkeypatch):
    old = {"stem": "same", "title": "Original", "splats": 2,
           "provenance": "provenance/same.html"}
    (gallery / "scenes.json").write_text(promote_scene.catalog_text([old]))
    fake_sog(gallery / "scenes/same.sog", 2)
    (gallery / "provenance").mkdir()
    (gallery / "provenance/same.html").write_text("solved provenance")
    (gallery / "provenance/same.json").write_text(json.dumps({
        "schema_version": 1, "mode": "solve", "argv": [], "date": "2026-09-04", "inputs": {},
    }))
    candidate = tmp_path / "candidate.sog"
    fake_sog(candidate, 99)
    trained = tmp_path / "metrics.json"
    trained.write_text("{}")
    subject = tmp_path / "subject"
    subject.mkdir()
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(promote_scene.subprocess, "run", run)

    assert replace(gallery, candidate, "--provenance-from", str(subject), "--trained", str(trained)) == 0
    provenance_command = run.call_args_list[0].args[0]
    assert "--app-export" not in provenance_command
    assert provenance_command[2] == str(subject)
    assert provenance_command[provenance_command.index("--trained") + 1] == str(trained)


def test_existing_scene_requires_replace(gallery, tmp_path):
    (gallery / "scenes.json").write_text(json.dumps([{"stem": "same"}]))
    candidate = tmp_path / "candidate.sog"
    fake_sog(candidate)
    with pytest.raises(SystemExit):
        promote_scene.main([
            str(candidate), "--stem", "same", "--title", "Same", "--blurb", "No.",
        ], root=gallery)


def seed_replacement(gallery, monkeypatch):
    old = {"stem": "same", "title": "Original", "splats": 2, "up": [0, 1, 0],
           "provenance": "provenance/same.html", "custom": {"keep": True}}
    catalog = [{"stem": "before"}, old, {"stem": "after"}]
    (gallery / "scenes.json").write_text(promote_scene.catalog_text(catalog))
    fake_sog(gallery / "scenes/same.sog", 2)
    (gallery / "scenes/same.spz").write_bytes(b"original spz")
    (gallery / "provenance").mkdir()
    (gallery / "provenance/same.html").write_text("old page")
    candidate = gallery / "candidate.sog"
    fake_sog(candidate, 99)
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(promote_scene.subprocess, "run", run)
    return catalog, candidate, run


def replace(gallery, candidate, *extra):
    return promote_scene.main([str(candidate), "--stem", "same", "--title", "Replacement",
                               "--blurb", "New", "--replace", "--up", "0,1,0", *extra], root=gallery)


def test_archive_round_trip(gallery, monkeypatch, capsys):
    catalog, candidate, run = seed_replacement(gallery, monkeypatch)
    before = {p.relative_to(gallery): p.read_bytes() for folder in ("scenes", "provenance")
              for p in (gallery / folder).iterdir()}
    assert replace(gallery, candidate) == 0
    archive = promote_scene.archives_for(gallery, "same")[-1]
    assert json.loads((archive / "entry.json").read_text()) == catalog[1]
    (gallery / "provenance/same.html").write_text("new page")
    replaced_catalog = json.loads((gallery / "scenes.json").read_text())
    assert promote_scene.main(["--revert", "same"], root=gallery) == 0
    assert json.loads((gallery / "scenes.json").read_text()) == catalog
    assert all((gallery / path).read_bytes() == data for path, data in before.items())
    displaced = promote_scene.archives_for(gallery, "same")[-1]
    assert displaced != archive
    assert (displaced / "same.sog").read_bytes() == candidate.read_bytes()
    assert (displaced / "provenance/same.html").read_text() == "new page"
    assert json.loads((displaced / "entry.json").read_text()) == replaced_catalog[1]
    assert run.call_count == 2
    assert run.call_args.args[0][-1] == "same"
    assert "verdict: PASS" in capsys.readouterr().out


def test_legacy_entry_fallback_and_failure_verdict(gallery, monkeypatch, capsys):
    _, candidate, run = seed_replacement(gallery, monkeypatch)
    replace(gallery, candidate)
    archive = promote_scene.archives_for(gallery, "same")[-1]
    (archive / "entry.json").unlink()
    (archive / "provenance/same.html").unlink()
    current = (gallery / "scenes.json").read_bytes()
    run.return_value.returncode = 1
    assert promote_scene.main(["--revert", "same"], root=gallery) == 1
    assert (gallery / "scenes.json").read_bytes() == current
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 2
    assert (gallery / "provenance/same.html").read_text() == "old page"
    output = capsys.readouterr().out
    assert "no entry.json; keeping the current catalog entry" in output
    assert "verdict: FAIL" in output


def test_revert_dry_run_and_explicit_archive(gallery, monkeypatch, capsys):
    _, candidate, run = seed_replacement(gallery, monkeypatch)
    replace(gallery, candidate)
    first = promote_scene.archives_for(gallery, "same")[-1]
    fake_sog(candidate, 100)
    replace(gallery, candidate)
    assert len(promote_scene.archives_for(gallery, "same")) == 2
    before = {p.relative_to(gallery): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in gallery.rglob("*") if p.is_file()}
    args = ["--revert", "same", "--from", str(first.relative_to(gallery))]
    assert promote_scene.main([*args, "--dry-run"], root=gallery) == 0
    assert before == {p.relative_to(gallery): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in gallery.rglob("*") if p.is_file()}
    assert run.call_count == 2
    assert "move archive/replaced/" in capsys.readouterr().out
    assert promote_scene.main(args, root=gallery) == 0
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 2


def test_revert_latest_and_no_archive(gallery, monkeypatch):
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main(["--revert", "same"], root=gallery)
    _, candidate, _ = seed_replacement(gallery, monkeypatch)
    replace(gallery, candidate)
    fake_sog(candidate, 100)
    replace(gallery, candidate)
    assert promote_scene.main(["--revert", "same"], root=gallery) == 0
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 99


def test_replace_dry_run_and_wrong_archive(gallery, monkeypatch):
    _, candidate, _ = seed_replacement(gallery, monkeypatch)
    assert replace(gallery, candidate, "--dry-run") == 0
    assert not (gallery / "archive").exists()
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 2
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main(["--revert", "same", "--from", "scenes"], root=gallery)


def test_revert_runs_real_checker(gallery, capfd):
    import shutil

    shutil.copy2(Path(__file__).parents[1] / "tools/check_deliverable.py", gallery / "tools/check_deliverable.py")
    archive = gallery / "archive/replaced/same-2026-09-01"
    archive.mkdir(parents=True)
    fake_sog(archive / "same.sog", 2)
    (archive / "entry.json").write_text(json.dumps({"stem": "same", "splats": 2}))
    fake_sog(gallery / "scenes/same.sog", 99)
    (gallery / "scenes.json").write_text(json.dumps([{"stem": "same", "splats": 99}]))
    # The header-only test SOG lacks geometry: the real checker must fail closed.
    assert promote_scene.main(["--revert", "same"], root=gallery) == 1
    assert promote_scene.sog_count(gallery / "scenes/same.sog") == 2
    output = capfd.readouterr().out
    assert "[FAIL] format:" in output
    assert "check_deliverable verdict: FAIL" in output


def promotion_args(candidate):
    return [str(candidate), "--stem", "ground", "--title", "Ground", "--blurb", "Scan"]


@pytest.mark.parametrize("dry_run", [False, True])
def test_cloud_promotes_known_up(gallery, monkeypatch, capsys, dry_run):
    from test_scene_up import angle, cloud, rotation, write_cloud

    positions, alpha, expected = cloud(rotation([0.7, 0.2, -0.4, 0.5]))
    candidate = gallery / "ground.sog"
    write_cloud(candidate, positions * [1, -1, -1], alpha)
    monkeypatch.setattr(promote_scene.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    before = {p.relative_to(gallery): p.read_bytes() for p in gallery.rglob("*") if p.is_file()}
    args = promotion_args(candidate) + (["--dry-run"] if dry_run else [])
    assert promote_scene.main(args, root=gallery) == 0
    assert "up (cloud):" in capsys.readouterr().out
    if dry_run:
        assert before == {p.relative_to(gallery): p.read_bytes() for p in gallery.rglob("*") if p.is_file()}
    else:
        entry = json.loads((gallery / "scenes.json").read_text())[0]
        assert angle(entry["up"], expected) < 2
        inputs = json.loads((gallery / "provenance/ground.json").read_text())["inputs"]
        assert inputs["up_source"] == "cloud"
        assert inputs["up"] == entry["up"]
        assert 0.40 < inputs["up_confidence"]["inliers"] < 0.46
        assert inputs["up_confidence"]["footprint_ratio"] >= 0.25


@pytest.mark.parametrize("subject", [False, True])
def test_no_plane_refuses_before_writes(gallery, monkeypatch, capsys, subject):
    import sys
    import types

    import numpy as np
    from test_scene_up import write_cloud

    candidate = gallery / "volume.sog"
    write_cloud(candidate, np.random.default_rng(4).normal(size=(4200, 3)), np.full(4200, 0.9))
    pose = types.SimpleNamespace(rotation=types.SimpleNamespace(matrix=lambda: np.eye(3)))
    monkeypatch.setitem(sys.modules, "pycolmap", types.SimpleNamespace(
        Reconstruction=lambda path: types.SimpleNamespace(
            images={0: types.SimpleNamespace(cam_from_world=lambda: pose)})))
    before = {p.relative_to(gallery): p.read_bytes() for p in gallery.rglob("*") if p.is_file()}
    args = promotion_args(candidate)
    if subject:
        args += ["--provenance-from", "subject", "--trained", "metrics.json"]
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main(args, root=gallery)
    error = capsys.readouterr().err
    assert "Promotion refused; pass --up=x,y,z after inspection to proceed." in error
    if subject:
        for axis in promote_scene.scene_up.AXES:
            assert f"  {axis}: [" in error
        assert "stage with --up=" in error
        assert "viewer.html?scene=ground&az=30&el=10&d=1.6" in error
        assert "cannot inspect an unpromoted candidate" in error
    assert before == {p.relative_to(gallery): p.read_bytes() for p in gallery.rglob("*") if p.is_file()}


def test_explicit_bypasses_estimate_and_records_source(gallery, monkeypatch):
    candidate = gallery / "explicit.sog"
    fake_sog(candidate)
    estimate = Mock(side_effect=AssertionError("must not estimate"))
    monkeypatch.setattr(promote_scene.scene_up, "cloud_estimate", estimate)
    monkeypatch.setattr(promote_scene.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    assert promote_scene.main([*promotion_args(candidate), "--up=1,0,0"], root=gallery) == 0
    estimate.assert_not_called()
    inputs = json.loads((gallery / "provenance/ground.json").read_text())["inputs"]
    assert inputs == {"up_source": "explicit", "up": [1, 0, 0]}


@pytest.mark.parametrize(("inliers", "extent", "threshold", "accepted"), [
    (0.35, [1, 4], 0.35, True), (0.349, [1, 4], 0.35, False),
    (0.9, [0.99, 4], 0.35, False), (0.4, [4, 4], 0.5, False),
    (0.3, [4, 4], 0.25, True),
])
def test_cloud_acceptance_boundaries(gallery, monkeypatch, inliers, extent, threshold, accepted):
    candidate = gallery / "boundary.sog"
    fake_sog(candidate)
    monkeypatch.setattr(promote_scene.scene_up, "cloud_estimate", lambda path: {
        "up": [0, 1, 0], "reason": None, "inliers": inliers, "extent": extent})
    args = [*promotion_args(candidate), "--dry-run", "--up-min-inliers", str(threshold)]
    if accepted:
        assert promote_scene.main(args, root=gallery) == 0
    else:
        with pytest.raises(SystemExit, match="2"):
            promote_scene.main(args, root=gallery)


@pytest.mark.parametrize("threshold", ["nan", "inf", "-0.1", "1.1"])
def test_invalid_inlier_threshold(threshold):
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main(["--up-min-inliers", threshold])


def test_up_only_sidecar_round_trip(gallery, monkeypatch):
    candidate = gallery / "ground.sog"
    fake_sog(candidate)
    monkeypatch.setattr(promote_scene.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    args = promotion_args(candidate)
    assert promote_scene.main([*args, "--up=0,1,0"], root=gallery) == 0
    sidecar = gallery / "provenance/ground.json"
    original = sidecar.read_bytes()
    assert promote_scene.main([*args, "--replace", "--up=1,0,0"], root=gallery) == 0
    assert sidecar.read_bytes() != original
    assert promote_scene.main(["--revert", "ground"], root=gallery) == 0
    assert sidecar.read_bytes() == original


def test_failed_estimate_does_not_reuse_existing_up(gallery, monkeypatch, capsys):
    _, candidate, _ = seed_replacement(gallery, monkeypatch)
    with pytest.raises(SystemExit, match="2"):
        promote_scene.main([*promotion_args(candidate), "--stem", "same", "--replace"], root=gallery)
    assert "cloud estimate failed:" in capsys.readouterr().err
    assert not (gallery / "archive").exists()
