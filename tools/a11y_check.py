#!/usr/bin/env python3
"""Static accessibility checks and a limited self-assessment statement."""

import argparse
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[1]
COLOR = r"(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|var\(--[\w-]+\))"


class PageParser(HTMLParser):
    """Collect only the markup facts needed by the checks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attrs = []
        self.headings = []
        self.title = ""
        self.anchors = []
        self.keyboard = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.attrs.append((tag, values, self.getpos()[0]))
        self._stack.append([tag, values, ""])

    def handle_startendtag(self, tag, attrs):
        self.attrs.append((tag, dict(attrs), self.getpos()[0]))

    def handle_data(self, data):
        for item in self._stack:
            item[2] += data

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            item = self._stack[index]
            if item[0] != tag:
                continue
            del self._stack[index:]
            text = " ".join(item[2].split())
            if tag == "title":
                self.title += text
            if re.fullmatch(r"h[1-6]", tag):
                self.headings.append((tag, text))
            if tag == "a":
                self.anchors.append((item[1], text))
            if item[1].get("id") == "hud":
                self.keyboard.append(text)
            return


def line_number(source, needle):
    """Return a one-based evidence line, or 1 for an absent requirement."""
    match = re.search(needle, source, re.I | re.M)
    return source.count("\n", 0, match.start()) + 1 if match else 1


def result(rule, passed, evidence, line=1):
    return {"rule": rule, "passed": bool(passed), "line": line, "evidence": evidence}


def expand(value, variables):
    match = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
    return variables.get(match.group(1), value) if match else value


def parse_color(value):
    value = value.strip().lower()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits)
        if len(digits) not in (6, 8):
            raise ValueError(value)
        parts = [int(digits[i : i + 2], 16) / 255 for i in range(0, 6, 2)]
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (*parts, alpha)
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if not match:
        raise ValueError(value)
    fields = [field.strip() for field in match.group(1).split(",")]
    if len(fields) not in (3, 4):
        raise ValueError(value)
    rgb = [
        float(field.rstrip("%")) / (100 if "%" in field else 255)
        for field in fields[:3]
    ]
    return (*rgb, float(fields[3]) if len(fields) == 4 else 1.0)


def composite(foreground, background):
    alpha = foreground[3]
    rgb = (foreground[i] * alpha + background[i] * (1 - alpha) for i in range(3))
    return (*rgb, 1.0)


def luminance(color):
    channels = [
        part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4
        for part in color[:3]
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground, background):
    """Return WCAG contrast, compositing translucent foreground first."""
    foreground = composite(foreground, background) if foreground[3] < 1 else foreground
    first, second = luminance(foreground), luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def css_contrast(source):
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, re.I | re.S))
    variables = {
        name: value
        for name, value in re.findall(rf"(--[\w-]+)\s*:\s*({COLOR})", styles)
    }
    body_match = re.search(
        rf"(?:html\s*,\s*)?body\s*{{[^}}]*background(?:-color)?\s*:\s*({COLOR})",
        styles,
        re.I | re.S,
    )
    ground_value = expand(body_match.group(1), variables) if body_match else "#ffffff"
    try:
        ground = parse_color(ground_value)
    except ValueError:
        ground = parse_color("#ffffff")
    pairs = []
    for match in re.finditer(r"([^{}]+)\{([^{}]+)\}", styles):
        selector, declarations = match.group(1).strip(), match.group(2)
        colors = re.findall(rf"(?<![-\w])color\s*:\s*({COLOR})", declarations, re.I)
        if not colors:
            continue
        backgrounds = re.findall(
            rf"background(?:-color)?\s*:\s*({COLOR})", declarations, re.I
        )
        background_value = (
            expand(backgrounds[-1], variables) if backgrounds else ground_value
        )
        try:
            background = parse_color(background_value)
        except ValueError:
            background = ground
        if background[3] < 1:
            background = composite(background, ground)
        large = bool(
            re.search(
                r"font-size\s*:\s*(?:1\.(?:[5-9]|\d{2,})|[2-9]\d*)rem", declarations
            )
        )
        threshold = (
            3.0 if large or re.search(r"(?:border|outline)", declarations) else 4.5
        )
        for value in colors:
            value = expand(value, variables)
            try:
                ratio = contrast_ratio(parse_color(value), background)
            except ValueError:
                continue
            pairs.append(
                (
                    selector,
                    value,
                    background_value,
                    ratio,
                    threshold,
                    source.count("\n", 0, source.find(match.group(0))) + 1,
                )
            )
    return pairs


def check_text(source, name="page.html"):
    parser = PageParser()
    parser.feed(source)
    items = []
    html = next((attrs for tag, attrs, _ in parser.attrs if tag == "html"), {})
    items.append(
        result(
            "document-lang",
            bool(html.get("lang", "").strip()),
            f"lang={html.get('lang')!r}",
            line_number(source, r"<html\b"),
        )
    )
    items.append(
        result(
            "document-title",
            bool(parser.title.strip()),
            f"title={parser.title!r}",
            line_number(source, r"<title\b"),
        )
    )
    h1s = [heading for heading, _ in parser.headings if heading == "h1"]
    heading_ok = not parser.headings or len(h1s) == 1
    items.append(
        result(
            "single-h1",
            heading_ok,
            f"headings={len(parser.headings)}; h1={len(h1s)}",
            line_number(source, r"<h[1-6]\b"),
        )
    )
    images = [(attrs, line) for tag, attrs, line in parser.attrs if tag == "img"]
    missing_alt = [line for attrs, line in images if "alt" not in attrs]
    items.append(
        result(
            "image-alt",
            not missing_alt,
            f"images={len(images)}; missing alt at {missing_alt or 'none'}",
            missing_alt[0] if missing_alt else line_number(source, r"<img\b"),
        )
    )
    empty_links = [
        attrs.get("href", "")
        for attrs, text in parser.anchors
        if not text and not (attrs.get("aria-label") or attrs.get("aria-labelledby"))
    ]
    items.append(
        result(
            "link-name",
            not empty_links,
            f"links={len(parser.anchors)}; unnamed={empty_links or 'none'}",
            line_number(source, r"<a\b"),
        )
    )
    canvases = [(attrs, line) for tag, attrs, line in parser.attrs if tag == "canvas"]
    dynamic_canvas = bool(
        re.search(
            r"(?:domElement|canvas).*setAttribute\(\s*[\"'](?:role|tabindex|aria-label)[\"']",
            source,
        )
    )
    interactive = [item for item in canvases if item[0].get("tabindex") is not None]
    canvas_ok = all(
        attrs.get("role")
        and attrs.get("tabindex") is not None
        and (attrs.get("aria-label") or attrs.get("aria-labelledby"))
        for attrs, _ in interactive
    )
    if dynamic_canvas:
        canvas_ok = all(
            re.search(rf"setAttribute\(\s*[\"']{attr}[\"']", source)
            for attr in ("role", "tabindex", "aria-label")
        )
    items.append(
        result(
            "interactive-canvas",
            canvas_ok,
            f"interactive markup={len(interactive)}; scripted={dynamic_canvas}",
            line_number(source, r"canvas|domElement.*setAttribute"),
        )
    )
    focus_rules = [
        (selector, declarations)
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]+)\}", source)
        if ":focus-visible" in selector
        and re.search(r"(?:outline|border|box-shadow)\s*:", declarations, re.I)
    ]
    link_ids = {attrs.get("id") for attrs, _ in parser.anchors if attrs.get("id")}
    link_focus = any(
        re.search(r"(?:^|[\s,>+~])a(?:[.#:\[]|\b)", selector, re.I)
        or any(f"#{link_id}" in selector for link_id in link_ids)
        for selector, _ in focus_rules
    )
    canvas_focus = any(
        re.search(r"(?:^|[\s,>+~])canvas(?:[.#:\[]|\b)", selector, re.I)
        for selector, _ in focus_rules
    )
    needs_link_focus = bool(parser.anchors)
    needs_canvas_focus = bool(interactive or dynamic_canvas)
    items.append(
        result(
            "focus-visible",
            (not needs_link_focus or link_focus)
            and (not needs_canvas_focus or canvas_focus),
            f"links={link_focus}; canvas={canvas_focus}",
            line_number(source, r":focus-visible"),
        )
    )
    bad_outline = []
    for match in re.finditer(
        r"([^{}]+)\{([^{}]*outline\s*:\s*(?:none|0)[^{}]*)\}", source, re.I
    ):
        declarations = match.group(2)
        if not re.search(
            r"(?:border(?:-color)?|box-shadow|background|transform)\s*:",
            declarations,
            re.I,
        ):
            bad_outline.append(source.count("\n", 0, match.start()) + 1)
    items.append(
        result(
            "outline-replacement",
            not bad_outline,
            f"unreplaced outline removals={bad_outline or 'none'}",
            bad_outline[0] if bad_outline else line_number(source, r"outline\s*:"),
        )
    )
    motion = bool(re.search(r"(?:animation|transition)\s*:", source, re.I))
    reduced = bool(
        re.search(
            r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)", source, re.I
        )
    )
    items.append(
        result(
            "reduced-motion",
            not motion or reduced,
            f"motion={motion}; reduced-motion override={reduced}",
            line_number(source, r"(?:animation|transition|prefers-reduced-motion)"),
        )
    )
    viewport = next(
        (
            attrs.get("content", "")
            for tag, attrs, _ in parser.attrs
            if tag == "meta" and attrs.get("name", "").lower() == "viewport"
        ),
        "",
    )
    blocks_zoom = bool(
        re.search(
            r"user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:\.0*)?(?:\D|$)",
            viewport,
            re.I,
        )
    )
    items.append(
        result(
            "viewport-zoom",
            not blocks_zoom,
            f"viewport={viewport!r}; blocks zoom={blocks_zoom}",
            line_number(source, r"<meta[^>]+viewport"),
        )
    )
    pairs = css_contrast(source)
    contrast_ok = bool(pairs) and all(pair[3] + 1e-9 >= pair[4] for pair in pairs)
    evidence = (
        "; ".join(
            f"{selector}: {fg} on {bg} = {ratio:.2f}:1 (AA {threshold:g}:1)"
            for selector, fg, bg, ratio, threshold, _ in pairs
        )
        or "no measurable text colour pairs"
    )
    items.append(result("contrast", contrast_ok, evidence, pairs[0][5] if pairs else 1))
    keyboard = []
    hud = " ".join(parser.keyboard)
    for label, pattern in (
        ("drag to orbit", r"drag\s+orbit"),
        ("right-drag to pan", r"right-drag\s+pan"),
        ("scroll to zoom", r"scroll\s+zoom"),
        ("arrow keys to pan", r"arrow keys\s+pan"),
    ):
        if re.search(pattern, hud, re.I):
            keyboard.append(label)
    return {
        "file": name,
        "passed": all(item["passed"] for item in items),
        "rules": items,
        "keyboard": keyboard,
    }


def check_file(path):
    try:
        name = str(path.resolve().relative_to(ROOT))
    except ValueError:
        name = path.name
    return check_text(path.read_text(encoding="utf-8"), name)


def statement(results):
    keyboard = next((item["keyboard"] for item in results if item["keyboard"]), [])
    conforming = sorted(
        {rule["rule"] for item in results for rule in item["rules"] if rule["passed"]}
    )
    failing = sorted(
        {
            rule["rule"]
            for item in results
            for rule in item["rules"]
            if not rule["passed"]
        }
    )
    status = (
        "All measured checks pass."
        if not failing
        else "Measured exceptions: " + ", ".join(failing) + "."
    )
    operations = "\n".join(f"- {operation}" for operation in keyboard)
    operations = operations or "- No viewer operations were detected."
    paragraphs = [
        "# Accessibility Conformance Statement (DRAFT)",
        (
            "This is a self-assessment, not an independent accessibility audit. "
            "It covers the static HTML and CSS in `viewer.html` and `index.html` "
            f"and was generated on {date.today().isoformat()} by accessibility "
            f"checker version {VERSION}."
        ),
        "## Measured support",
        (
            f"{status} The checker measured: {', '.join(conforming)}. These results "
            "support only the specific automated checks listed here; they do not "
            "establish complete WCAG 2.1 AA conformance."
        ),
        "## Partially supported content",
        (
            "The interactive 3D canvas does not provide a text alternative for its "
            "full visual content. Its accessible name and screen-reader description "
            "identify the scan and summarize its catalog blurb and controls. The "
            "linked provenance page provides capture, processing, and delivery "
            "context, but neither substitute conveys every spatial or visual detail "
            "in the scan."
        ),
        "## Keyboard operations",
        operations,
        "## Known limitations",
        (
            "- This static checker does not test screen-reader behavior, browser "
            "rendering, focus order during interaction, pointer gestures, JavaScript "
            "failures, captions, cognitive accessibility, or the complete WCAG "
            "success-criterion set.\n- Contrast is calculated only for CSS foreground/"
            "background pairs the checker can resolve from hex or rgba values over "
            "the known body background.\n- The 3D scene itself has no complete "
            "nonvisual equivalent."
        ),
        "Feedback contact: ACCESSIBILITY CONTACT TO BE PROVIDED",
    ]
    return "\n\n".join(paragraphs) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--all", action="store_true", help="check viewer.html and index.html"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--statement", type=Path)
    args = parser.parse_args(argv)
    paths = (
        [ROOT / "viewer.html", ROOT / "index.html"]
        if args.all or not args.paths
        else args.paths
    )
    results = [check_file(path) for path in paths]
    if args.statement:
        args.statement.parent.mkdir(parents=True, exist_ok=True)
        args.statement.write_text(statement(results), encoding="utf-8")
    if args.json:
        print(json.dumps({"tool_version": VERSION, "results": results}, indent=2))
    else:
        for page in results:
            print(f"{page['file']}: {'PASS' if page['passed'] else 'FAIL'}")
            for rule in page["rules"]:
                state = "PASS" if rule["passed"] else "FAIL"
                print(
                    f"  {state} {rule['rule']} (line {rule['line']}): {rule['evidence']}"
                )
            if page["keyboard"]:
                print("  keyboard: " + ", ".join(page["keyboard"]))
    return 0 if all(page["passed"] for page in results) else 1


if __name__ == "__main__":
    sys.exit(main())
