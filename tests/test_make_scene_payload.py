"""Exercise payload construction with a tiny, pycolmap-free reconstruction double."""

import importlib.util
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest

Image = pytest.importorskip("PIL.Image", reason="payload fixtures require Pillow")
pytest.importorskip("numpy", reason="alpha-mask filtering requires numpy")


@pytest.fixture
def payload(tmp_path, monkeypatch):
    subject = tmp_path / "oak"
    (subject / "sparse/0").mkdir(parents=True)
    (subject / "sparse/0/cameras.bin").touch()
    (subject / "images").mkdir()
    (subject / "masks").mkdir()
    camera = SimpleNamespace(width=4, height=4)
    images = {}
    for i in reversed(range(20)):
        name = f"shot-{i:02}.jpg"
        images[i] = SimpleNamespace(name=name, frame_id=i, camera_id=1)
        Image.new("RGB", (4, 4), "red").save(subject / "images" / name)
        mask = Image.new("L", (4, 4), 0)
        mask.paste(255, (0, 0, 2, 4))
        mask.save(subject / "masks" / f"shot-{i:02}.png")

    class Reconstruction:
        def __init__(self, _path):
            self.images = images.copy()
            self.cameras = {1: camera}
            self.dropped = []

        def num_images(self):
            return len(self.images)

        def num_points3D(self):
            return 0

        def deregister_frame(self, frame_id):
            self.dropped.append(frame_id)
            del self.images[frame_id]

        def find_image_with_name(self, name):
            return next(im for im in self.images.values() if im.name == name)

        def write_binary(self, path):
            (path / "images.bin").write_text(json.dumps(sorted(im.name for im in self.images.values())))
            (path / "cameras.bin").write_bytes(b"fixture")

    monkeypatch.setitem(sys.modules, "pycolmap", SimpleNamespace(Reconstruction=Reconstruction))
    spec = importlib.util.spec_from_file_location("payload", Path(__file__).parents[1] / "tools/make_scene_payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "scene.tar"

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["make_scene_payload.py", str(subject), str(out), *args])
        module.main()
        with tarfile.open(out) as archive:
            return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}

    return subject, run


@pytest.mark.parametrize("limit", [8, 12, 20, 24])
def test_limit_even_filename_order_and_solve(payload, limit, capsys):
    _, run = payload
    contents = run("--limit", str(limit))
    count = min(limit, 20)
    expected = [f"shot-{round(i * 19 / (count - 1)):02}.jpg" for i in range(count)]
    assert contents["oak/names.txt"].decode().splitlines() == expected
    assert json.loads(contents["oak/sparse/0/images.bin"]) == expected
    assert f"dropped {20 - count} view(s) -> {count} views" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--embed-images", "--masks"])
def test_degenerate_filter_precedes_limit(payload, mode):
    subject, run = payload
    Image.new("L", (4, 4), 255).save(subject / "masks/shot-00.png")
    Image.new("L", (4, 4), 0).save(subject / "masks/shot-19.png")
    contents = run(mode, "--alpha-from-masks", "--limit", "8")
    expected = [f"shot-{1 + round(i * 17 / 7):02}" for i in range(8)]
    assert contents["oak/names.txt"].decode().splitlines() == [f"{n}.jpg" for n in expected]
    ext = ".png" if mode == "--embed-images" else ".jpg"
    assert json.loads(contents["oak/sparse/0/images.bin"]) == [n + ext for n in expected]
    folder = "images" if mode == "--embed-images" else "masks"
    assert sorted(n for n in contents if f"/{folder}/" in n) == [f"oak/{folder}/{n}.png" for n in expected]
    if mode == "--masks":
        assert not any("/images/" in n for n in contents)


@pytest.mark.parametrize("limit", ["0", "7", "-1"])
def test_reject_limit_below_eight(payload, limit, capsys):
    _, run = payload
    with pytest.raises(SystemExit, match="2"):
        run("--limit", limit)
    assert "--limit must be at least 8" in capsys.readouterr().err
