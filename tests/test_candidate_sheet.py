"""Isolated candidate staging, camera links, verdicts, and refresh safety."""

import html
import json
import socket

import pytest

from tools import candidate_sheet as sheet
from test_promote_scene import fake_sog
from test_mesh_promotion import textured_quad


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def gallery(tmp_path):
    root = tmp_path / 'gallery'
    (root / 'scenes').mkdir(parents=True)
    (root / 'provenance').mkdir()
    for name in ('index.html', 'viewer.html', 'README.md', 'LICENSE', 'checks.json'):
        (root / name).write_text(name)
    (root / 'provenance/sample.html').write_text('Current provenance')
    (root / 'provenance/sample.json').write_text('{}')
    fake_sog(root / 'scenes/sample.sog', 42)
    (root / 'scenes/sample.spz').write_bytes(b'current alternate')
    fake_sog(root / 'scenes/unrelated.sog')
    entry = dict(stem='sample', title='Sample & <scan>', splats=42, up=[0, 1, 0],
                 provenance='provenance/sample.html', custom='keep', subject='place')
    (root / 'scenes.json').write_text(json.dumps([entry, dict(stem='unrelated')]))
    candidate = tmp_path / 'candidate.sog'
    fake_sog(candidate, 73)
    return root, candidate, tmp_path / 'scratch'


def test_exact_site_urls_labels_checks_and_refresh(gallery, capsys):
    root, candidate, scratch = gallery
    before = snapshot(root)
    assert sheet.main([str(candidate), '--stem', 'sample', '--scratch', str(scratch)], root=root) == 0
    assert str(scratch / 'sheet.html') in capsys.readouterr().out
    assert set(snapshot(scratch)) == {'index.html', 'viewer.html', 'scenes.json', 'sheet.html',
                                      'scenes/sample.sog', 'scenes/sample.spz',
                                      'scenes/sample-candidate.sog', 'provenance/sample.html'}
    current, new = json.loads((scratch / 'scenes.json').read_text())
    assert current == json.loads((root / 'scenes.json').read_text())[0]
    assert new == dict(current, stem='sample-candidate', title='Sample & <scan> (candidate)', splats=73)
    page = (scratch / 'sheet.html').read_text()
    assert page.count('loading="lazy"') == 6
    for stem in ('sample', 'sample-candidate'):
        for angle in ('az=30&el=15&d=1.4', 'az=200&el=25&d=1.4', 'az=120&el=60&d=2.2'):
            assert html.escape(f'viewer.html?scene={stem}&{angle}', quote=True) in page
    assert 'Current: Sample &amp; &lt;scan&gt;' in page
    assert 'Candidate: Sample &amp; &lt;scan&gt; (candidate)' in page
    assert '73 splats' in page and f'{candidate.stat().st_size} bytes' in page
    assert 'check_deliverable (web-mobile): FAIL' in page
    assert 'licence:' in page and 'format:' in page and 'cleanliness:' in page
    assert 'current catalog entry; cloud estimate unavailable' in page
    (scratch / 'stale.txt').write_text('remove on refresh')
    assert sheet.main([str(candidate), '--stem', 'sample', '--scratch', str(scratch),
                       '--up', '1,0,0', '--angles', '0,0,2'], root=root) == 0
    assert not (scratch / 'stale.txt').exists()
    page = (scratch / 'sheet.html').read_text()
    assert page.count('loading="lazy"') == 2
    assert 'explicit --up' in page and 'up: [1.0, 0.0, 0.0]' in page
    assert snapshot(root) == before


def test_cloud_and_fallback(gallery, monkeypatch):
    root, candidate, scratch = gallery
    monkeypatch.setattr(sheet.scene_up, 'cloud_estimate', lambda _: {'up': [1, 0, 0], 'reason': None})
    sheet.build(candidate, 'sample', scratch, root=root)
    assert 'cloud estimate via scene_up' in (scratch / 'sheet.html').read_text()
    assert json.loads((scratch / 'scenes.json').read_text())[1]['up'] == [1, 0, 0]
    monkeypatch.setattr(sheet.scene_up, 'cloud_estimate', lambda _: {'up': None, 'reason': 'no ground'})
    sheet.build(candidate, 'sample', scratch, root=root)
    assert 'current catalog entry; no ground' in (scratch / 'sheet.html').read_text()


def test_mesh_and_switch_back(gallery):
    root, candidate, scratch = gallery
    mesh = candidate.with_suffix('.glb')
    textured_quad(mesh)
    sheet.build(mesh, 'sample', scratch, root=root)
    new = json.loads((scratch / 'scenes.json').read_text())[1]
    assert new['mesh'] == 'scenes/sample-candidate.glb'
    assert new['splats'] == 0 and new['triangles'] == 2
    entries = json.loads((root / 'scenes.json').read_text())
    textured_quad(root / 'scenes/sample.glb')
    entries[0].update(mesh='scenes/sample.glb', triangles=2, splats=0)
    (root / 'scenes.json').write_text(json.dumps(entries))
    before = snapshot(root)
    sheet.build(candidate, 'sample', scratch, root=root)
    current, new = json.loads((scratch / 'scenes.json').read_text())
    assert current['mesh'] == 'scenes/sample.glb'
    assert 'mesh' not in new and 'triangles' not in new
    assert not (scratch / 'scenes/sample-candidate.glb').exists()
    assert snapshot(root) == before


@pytest.mark.parametrize('target', ['.', 'scenes', 'scenes/nested', 'provenance', '.git', 'index.html'])
def test_refuses_gallery_destinations(gallery, target):
    root, candidate, _ = gallery
    before = snapshot(root)
    with pytest.raises(ValueError):
        sheet.build(candidate, 'sample', root / target, root=root)
    assert snapshot(root) == before


def test_refuses_unowned_directory_and_symlink(gallery):
    root, candidate, scratch = gallery
    scratch.mkdir()
    (scratch / 'keep').write_text('keep')
    with pytest.raises(ValueError, match='nonempty'):
        sheet.build(candidate, 'sample', scratch, root=root)
    link = scratch.parent / 'link'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        sheet.build(candidate, 'sample', link, root=root)
    assert (scratch / 'keep').read_text() == 'keep'


@pytest.mark.parametrize('options', [['--stem', '../bad'], ['--stem', 'unknown'],
                                   ['--stem', 'sample', '--up', '0,0,0'],
                                   ['--stem', 'sample', '--angles', '0,91,1']])
def test_bad_inputs_do_not_write(gallery, options):
    root, candidate, scratch = gallery
    before = snapshot(root)
    with pytest.raises(SystemExit, match='2'):
        sheet.main([str(candidate), '--scratch', str(scratch), *options], root=root)
    assert not scratch.exists() and snapshot(root) == before


def test_free_port_selection():
    port = sheet.free_port()
    assert 0 < port < 65536
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
