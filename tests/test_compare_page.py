"""Tests for static gallery comparison page generation."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "compare_page", Path(__file__).parents[1] / "tools/compare_page.py"
)
compare_page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare_page)


def test_page_uses_catalog_labels_and_sync(tmp_path):
    catalog = tmp_path / "scenes.json"
    catalog.write_text(json.dumps([
        {"stem": "one", "title": "First", "splats": 1234},
        {"stem": "two", "title": "Second", "splats": 5678},
    ]))
    output = tmp_path / "compare" / "pair.html"
    assert compare_page.main([
        "--title", "A & B", "--out", str(output), "one", "two",
        "--label", "Custom <one>", "--catalog", str(catalog),
    ]) == 0
    page = output.read_text()
    assert "A &amp; B" in page
    assert "Custom &lt;one&gt;" in page
    assert "Second · 5,678 splats" in page
    assert "../viewer.html?scene=one&amp;sync=1" in page
    assert 'id="sync" type="checkbox" checked' in page
    assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in page
    assert "event.source" in page and "postMessage" in page


def test_page_requires_two_stems(tmp_path):
    catalog = tmp_path / "scenes.json"
    catalog.write_text("[]")
    try:
        compare_page.main([
            "--title", "Solo", "--out", str(tmp_path / "solo.html"),
            "one", "--catalog", str(catalog),
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("one stem should be rejected")
