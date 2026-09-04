"""Exact isolation fixtures and shared PLY/SOG decoding coverage."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_deliverable as checker
import scene_stats as stats
from test_check_deliverable import ply, repo as repo, test_sog_decode as make_sog


@pytest.fixture
def shell():
    # Heavy centre fixes the median; shell mass fixes MAD at one.
    distances = np.array([0, 1, -1, 3, -3, 5, -5, 10, -10, 11, -11], dtype=float)
    pos = np.column_stack([distances] * 3) + np.array([2, 4, 6])
    alpha = np.array([0.9, 0.8, 0.8] + [0.01] * 8)
    scale = np.full((11, 3), 0.25)
    scale[0, 0], scale[1, 0] = 1, 2
    return pos, scale, alpha, np.zeros((11, 1))


def test_exact_shell(shell):
    result = stats.statistics(shell)
    assert result["center"] == [2, 4, 6]
    assert result["mad"] == 1
    assert result["within_mad"] == {"1": 3 / 11, "3": 5 / 11, "5": 7 / 11, "10": 9 / 11}
    assert result["long_axis_fraction"] == {"0.25": 2 / 11, "1.0": 1 / 11}
    assert result["long_axis_percentiles"] == {"p50": 0.25, "p90": 1.0, "p99": 1.9000000000000004}
    checked = checker.cleanliness(shell)
    assert result["center"] == checked["center"]
    assert result["mad"] == checked["mad"]
    assert result["within_mad"]["3"] == pytest.approx(1 - checked["floater_fraction"])


def test_degenerate_and_nonfinite(shell):
    pos, _scale, alpha, raw = shell
    pos[:] = [2, 4, 6]
    raw[0] = np.nan
    result = stats.statistics(shell)
    assert result["finite_splats"] == 10
    assert result["nonfinite_count"] == 1
    assert result["mad"] == 0
    assert set(result["within_mad"].values()) == {1.0}
    alpha[:] = 0
    assert stats.statistics(shell)["mad"] is None
    raw[:] = np.nan
    result = stats.statistics(shell)
    assert result["finite_splats"] == 0
    assert set(result["long_axis_fraction"].values()) == {None}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_ply_cli(repo, encoding, capsys):
    path = ply(repo, encoding=encoding, dirty=True, nonfinite=True)
    expected = stats.statistics(checker.read_ply(path)[1])
    assert stats.main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert stats.main([str(path)]) == 0
    assert "Fraction within 10x MAD:" in capsys.readouterr().out


def test_sog(repo):
    make_sog(repo)
    path = repo / "scenes/test.sog"
    result = stats.scene_stats(path)
    checked = checker.cleanliness(checker.read_sog(path)[1])
    assert result["splats"] == 64
    assert result["center"] == checked["center"]
    assert result["mad"] == checked["mad"]
    assert result["within_mad"]["3"] == 1 - checked["floater_fraction"]


def test_bad_input(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        stats.main([str(tmp_path / "missing.ply"), "--json"])
    assert error.value.code == 1
    assert capsys.readouterr().out == ""
    with pytest.raises(ValueError, match="PLY or SOG"):
        stats.scene_stats(tmp_path / "bad.spz")
