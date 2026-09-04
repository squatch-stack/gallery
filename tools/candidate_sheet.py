#!/usr/bin/env python3
"""Stage a candidate beside its current scene in an isolated inspection site."""

import argparse
import html
import json
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
import zipfile

try:
    from tools import check_deliverable, scene_up
    from tools.inspect_page import DEFAULT_ANGLES, parse_angles
    from tools.promote_scene import glb_summary, parse_up, provenance_path, scene_files, sog_count
except ModuleNotFoundError:
    import check_deliverable
    import scene_up
    from inspect_page import DEFAULT_ANGLES, parse_angles
    from promote_scene import glb_summary, parse_up, provenance_path, scene_files, sog_count

ROOT = Path(__file__).resolve().parents[1]
MARKER = '<!-- candidate_sheet generated site -->'


def choose_up(candidate, explicit, current):
    if explicit is not None:
        return explicit, 'explicit --up'
    reason = 'mesh has no cloud estimate'
    if candidate.suffix.lower() == '.sog':
        try:
            estimate = scene_up.cloud_estimate(candidate)
            if estimate['up'] is not None and not estimate.get('reason'):
                return parse_up(','.join(map(str, estimate['up']))), 'cloud estimate via scene_up'
            reason = estimate.get('reason') or 'cloud estimate returned no up vector'
        except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError, ImportError,
                zipfile.BadZipFile, argparse.ArgumentTypeError) as exc:
            reason = f'cloud estimate unavailable ({type(exc).__name__})'
    return current.get('up'), f'current catalog entry; {reason}'


def free_port():
    """Ask the OS for an available loopback port without starting a server."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def serve(scratch):
    port = free_port()
    process = subprocess.Popen([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1',
                                '--directory', str(scratch)])
    try:
        print(f'http://127.0.0.1:{port}/sheet.html', flush=True)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()


def render(entries, results, angles, source):
    summaries = []
    rows = []
    for label, entry, result in zip(('Current', 'Candidate'), entries, results, strict=True):
        # The header-only count is still useful when geometry validation fails.
        count = result['count'] if result['count'] is not None else entry.get('splats', 'unknown')
        summary = (f"{label}: {entry['title']} · {count} splats · {result['size_bytes']} bytes · "
                   f"check_deliverable (web-mobile): {'PASS' if result['passed'] else 'FAIL'} · "
                   f"up: {json.dumps(entry.get('up'))} "
                   f"({source if label == 'Candidate' else 'current catalog entry'})")
        failures = '; '.join(f"{c['name']}: {c['detail']}" for c in result['checks'] if c['status'] == 'fail')
        summaries.append(f'<p>{html.escape(summary)}</p><p>{html.escape(failures)}</p>')
    for angle in angles:
        values = [format(number, '.15g') for number in angle]
        label = f'az={values[0]}, el={values[1]}, d={values[2]}'
        panes = []
        for version, entry in zip(('Current', 'Candidate'), entries, strict=True):
            query = urlencode(dict(zip(('scene', 'az', 'el', 'd'), [entry['stem'], *values], strict=True)))
            title = html.escape(f"{version}: {entry['title']} · {label}", quote=True)
            panes.append(f'<section><h3>{title}</h3><iframe loading="lazy" title="{title}" '
                         f'src="{html.escape("viewer.html?" + query, quote=True)}"></iframe></section>')
        rows.append(f'<section class="angle"><h2>{label}</h2><div class="pair">{"".join(panes)}</div></section>')
    return f'''<!doctype html>
{MARKER}
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Candidate sheet</title><style>
body{{margin:1rem;background:#0d0b09;color:#e6ddcd;font:14px/1.5 system-ui}}
header,main{{max-width:110rem;margin:auto}}.pair{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}}
h3,p{{overflow-wrap:anywhere}}iframe{{width:100%;height:65vh;min-height:24rem;border:1px solid #655746}}
@media(max-width:48rem){{.pair{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>Current / Candidate</h1>{''.join(summaries)}
<p>Scratch verdicts omit gallery licence evidence: README.md and LICENSE are outside this minimal site.
Candidate provenance is inherited from the current entry, not new candidate evidence.
A null up vector means the viewer uses its default alignment.</p></header><main>{''.join(rows)}</main></body></html>
'''


def build(candidate, stem, scratch, up=None, angles=None, root=ROOT):
    root, candidate = root.resolve(), candidate.resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', stem):
        raise ValueError('stem may contain only letters, numbers, hyphens, and underscores')
    if candidate.suffix.lower() not in {'.sog', '.glb'} or not candidate.is_file():
        raise ValueError('candidate must be an existing .sog or .glb file')
    if scratch.is_symlink():
        raise ValueError('scratch must not be a symlink')
    scratch = scratch.resolve()
    protected = [root / name for name in ('scenes', 'provenance', '.git', '.agents', '.codex')]
    if root.is_relative_to(scratch) or any(scratch.is_relative_to(p.resolve()) for p in protected):
        raise ValueError('scratch overlaps the real gallery')
    entries = json.loads((root / 'scenes.json').read_text())
    current = next((e for e in entries if e['stem'] == stem), None)
    if current is None:
        raise ValueError('stem must exist in the current catalog')
    files = [root / 'index.html', root / 'viewer.html', *scene_files(root, stem)]
    page = provenance_path(root, current)
    if page:
        files.append(page)
    if current.get('mesh'):
        mesh = root / current['mesh']
        if not mesh.resolve().is_relative_to((root / 'scenes').resolve()):
            raise ValueError('current mesh must be inside scenes/')
        if mesh not in files:
            files.append(mesh)
    if any(p.resolve().is_relative_to(scratch) for p in [*files, candidate]):
        raise ValueError('scratch must not contain input files')
    if scratch.exists() and (not scratch.is_dir() or any(scratch.iterdir())):
        sheet = scratch / 'sheet.html'
        if not sheet.is_file() or MARKER not in sheet.read_text():
            raise ValueError('refusing to refresh a nonempty directory not generated by candidate_sheet')
    new = dict(current, stem=stem + '-candidate', title=current.get('title', stem) + ' (candidate)')
    new.pop('mesh', None)
    new.pop('triangles', None)
    if candidate.suffix.lower() == '.glb':
        new.update(splats=0, triangles=glb_summary(candidate)[0], mesh=f"scenes/{new['stem']}.glb")
    else:
        new['splats'] = sog_count(candidate)
    vector, source = choose_up(candidate, up, current)
    if vector is not None:
        new['up'] = vector
    scratch.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.candidate-sheet-', dir=scratch.parent) as temporary:
        stage = Path(temporary) / 'site'
        stage.mkdir()
        for path in files:
            destination = stage / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        destination = stage / 'scenes' / (new['stem'] + candidate.suffix.lower())
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)
        (stage / 'scenes.json').write_text(json.dumps([current, new], indent=2) + '\n')
        results = [check_deliverable.check_scene(e['stem'], root=stage) for e in (current, new)]
        (stage / 'sheet.html').write_text(render([current, new], results,
                                               angles or parse_angles(DEFAULT_ANGLES), source), encoding='utf-8')
        if scratch.exists():
            shutil.rmtree(scratch)
        stage.rename(scratch)
    return scratch / 'sheet.html'


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--stem', required=True)
    parser.add_argument('--up', type=parse_up)
    parser.add_argument('--angles', type=parse_angles, default=DEFAULT_ANGLES)
    parser.add_argument('--scratch', type=Path, default=root / 'out/sheet-site')
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args(argv)
    try:
        sheet = build(args.candidate, args.stem, args.scratch, args.up, args.angles, root)
    except (OSError, ValueError, KeyError, TypeError, struct.error, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    if args.serve:
        return serve(sheet.parent)
    print(sheet)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
