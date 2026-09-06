#!/usr/bin/env python3
"""Insert or refresh the turntable figure on already-generated provenance pages.

`provenance.py` emits the figure for any sheet it writes from now on, but the
existing sheets cannot simply be regenerated: a solve-mode sidecar records
`<external>/metrics.json` rather than a real path, deliberately, so the argv it
stores is portable evidence and not a replayable command. Rather than
hand-editing thirteen pages, this re-runs the one function that owns the markup
and splices its output in, so there is still a single source of truth for what
the figure looks like.

Idempotent: an existing figure and its script are removed before the current
one is inserted, so running it twice changes nothing the second time.

    python3 tools/refresh_provenance_media.py --check   # report, change nothing
    python3 tools/refresh_provenance_media.py
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

# Deliberately does NOT consume the newline after </script>: that newline
# belongs to the content the figure was spliced in front of, and eating it made
# the refresher rewrite every page on every run.
# Matches both shapes: the current still-only figure, and the video figure
# with its trailing script that this replaced, so old sheets are cleaned up.
FIGURE = re.compile(
    r'<figure class="turntable">.*?</figure>(?:\s*<script>.*?</script>)?',
    re.DOTALL,
)


def load_provenance():
    path = Path(__file__).with_name('provenance.py')
    spec = importlib.util.spec_from_file_location('provenance_module', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refresh(page, figure_for):
    """Return (new_text, action) for one provenance page."""
    text = page.read_text()
    stripped = FIGURE.sub('', text)
    figure = figure_for(page)
    if not figure:
        return stripped, ('removed' if stripped != text else 'none')
    if '</h1>' not in stripped:
        return stripped, 'no-heading'
    head, rest = stripped.split('</h1>', 1)
    updated = head + '</h1>' + figure + rest
    if updated == text:
        return text, 'unchanged'
    return updated, ('added' if FIGURE.search(text) is None else 'updated')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', default=Path(__file__).resolve().parents[1])
    p.add_argument('--check', action='store_true', help='report without writing')
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    provenance = load_provenance()
    root = Path(args.root)
    pages = sorted((root / 'provenance').glob('*.html'))
    if not pages:
        raise SystemExit(f'no provenance pages under {root / "provenance"}')

    changed = []
    for page in pages:
        text, action = refresh(page, provenance.turntable_figure)
        if action in ('added', 'updated', 'removed'):
            changed.append((page.name, action))
            if not args.check:
                page.write_text(text)
        print(f'{page.name:34s} {action}')
    print(f'\n{len(changed)}/{len(pages)} page(s) {"would change" if args.check else "changed"}')
    if args.check and changed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
