from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("insta360_intake", ROOT / "tools" / "insta360_intake.py")
assert SPEC and SPEC.loader
intake = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = intake
SPEC.loader.exec_module(intake)


XMP = b'''<?xpacket begin="">
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description xmlns:GPano="http://ns.google.com/photos/1.0/panorama/"
   xmlns:tiff="http://ns.adobe.com/tiff/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   GPano:ProjectionType="equirectangular" GPano:FullPanoWidthPixels="400"
   GPano:FullPanoHeightPixels="200" GPano:PoseHeadingDegrees="91.5"
   GPano:PosePitchDegrees="-1" GPano:PoseRollDegrees="2"
   tiff:Model="Insta360 X5"><xmp:CreatorTool>Insta360 Studio</xmp:CreatorTool></rdf:Description>
 </rdf:RDF>
</x:xmpmeta><?xpacket end="w"?>'''


def test_parse_synthetic_xmp_attributes_and_elements() -> None:
    values = intake.parse_xmp_packet(b"prefix" + XMP + b"suffix")
    assert values["ProjectionType"] == "equirectangular"
    assert values["FullPanoWidthPixels"] == "400"
    assert values["PoseHeadingDegrees"] == "91.5"
    assert values["Software"] == "Insta360 Studio"
    assert values["Model"] == "Insta360 X5"


def test_ready_jpeg_with_synthetic_xmp(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "pano.jpg"
    Image.new("RGB", (400, 200)).save(path)
    monkeypatch.setattr(intake, "read_exiftool", lambda unused: intake.parse_xmp_packet(XMP))
    report = intake.inspect(path)
    assert report["ready"] is True
    assert report["pose"] == {"heading": "91.5", "pitch": "-1", "roll": "2"}


def test_non_2_to_1_and_missing_projection_are_not_ready(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "flat.jpg"
    Image.new("RGB", (320, 200)).save(path)
    monkeypatch.setattr(intake, "read_exiftool", lambda unused: {})
    report = intake.inspect(path)
    assert report["ready"] is False
    assert len(report["missing"]) == 2


def test_insv_is_rejected_before_probe(tmp_path: Path) -> None:
    path = tmp_path / "camera.insv"
    path.write_bytes(b"not a real container")
    report = intake.inspect(path)
    assert report["ready"] is False
    assert ".insv" in report["missing"][0]
