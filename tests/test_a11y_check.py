import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "a11y_check", Path(__file__).parents[1] / "tools/a11y_check.py"
)
a11y_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a11y_check)


BASE = """<!doctype html>
<html lang="en"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fixture</title>
<style>
body { background: #fff; color: #000; }
a:focus-visible { outline: 2px solid #000; }
canvas:focus-visible { outline: 2px solid #000; }
</style></head><body>
<h1>Fixture</h1><img src="x" alt="example"><a href="/">Home</a>
</body></html>"""


def failed_rules(source):
    return {
        item["rule"]
        for item in a11y_check.check_text(source)["rules"]
        if not item["passed"]
    }


def test_passing_fixture():
    assert a11y_check.check_text(BASE)["passed"]


@pytest.mark.parametrize(
    ("rule", "source"),
    [
        ("document-lang", BASE.replace(' lang="en"', "")),
        ("document-title", BASE.replace("<title>Fixture</title>", "<title></title>")),
        ("single-h1", BASE.replace("</h1>", "</h1><h1>Again</h1>")),
        ("image-alt", BASE.replace(' alt="example"', "")),
        ("link-name", BASE.replace(">Home</a>", "></a>")),
        (
            "interactive-canvas",
            BASE.replace("</body>", '<canvas tabindex="0" role="img"></canvas></body>'),
        ),
        ("focus-visible", BASE.replace("a:focus-visible", "a:hover")),
        (
            "outline-replacement",
            BASE.replace("outline: 2px solid #000", "outline: none", 1),
        ),
        (
            "reduced-motion",
            BASE.replace("</style>", "a { transition: color .2s; }</style>"),
        ),
        (
            "viewport-zoom",
            BASE.replace("initial-scale=1", "initial-scale=1, user-scalable=no"),
        ),
        ("contrast", BASE.replace("color: #000", "color: #777")),
    ],
)
def test_failing_fixture_for_each_rule(rule, source):
    assert rule in failed_rules(source)


def test_page_without_headings_does_not_require_h1():
    assert "single-h1" not in failed_rules(BASE.replace("<h1>Fixture</h1>", ""))


def test_interactive_canvas_can_be_accessible():
    canvas = '<canvas role="img" tabindex="0" aria-label="Scene"></canvas>'
    assert "interactive-canvas" not in failed_rules(
        BASE.replace("</body>", canvas + "</body>")
    )


def test_contrast_known_pairs():
    white = a11y_check.parse_color("#fff")
    assert a11y_check.contrast_ratio(a11y_check.parse_color("#000"), white) == 21
    assert a11y_check.contrast_ratio(
        a11y_check.parse_color("#777"), white
    ) == pytest.approx(4.48, abs=0.01)


def test_rgba_is_composited_over_background():
    foreground = a11y_check.parse_color("rgba(0, 0, 0, 0.5)")
    white = a11y_check.parse_color("#fff")
    assert a11y_check.contrast_ratio(foreground, white) == pytest.approx(3.98, abs=0.01)


def test_json_cli_and_failure_exit(tmp_path):
    page = tmp_path / "bad.html"
    page.write_text(BASE.replace(' lang="en"', ""), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "tools/a11y_check.py", "--json", str(page)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["results"][0]["passed"] is False


def test_statement_is_generated(tmp_path):
    output = tmp_path / "accessibility.md"
    assert a11y_check.main(["--statement", str(output), "--all"]) == 0
    text = output.read_text(encoding="utf-8")
    assert "self-assessment, not an independent accessibility audit" in text
    assert "does not provide a text alternative for its full visual content" in text
    assert "ACCESSIBILITY CONTACT TO BE PROVIDED" in text


def test_real_pages_pass():
    root = Path(__file__).resolve().parents[1]
    assert a11y_check.main([str(root / "viewer.html"), str(root / "index.html")]) == 0
