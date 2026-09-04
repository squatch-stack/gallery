"""Help must work offline with a bare interpreter and must not start work."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("tool", sorted((ROOT / "tools").glob("*.py")), ids=lambda path: path.name)
@pytest.mark.parametrize("bare", [False, True], ids=["normal", "bare"])
def test_every_python_tool_help(tool, bare, tmp_path):
    env = dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, *(["-S"] if bare else []), str(tool), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower()
    assert "--help" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["refresh_checks.sh", "gate.sh"])
def test_shell_help_does_no_work(name, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / name
    script.write_bytes((ROOT / "tools" / name).read_bytes())
    snapshot = tmp_path / "checks.json"
    snapshot.write_text("keep this snapshot\n")
    before = snapshot.stat()
    # Any attempt to select/run an interpreter or gate stage must fail.
    env = dict(os.environ, PYTHON="/nonexistent-python", PATH="")
    result = subprocess.run(
        ["/bin/sh", str(script), "--help"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout
    assert not result.stderr
    assert snapshot.read_text() == "keep this snapshot\n"
    assert snapshot.stat().st_mtime_ns == before.st_mtime_ns
    assert sorted(p.name for p in tmp_path.iterdir()) == ["checks.json", "tools"]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_solve_legacy_positional_defaults():
    assert vars(load_tool("solve_subject").parse_args(["~/Documents/subject"])) == {
        "subject": "~/Documents/subject", "out": "sparse", "features": 8192, "image_size": 2400,
        "global_mapping": False, "relaxed": False, "guided": False, "sequential": False, "remap": False,
    }


def test_solve_legacy_positional_flags():
    assert vars(load_tool("solve_subject").parse_args([
        "~/Documents/subject", "--global", "--out", "sparse-global", "--features", "16000",
        "--image-size", "1920", "--guided", "--sequential", "--relaxed", "--remap",
    ])) == {
        "subject": "~/Documents/subject", "out": "sparse-global", "features": 16000, "image_size": 1920,
        "global_mapping": True, "relaxed": True, "guided": True, "sequential": True, "remap": True,
    }


@pytest.mark.parametrize("sparse", [[], ["sparse-global"]])
@pytest.mark.parametrize("axis", [None, "-y", "+y", "-x", "+x"])
def test_scene_up_legacy_positionals(sparse, axis):
    argv = ["~/Documents/subject", *sparse, *([] if axis is None else ["--axis", axis])]
    assert vars(load_tool("scene_up").parse_args(argv)) == {
        "subject": "~/Documents/subject", "sparse": sparse[0] if sparse else "sparse", "axis": axis or "-y",
    }


def test_scan_help_does_not_scan(monkeypatch, capsys):
    tool = load_tool("scan_paths")

    def forbidden():
        pytest.fail("--help must not scan tracked files")

    monkeypatch.setattr(tool, "findings", forbidden)
    with pytest.raises(SystemExit) as result:
        tool.main(["--help"])
    assert result.value.code == 0
    assert "usage:" in capsys.readouterr().out
