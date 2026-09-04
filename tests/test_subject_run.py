import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools/subject_run.py"


def write_stub_tools(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "stub_masks.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "from PIL import Image\n"
        "out = Path(sys.argv[1]) / 'masks'; out.mkdir()\n"
        "Image.new('L', (2, 2), 255).save(out / 'view.png')\n"
    )
    (tools / "stub_payload.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "out = Path(sys.argv[2]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b'tar')\n"
    )
    (tools / "stub_clean.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "stem = sys.argv[sys.argv.index('--stem') + 1]\n"
        "out = Path(sys.argv[sys.argv.index('--out') + 1]); archive = Path(sys.argv[sys.argv.index('--archive') + 1])\n"
        "out.mkdir(parents=True); archive.mkdir(parents=True)\n"
        "(out / (stem + '.sog')).write_bytes(b'sog'); (out / (stem + '.spz')).write_bytes(b'spz')\n"
        "(archive / (stem + '.ply')).write_bytes(b'ply')\n"
    )
    (tools / "stub_promote.py").write_text("raise SystemExit(0)\n")
    return tools


def command(subject, job, tools, *extra):
    return [
        sys.executable,
        str(SCRIPT),
        str(subject),
        str(job),
        "--stem",
        "test-subject",
        "--prompt",
        "statue",
        "--python",
        sys.executable,
        "--tools-dir",
        str(tools),
        "--mask-tool",
        str(tools / "stub_masks.py"),
        "--payload-tool",
        str(tools / "stub_payload.py"),
        "--clean-tool",
        str(tools / "stub_clean.py"),
        "--promote-tool",
        str(tools / "stub_promote.py"),
        *extra,
    ]


def subject_dir(tmp_path, masks=True):
    subject = tmp_path / "subject"
    (subject / "images").mkdir(parents=True)
    (subject / "images/view.jpg").write_bytes(b"photo")
    if masks:
        (subject / "masks").mkdir()
        Image.new("L", (2, 2), 255).save(subject / "masks/view.png")
    return subject


def test_no_submit_builds_payload_and_records_hashes(tmp_path):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path)
    job = tmp_path / "job"
    result = subprocess.run(command(subject, job, tools), text=True, capture_output=True)
    assert result.returncode == 0
    assert "submission not requested" in result.stdout
    record = json.loads((job / "run.json").read_text())
    assert [step["name"] for step in record["steps"]] == ["payload", "size_gate"]
    assert record["steps"][0]["exit_status"] == 0
    assert record["steps"][0]["produced"][0]["sha256"]
    assert record["masks"]["count"] == 1


def test_masks_only_run_when_absent(tmp_path):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path, masks=False)
    result = subprocess.run(command(subject, tmp_path / "job", tools), text=True, capture_output=True)
    assert result.returncode == 0
    record = json.loads((tmp_path / "job/run.json").read_text())
    assert [step["name"] for step in record["steps"]] == ["masks", "payload", "size_gate"]


def test_dry_run_prints_plan_and_writes_nothing(tmp_path):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path, masks=False)
    job = tmp_path / "job"
    result = subprocess.run(command(subject, job, tools, "--dry-run"), text=True, capture_output=True)
    assert result.returncode == 0
    assert all(name in result.stdout for name in ("masks:", "payload:", "submit:", "clean:", "promote:"))
    assert not job.exists()


@pytest.mark.parametrize("host", [False, True])
@pytest.mark.parametrize("views", [145, 8])
def test_size_gate_blocks_gpugate(tmp_path, host, views):
    tools = write_stub_tools(tmp_path)
    (tools / "stub_payload.py").write_text(
        "from pathlib import Path\nimport sys, tarfile, io\n"
        "p=Path(sys.argv[2]); p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"data=''.join(f'view-{{i}}.jpg\\n' for i in range({views})).encode()\n"
        "with tarfile.open(p, 'w') as t:\n"
        " m=tarfile.TarInfo('oak/names.txt'); m.size=len(data); t.addfile(m, io.BytesIO(data))\n"
        "with p.open('r+b') as f: f.truncate(542*1024*1024)\n"
    )
    marker = tmp_path / "called"
    shim = tmp_path / "stub_gpugate"
    shim.write_text(f"#!/bin/sh\ntouch {marker}\n")
    shim.chmod(0o755)
    result = subprocess.run(
        command(subject_dir(tmp_path), tmp_path / "job", tools, "--submit", "--gpugate", str(shim),
                *(["--host-composite"] if host else [])),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    if views == 145:
        assert "fewer views via --limit 68" in result.stdout
    else:
        assert "minimum is 8" in result.stdout
    assert not marker.exists()


def test_resume_at_clean_uses_gpugate_artifacts(tmp_path):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path)
    job = tmp_path / "job"
    result_dir = job / "gpugate-123"
    result_dir.mkdir(parents=True)
    (result_dir / "metrics.json").write_text("{}")
    (result_dir / "point_cloud.ply").write_bytes(b"ply")
    result = subprocess.run(command(subject, job, tools), text=True, capture_output=True)
    assert result.returncode == 0
    record = json.loads((job / "run.json").read_text())
    assert [step["name"] for step in record["steps"]] == ["clean", "promote"]
    assert (job / "scenes/test-subject.sog").is_file()
    promote = record["steps"][-1]["argv"]
    assert promote[promote.index("--supervision") + 1] == "alpha"
    assert promote[promote.index("--provenance-python") + 1] == sys.executable
    assert record["supervision"] == "alpha"
    assert record["provenance_python"] == sys.executable


@pytest.mark.parametrize("host", [False, True])
def test_submit_through_path_shim(tmp_path, host):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path)
    job = tmp_path / "job"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "gpugate-mac"
    shim.write_text(
        "#!/bin/sh\nmkdir gpugate-stub\nprintf '{}' > gpugate-stub/metrics.json\n"
        "printf ply > gpugate-stub/point_cloud.ply\n"
    )
    shim.chmod(0o755)
    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        command(subject, job, tools, "--submit", "--gpugate", "gpugate-mac",
                *(["--host-composite"] if host else [])),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    record = json.loads((job / "run.json").read_text())
    assert [step["name"] for step in record["steps"]] == [
        "payload",
        "size_gate",
        "submit",
        "clean",
        "promote",
    ]

    submit = record["steps"][2]["argv"]
    assert ("--alpha_from_masks=true" in submit) == host
    promote = record["steps"][-1]["argv"]
    assert promote[promote.index("--trained") + 1] == str(job / "gpugate-stub/metrics.json")
    assert promote[promote.index("--provenance-python") + 1] == sys.executable
    assert promote[promote.index("--supervision") + 1] == "alpha"


@pytest.mark.parametrize("host", [False, True])
def test_limit_and_host_composite(tmp_path, host):
    tools = write_stub_tools(tmp_path)
    subject = subject_dir(tmp_path)
    job = tmp_path / "job"
    extra = ["--host-composite"] if host else []
    result = subprocess.run(command(subject, job, tools, "--limit", "12", *extra),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    record = json.loads((job / "run.json").read_text())
    argv = record["steps"][0]["argv"]
    assert argv[argv.index("--limit") + 1] == "12"
    assert ("--masks" in argv) == host
    assert ("--embed-images" in argv) != host
    assert ("--alpha_from_masks=true" in result.stdout) == host
    assert "--alpha-from-masks" in argv
    assert record["supervision"] == "alpha"
    assert record["provenance_python"] == sys.executable


def test_invalid_limit_writes_nothing(tmp_path):
    tools = write_stub_tools(tmp_path)
    job = tmp_path / "job"
    result = subprocess.run(command(subject_dir(tmp_path), job, tools, "--limit", "7"),
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "--limit must be at least 8" in result.stderr
    assert not job.exists()
