#!/usr/bin/env python3
"""Generate a static, synchronized side-by-side gallery comparison page."""

import argparse
import html
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def pane_label(stem, catalog):
    entry = next((item for item in catalog if item.get("stem") == stem), None)
    if not entry:
        return stem
    count = entry.get("splats")
    suffix = f" · {count:,} splats" if isinstance(count, int) else ""
    return f"{entry.get('title', stem)}{suffix}"


def render(title, stems, labels, catalog):
    panes = []
    for index, stem in enumerate(stems):
        label = labels[index] if index < len(labels) else pane_label(stem, catalog)
        panes.append(
            '<section class="pane"><div class="label">'
            + html.escape(label)
            + '</div><iframe title="'
            + html.escape(label, quote=True)
            + '" src="../viewer.html?scene='
            + quote(stem, safe="-_~")
            + '&amp;sync=1"></iframe></section>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--ground:#0d0b09;--panel:#17130e;--line:#3a332a;--ink:#e6ddcd;--dim:#97907f;--ember:#e58a5e}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--ground);color:var(--ink)}}
body{{font:14px/1.5 ui-monospace,Menlo,monospace;padding:1rem;overflow-x:hidden}}
header{{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:0 auto 1rem;max-width:110rem}}
h1{{font:400 clamp(1.25rem,3vw,2rem)/1.2 Georgia,"Times New Roman",serif;margin:0}}
.toggle{{display:flex;align-items:center;gap:.55rem;color:var(--dim);white-space:nowrap}}
.toggle input{{accent-color:var(--ember)}}
.grid{{display:grid;grid-template-columns:repeat({min(len(stems), 3)},minmax(0,1fr));gap:1rem;
  max-width:110rem;margin:auto}}
.pane{{height:calc(100vh - 5.5rem);min-height:28rem;display:grid;grid-template-rows:auto 1fr;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.label{{padding:.55rem .75rem;border-bottom:1px solid var(--line);color:var(--ember);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
iframe{{display:block;width:100%;height:100%;border:0}}
@media(max-width:48rem){{header{{align-items:flex-start;flex-direction:column}}.grid{{grid-template-columns:1fr}}.pane{{height:72vh;min-height:24rem}}}}
</style></head><body><header><h1>{html.escape(title)}</h1>
<label class="toggle"><input id="sync" type="checkbox" checked> sync views</label></header>
<main class="grid">{''.join(panes)}</main>
<script>
const toggle=document.getElementById("sync");
const frames=[...document.querySelectorAll("iframe")];
addEventListener("message",event=>{{
  if(!toggle.checked||event.origin!==location.origin||
     !event.data||event.data.type!=="squatch-gallery-view"||
     !frames.some(frame=>frame.contentWindow===event.source))return;
  for(const frame of frames)if(frame.contentWindow!==event.source)
    frame.contentWindow.postMessage(event.data,location.origin);
}});
</script></body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("stems", nargs="+")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--catalog", type=Path, default=ROOT / "scenes.json", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if len(args.stems) < 2:
        parser.error("at least two scene stems are required")
    if len(args.label) > len(args.stems):
        parser.error("there cannot be more labels than scene stems")
    catalog = json.loads(args.catalog.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.title, args.stems, args.label, catalog))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
