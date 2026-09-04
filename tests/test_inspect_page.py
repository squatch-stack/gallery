"""Tests for deterministic inspection contact sheets."""

import argparse
import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

SPEC = importlib.util.spec_from_file_location(
    "inspect_page", Path(__file__).parents[1] / "tools/inspect_page.py"
)
inspect_page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect_page)


class Frames(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.frames = []
        self.rows = 0
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "iframe":
            self.frames.append(attrs)
        if tag == "section" and attrs.get("class") == "scene":
            self.rows += 1


def test_default_catalog_and_angles(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"stem": "one"}, {"stem": "two"}]))
    output = tmp_path / "compare" / "inspect.html"
    assert inspect_page.main(["--out", str(output), "--catalog", str(catalog)]) == 0
    page = output.read_text()
    parsed = Frames(page)
    assert parsed.rows == 2
    assert len(parsed.frames) == 6
    for index, frame in enumerate(parsed.frames):
        stem = ("one", "two")[index // 3]
        az, el, distance = (("30", "15", "1.4"), ("200", "25", "1.4"), ("120", "60", "2.2"))[index % 3]
        url = urlsplit(frame["src"])
        assert parse_qs(url.query) == {"scene": [stem], "az": [az], "el": [el], "d": [distance]}
        assert (output.parent / url.path).resolve() == inspect_page.ROOT / "viewer.html"
        assert frame["loading"] == "lazy"
        assert frame["title"] == f"{stem} · az={az}, el={el}, d={distance}"
        assert frame["title"] in page


def test_explicit_stems_custom_angles_and_escaping(tmp_path):
    output = tmp_path / "inspect.html"
    assert inspect_page.main([
        "--out", str(output), 'a<&"?', "second", "--angles=-30,0,0.5;360,90,2",
    ]) == 0
    page = output.read_text()
    parsed = Frames(page)
    assert parsed.rows == 2
    assert len(parsed.frames) == 4
    assert 'a&lt;&amp;&quot;?' in page
    assert parse_qs(urlsplit(parsed.frames[0]["src"]).query) == {
        "scene": ['a<&"?'], "az": ["-30"], "el": ["0"], "d": ["0.5"],
    }
    assert '../viewer.html?scene=cannon&amp;az=30&amp;el=15&amp;d=1.4' in inspect_page.render(
        ["cannon"], inspect_page.parse_angles("30,15,1.4")
    )


@pytest.mark.parametrize("value", [
    "", " ", "30,15", "30,15,1,2", "30,,1", "x,15,1", "30,15,1;", ";30,15,1",
    "nan,15,1", "30,inf,1", "30,15,-inf", "30,91,1", "30,-91,1", "30,15,0", "30,15,-1",
])
def test_malformed_angles(value):
    with pytest.raises(argparse.ArgumentTypeError):
        inspect_page.parse_angles(value)


def test_whitespace_and_boundaries():
    assert inspect_page.parse_angles(" -360, -90, .5 ; 720, 90, 2 ") == [(-360, -90, .5), (720, 90, 2)]


def test_cli_rejects_angles_without_writing(tmp_path):
    output = tmp_path / "inspect.html"
    with pytest.raises(SystemExit) as error:
        inspect_page.main(["--out", str(output), "cannon", "--angles", "1,2"])
    assert error.value.code == 2
    assert not output.exists()
