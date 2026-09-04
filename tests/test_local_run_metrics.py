"""Offline reconstruction fixtures and error cases."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct

import pytest

SPEC = importlib.util.spec_from_file_location(
    "local_run_metrics", Path(__file__).parents[1] / "tools/local_run_metrics.py"
)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


@pytest.fixture
def local_job(tmp_path):
    job = tmp_path / "job"
    dataset = job / "dataset"
    images = dataset / "images"
    images.mkdir(parents=True)
    for name in ("one.jpg", "two.PNG", "three.jpeg"):
        (images / name).write_bytes(b"image fixture")
    (images / "notes.txt").write_text("not an image")
    for step, count in ((900, 1), (10000, 2)):
        (job / f"oak-{step}.ply").write_bytes(
            f"ply\nformat ascii 1.0\nelement vertex {count}\nproperty float x\nend_header\n".encode()
            + b"0\n" * count
        )
    start = dataset.stat().st_ctime
    os.utime(job / "oak-900.ply", (start + 900, start + 900))
    os.utime(job / "oak-10000.ply", (start + 120, start + 120))
    out = job / "metrics.json"
    argv = [str(job), "--dataset", str(dataset), "--out", str(out),
            "--max-res", "1920", "--seed", "42", "--max-splats", "1000"]
    return job, dataset, out, argv


def test_every_field(local_job):
    job, _, out, argv = local_job
    assert metrics.main(argv) == 0
    ply = job / "oak-10000.ply"
    assert json.loads(out.read_text()) == {
        "name": "job", "recipe": "splat-brush", "origin": "local",
        "trainer": "Brush 0.3.0 (Mac, Metal)", "photos": "embedded", "rc": None, "peak_rss_mb": None,
        "steps": 10000, "max_res": 1920, "seed": 42, "max_splats": 1000,
        "splats": 2, "ply_bytes": ply.stat().st_size, "ply_sha256": hashlib.sha256(ply.read_bytes()).hexdigest(),
        "images": 3, "wall_seconds": 120.0, "wall_seconds_estimate": True, "seconds_per_1k_steps": 12.0,
        "reconstructed": ["steps", "splats", "ply_bytes", "ply_sha256", "images",
                          "wall_seconds", "seconds_per_1k_steps"],
        "schema_note": metrics.SCHEMA_NOTE,
    }
    assert str(job) not in out.read_text()


def test_overrides_and_binary_count(local_job):
    job, dataset, out, argv = local_job
    binary = dataset / "sparse/0/images.bin"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(struct.pack("<Q", 7) + b"ignored")
    start = dataset.stat().st_ctime
    os.utime(job / "oak-10000.ply", (start + 120, start + 120))
    metrics.main([*argv, "--steps", "20000", "--name", "Oak", "--trainer", "Custom Brush", "--seed", "0"])
    data = json.loads(out.read_text())
    assert (data["steps"], data["name"], data["trainer"], data["seed"], data["images"]) == (
        20000, "Oak", "Custom Brush", 0, 7,
    )
    assert data["seconds_per_1k_steps"] == 6
    assert data["reconstructed"] == ["splats", "ply_bytes", "ply_sha256", "images",
                                      "wall_seconds", "seconds_per_1k_steps"]


@pytest.mark.parametrize("flag", ["--max-res", "--seed", "--max-splats"])
def test_required_settings(local_job, flag):
    _, _, out, argv = local_job
    position = argv.index(flag)
    del argv[position:position + 2]
    with pytest.raises(SystemExit):
        metrics.main(argv)
    assert not out.exists()


@pytest.mark.parametrize("failure", ["no_exports", "ambiguous", "bad_header", "bad_clock", "bad_images"])
def test_invalid_inputs_do_not_write(local_job, failure):
    job, dataset, out, argv = local_job
    final = job / "oak-10000.ply"
    if failure == "no_exports":
        for ply in job.glob("*.ply"):
            ply.unlink()
    elif failure == "ambiguous":
        (job / "other-10000.ply").write_bytes(final.read_bytes())
    elif failure == "bad_header":
        final.write_bytes(b"ply\nelement vertex 2\n")
        start = dataset.stat().st_ctime
        os.utime(final, (start + 120, start + 120))
    elif failure == "bad_clock":
        os.utime(final, (1, 1))
    else:
        binary = dataset / "sparse/0/images.bin"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"bad")
    with pytest.raises(SystemExit) as exc:
        metrics.main(argv)
    assert exc.value.code == 1
    assert not out.exists()
