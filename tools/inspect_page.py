#!/usr/bin/env python3
"""Generate a static contact sheet of gallery scenes at fixed camera angles."""

import argparse
import html
import json
import math
import os
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANGLES = "30,15,1.4;200,25,1.4;120,60,2.2"


def parse_angles(value):
    """Parse finite azimuth/elevation/distance triples; distance must be positive."""
    angles = []
    for group in value.split(";"):
        fields = group.split(",")
        try:
            if len(fields) != 3 or any(not field.strip() for field in fields):
                raise ValueError
            az, el, distance = map(float, fields)
            if not all(math.isfinite(number) for number in (az, el, distance)):
                raise ValueError
            if not -90 <= el <= 90 or distance <= 0:
                raise ValueError
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "angles must be az,el,d triples separated by ';': finite numbers, -90 <= el <= 90, d > 0"
            ) from exc
        angles.append((az, el, distance))
    return angles


def render(stems, angles, viewer="../viewer.html"):
    rows = []
    for stem in stems:
        panes = []
        for az, el, distance in angles:
            values = [format(number, ".15g") for number in (az, el, distance)]
            label = f"{stem} · az={values[0]}, el={values[1]}, d={values[2]}"
            query = urlencode(dict(zip(("scene", "az", "el", "d"), [stem, *values], strict=True)))
            panes.append(
                '<section class="pane"><div class="label">'
                + html.escape(label)
                + '</div><iframe loading="lazy" title="'
                + html.escape(label, quote=True)
                + '" src="'
                + html.escape(f"{viewer}?{query}", quote=True)
                + '"></iframe></section>'
            )
        rows.append(f'<section class="scene"><h2>{html.escape(stem)}</h2><div class="grid">'
                    + "".join(panes) + '</div></section>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Squatch Stack — inspection</title>
<style>
:root{{--ground:#0d0b09;--panel:#17130e;--line:#3a332a;--ink:#e6ddcd;--ember:#e58a5e}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--ground);color:var(--ink)}}
body{{font:14px/1.5 ui-monospace,Menlo,monospace;padding:1rem}}
header,main{{max-width:110rem;margin:auto}}header{{margin-bottom:1rem}}
h1{{font:400 clamp(1.25rem,3vw,2rem)/1.2 Georgia,"Times New Roman",serif}}
h2{{font-size:1rem}}.scene{{margin-bottom:2rem}}
.grid{{display:grid;grid-template-columns:repeat({len(angles)},minmax(0,1fr));gap:1rem}}
.pane{{height:65vh;min-height:24rem;display:grid;grid-template-rows:auto 1fr;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.label{{padding:.55rem .75rem;border-bottom:1px solid var(--line);color:var(--ember);overflow-wrap:anywhere}}
iframe{{display:block;width:100%;height:100%;border:0}}
iframe:focus-visible{{outline:2px solid var(--ember);outline-offset:-2px}}
@media(max-width:48rem){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>Squatch Stack — inspection</h1></header>
<main>{''.join(rows)}</main></body></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("stems", nargs="*")
    parser.add_argument("--angles", type=parse_angles, default=DEFAULT_ANGLES)
    parser.add_argument("--catalog", type=Path, default=ROOT / "scenes.json", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    stems = args.stems or [entry["stem"] for entry in json.loads(args.catalog.read_text(encoding="utf-8"))]
    viewer = quote(Path(os.path.relpath(ROOT / "viewer.html", args.out.resolve().parent)).as_posix(), safe="/.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(stems, args.angles, viewer), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
