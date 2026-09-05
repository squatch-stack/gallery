"""Independent analytic geometry and CPU-only pruning regressions."""

from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import prune_by_views as tool


def camera(model='PINHOLE', params=(240, 240, 128, 128)):
    return NS(model=model, params=np.array(params), width=256, height=256)


@pytest.fixture
def sphere():
    # Fibonacci shell; masks use only the analytic tangent-cone radius,
    # never project(), voting, or a rendered point cloud.
    n = 60000
    y = 1 - 2 * (np.arange(n) + .5) / n
    phi = np.arange(n) * np.pi * (3 - np.sqrt(5))
    radial = np.sqrt(1 - y * y)
    subject = .5 * np.column_stack((radial * np.cos(phi), y, radial * np.sin(phi)))
    views = []
    yy, xx = np.mgrid[:256, :256]
    mask = (xx - 128) ** 2 + (yy - 128) ** 2 <= (120 * .5 / np.sqrt(16 - .25)) ** 2
    for i in range(8):
        angle = i * np.pi / 4
        c = 4 * np.array([np.sin(angle), 0, np.cos(angle)])
        z = -c / 4
        x = np.cross([0, 1, 0], z)
        R = np.array([x, np.cross(z, x), z])
        views.append(NS(name=f'view-{i}.jpg', R=R, t=-R @ c, camera=camera(params=(120, 120, 128, 128)),
                        mask=mask.copy()))
    a = (np.arange(8) + .5) * 2 * np.pi / 8
    grass = 3 * np.column_stack((np.sin(a), np.zeros(len(a)), np.cos(a)))
    spokes = np.tile([0, 0, -2.5], (20, 1)) + np.column_stack((np.linspace(-.01, .01, 20),
                                                                           np.zeros((20, 2))))
    pos = np.vstack((subject, grass, spokes))
    return pos, np.full_like(pos, .004), np.full(len(pos), .95), views, n


def test_hand_projection():
    p = np.array([[0, 0, 2], [1, 2, 4], [-2, 1, 4], [1, -1, 2]])
    xy, _, _ = tool.project(np.eye(3), np.zeros(3), camera(), p)
    np.testing.assert_allclose(xy, [[128, 128], [188, 248], [8, 188], [248, 8]])
    cam = camera('SIMPLE_RADIAL', [240, 128, 128, .1])
    xy, _, _ = tool.project(np.eye(3), np.zeros(3), cam, p)
    uv = p[:, :2] / p[:, 2, None]
    np.testing.assert_allclose(xy, 128 + 240 * uv * (1 + .1 * (uv ** 2).sum(1))[:, None])


@pytest.mark.parametrize('model,params', [('SIMPLE_PINHOLE', [240, 128, 128]),
    ('PINHOLE', [240, 250, 128, 128]), ('SIMPLE_RADIAL', [240, 128, 128, .1]),
    ('RADIAL', [240, 128, 128, .1, -.02]), ('OPENCV', [240, 250, 128, 128, .1, -.02, .003, -.004])])
def test_pycolmap_agreement(model, params):
    pycolmap = pytest.importorskip('pycolmap')
    cam = pycolmap.Camera(model=model, width=256, height=256, params=params)
    p = np.random.default_rng(7).normal(size=(500, 3))
    p[:, 2] = np.abs(p[:, 2]) + 1
    xy, _, _ = tool.project(np.eye(3), np.zeros(3), cam, p)
    np.testing.assert_allclose(xy, cam.img_from_cam(p), atol=1e-6, rtol=0)


def test_shell_and_paired_spokes(sphere):
    pos, scale, alpha, views, n = sphere
    cfg = tool.settings()
    keep, _, _, _ = tool.vote(pos, scale, alpha, views, 1, cfg)
    assert keep[:n].all()
    assert not keep[n:n + 8].any()
    assert not keep[n + 8:].any()
    relaxed, _, _, _ = tool.vote(pos, scale, alpha, views, 1, tool.settings(min_views=1, inside_fraction=0))
    assert relaxed[n + 8:].all()


def test_farside_paired(sphere):
    pos, scale, alpha, views, n = sphere
    # A far-side appendage hidden by the shell in view 0. A deliberately
    # missed silhouette in two adjacent masks exercises occlusion abstention.
    far = np.array([[0, 0, -.5]])
    pos = np.vstack((pos[:n], far))
    scale, alpha = np.full_like(pos, .004), np.full(len(pos), .95)
    selected = views[:2]
    for view in selected:
        view.mask[120:136, 112:144] = False
    keep, _, _, _ = tool.vote(pos, scale, alpha, selected, 1, tool.settings())
    bad, _, _, _ = tool.vote(pos, scale, alpha, selected, 1, tool.settings(no_depth_test=True))
    assert keep[-1]
    assert not bad[-1]


def test_depth_order_and_alpha_gate():
    xy = np.array([[8, 8], [8, 8], [9, 8], [16, 16]])
    z, a = np.array([.1, 3, 2, 4]), np.array([.01, .9, .9, .9])
    order = [3, 1, 0, 2]
    def run(order):
        return tool.depth_buffer(xy[order], z[order], np.ones(4, bool), a[order], 32, 32, 4, .5, 1)
    np.testing.assert_array_equal(run(np.arange(4)), run(order))
    assert run(order)[2, 2] == 2


def test_three_states_and_grid():
    mask = np.zeros((12, 12), bool)
    mask[4:8, 4:8] = True
    mask[0, 0] = True
    states = tool.mask_states(mask, 4, 0)
    assert states[0, 0] == 2 and states[1, 1] == 1 and states[2, 2] == 0
    assert tool.mask_grid(np.zeros((128, 128)), camera(), .001) == (.5, .5)
    with pytest.raises(ValueError, match=r'128x64.*256x256'):
        tool.mask_grid(np.zeros((64, 128)), camera(), .001)


def test_outliers_and_padding():
    cluster = np.zeros((40, 3))
    isolated = np.column_stack((np.arange(1, 11) * 5, np.zeros((10, 2))))
    pos = np.vstack((cluster, isolated))
    a = np.ones(len(pos))
    cfg = tool.settings(outlier_cell_fraction=1, outlier_min_neighbours=3)
    keep = tool.outlier_keep(pos, a, 1, cfg)
    assert keep[:40].all() and (~keep).sum() == 10
    assert tool.outlier_keep(pos, a, 1, tool.settings(outlier_min_neighbours=0)).all()
    # Adjacent flattened rows, distant spatially; must not vouch for each other.
    edge = np.array([[0, 0, 8], [0, 1, 0]])
    assert not tool.outlier_keep(edge, np.ones(2), 1,
                                 tool.settings(outlier_cell_fraction=1, outlier_min_neighbours=2)).any()
    assert not tool.outlier_keep(np.zeros((20, 3)), np.full(20, .01), 1,
                                 tool.settings(outlier_weight='alpha')).any()


def test_permutations_and_frame(sphere):
    p, s, a, views, _ = sphere
    first = tool.vote(p, s, a, views, 1, tool.settings())[0].tobytes()
    assert first == tool.vote(p, s, a, views[::-1], 1, tool.settings())[0].tobytes()
    assert first == tool.vote(p, s, a, views, 1, tool.settings())[0].tobytes()
    with pytest.raises(ValueError, match="solve's frame"):
        tool.vote(p + np.array([0, 100, 0]), s, a, views, 1, tool.settings())


def write_ply(path, pos, scale=None, alpha=None, endian='<'):
    from tools.check_deliverable import REQUIRED

    props = sorted(REQUIRED | {f'f_rest_{i}' for i in range(45)})
    rec = np.zeros(len(pos), dtype=[(name, endian + 'f4') for name in props])
    for i, name in enumerate(('x', 'y', 'z')):
        rec[name] = pos[:, i]
        rec[f'scale_{i}'] = np.log(.004 if scale is None else scale[:, i])
    a = np.full(len(pos), .95) if alpha is None else alpha
    rec['opacity'] = np.log(a / (1 - a))
    for i in range(45):
        rec[f'f_rest_{i}'] = np.arange(len(pos)) * .001 + i
    fmt = 'binary_little_endian' if endian == '<' else 'binary_big_endian'
    header = (f'ply\nformat {fmt} 1.0\ncomment preserve SH and header\nelement vertex {len(pos)}\n'
              + ''.join(f'property float {p}\n' for p in props) + 'end_header\n').encode()
    path.write_bytes(header + rec.tobytes())
    return header, rec


@pytest.mark.parametrize('endian', ['<', '>'])
def test_ply_byte_exact_with_sh(tmp_path, endian):
    path, out = tmp_path / 'input.ply', tmp_path / 'output.ply'
    header, rec = write_ply(path, np.arange(30).reshape(10, 3), endian=endian)
    p, _, _, records = tool.read_cloud(path)
    np.testing.assert_array_equal(p, np.arange(30).reshape(10, 3))
    keep = np.array([True, False] * 5)
    tool.write_subset(path, out, records, keep)
    expected_header = header.replace(b'element vertex 10', b'element vertex 5')
    assert out.read_bytes() == expected_header + rec[keep].tobytes()
    _, _, _, written = tool.read_cloud(out)
    assert written[1].dtype.names == rec.dtype.names


def test_mass_fraction():
    from tools.clean_export import footprint_score

    mass = footprint_score(np.array([[1, 2, 3], [2, 3, 4]], float), np.array([.5, .25]))
    np.testing.assert_array_equal(mass, [3, 3])
    assert tool.stage('vote', np.array([True, True]), np.array([True, False]), mass) == {
        'name': 'vote', 'removed': 1, 'removed_mass_fraction': .5}
    assert tool.stage('outlier', np.array([True, False]), np.array([False, False]), mass)[
        'removed_mass_fraction'] == 1


def test_holdout_and_full_prune(sphere):
    p, s, a, views, n = sphere
    keep, result = tool.prune(p, s, a, views[:6], views[6:], tool.settings())
    assert keep[:n].all()
    assert not keep[n:].any()
    before, after = result['holdout']['before'], result['holdout']['after']
    assert after['precision'] > before['precision']
    assert after['recall'] == before['recall']
    assert after['recall'] > .9


@pytest.fixture
def cli(tmp_path, monkeypatch, sphere):
    p, s, a, views, _ = sphere
    cloud = tmp_path / 'input.ply'
    write_ply(cloud, p, s, a)
    subject = tmp_path / 'subject'
    sparse, masks = subject / 'sparse/0', subject / 'masks'
    sparse.mkdir(parents=True)
    masks.mkdir()
    (sparse / 'cameras.bin').write_bytes(b'fixture solve')
    images = {}
    for i, view in enumerate(views):
        pose = NS(rotation=NS(matrix=lambda R=view.R: R), translation=view.t)
        images[i] = NS(name=view.name, camera_id=i, cam_from_world=lambda pose=pose: pose)
        Image.fromarray(view.mask.astype('uint8') * 255).save(masks / f'view-{i}.png')
    rec = NS(images=images, cameras={i: v.camera for i, v in enumerate(views)})
    rec.num_images = lambda: len(images)
    monkeypatch.setitem(sys.modules, 'pycolmap', NS(Reconstruction=lambda path: rec))
    base = [str(cloud), '--solve', str(subject), '--masks', str(masks), '--holdout', '2']
    return base, tmp_path, masks, rec


def test_cli_reports_and_determinism(cli, capsys):
    import json
    from tools.inspect_page import parse_angles

    base, tmp, _, _ = cli
    outputs = ['--out-ply', str(tmp / 'pruned.ply'), '--keep-out', str(tmp / 'keep.npz'),
               '--report', str(tmp / 'report.json'), '--angles-out', str(tmp / 'angles.txt'), '--json']
    # Explicit order cannot alter the jury, holdout, or output records.
    tool.main(base + outputs + ['--view-list', 'view-5.jpg,view-2.jpg,view-0.jpg,view-4.jpg'])
    output = json.loads(capsys.readouterr().out)
    report = json.loads((tmp / 'report.json').read_text())
    assert report == output
    assert report['tool'] == 'prune_by_views'
    assert not set(report['jury']) & set(report['holdout_views'])
    assert all(len(item['sha256']) == 64 for item in report['inputs'])
    assert report['holdout']['after']['recall'] is not None
    first_ply = (tmp / 'pruned.ply').read_bytes()
    with np.load(tmp / 'keep.npz') as saved:
        first_keep = saved['keep'].tobytes()
        assert saved['n'] == report['n']
        assert saved['source_sha256'] == report['source_sha256']
    assert parse_angles((tmp / 'angles.txt').read_text().strip())
    for _ in range(2):
        tool.main(base + outputs + ['--view-list', 'view-4.jpg,view-0.jpg,view-2.jpg,view-5.jpg'])
        capsys.readouterr()
        assert (tmp / 'pruned.ply').read_bytes() == first_ply
        with np.load(tmp / 'keep.npz') as saved:
            assert saved['keep'].tobytes() == first_keep


@pytest.mark.parametrize('flag,value', [('--inside-fraction', 'nan'), ('--inside-fraction', '1.1'),
    ('--inside-fraction', '-.1'), ('--views', '0'), ('--depth-rel', 'inf'), ('--mask-scale', '0'),
    ('--depth-fill', '-1'), ('--min-views', '1.5'), ('--sheet-distance', '0')])
def test_numeric_refusals(flag, value, tmp_path):
    with pytest.raises(SystemExit, match='2'):
        tool.main(['none.ply', '--solve', str(tmp_path), '--masks', str(tmp_path), flag, value])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('problem,match', [('missing', 'missing mask'), ('grid', 'mask grid'),
    ('frame', "solve's frame"), ('empty', 'every splat removed'), ('model', 'unsupported camera model FOV')])
def test_cli_refusals_write_nothing(cli, problem, match, capsys):
    base, tmp, masks, rec = cli
    if problem == 'missing':
        (masks / 'view-0.png').unlink()
    if problem == 'grid':
        Image.new('L', (128, 64)).save(masks / 'view-0.png')
    if problem == 'frame':
        p, s, a, _ = tool.read_cloud(Path(base[0]))
        write_ply(Path(base[0]), p + np.array([0, 100, 0]), s, a)
    if problem == 'empty':
        base += ['--alpha-min', '1']
    if problem == 'model':
        rec.cameras[0].model = 'FOV'
    dest = tmp / 'outputs'
    with pytest.raises(SystemExit, match='2'):
        tool.main([*base, '--report', str(dest / 'report.json'), '--out-ply', str(dest / 'out.ply'),
                          '--keep-out', str(dest / 'keep.npz'), '--angles-out', str(dest / 'angles.txt')])
    assert match in capsys.readouterr().err
    assert not dest.exists()


def test_degenerate_masks_and_grid_scaling(cli, capsys):
    import json

    base, _, masks, _ = cli
    Image.new('L', (256, 256), 255).save(masks / 'view-0.png')
    Image.new('L', (256, 256), 0).save(masks / 'view-1.png')
    with Image.open(masks / 'view-2.png') as image:
        image.resize((512, 512), Image.Resampling.NEAREST).save(masks / 'view-2.png')
    tool.main([*base, '--json'])
    output = capsys.readouterr()
    assert 'scale pixel coordinates by 2,2' in output.err
    assert 'warning: excluded view-0.jpg' in output.err
    report = json.loads(output.out)
    assert len(report['excluded_views']) == 2
    assert not {'view-0.jpg', 'view-1.jpg'} & set(report['jury'] + report['holdout_views'])


def test_sog_refusal_and_report_only(cli, monkeypatch, capsys):
    import json
    from tools import check_deliverable

    base, tmp, _, _ = cli
    p, s, a, _ = tool.read_cloud(Path(base[0]))
    sog = tmp / 'input.sog'
    sog.write_bytes(b'fixture')
    base[0] = str(sog)
    monkeypatch.setattr(check_deliverable, 'read_sog', lambda path: (len(p), (p, s, a, None)))
    tool.main([*base, '--json', '--keep-out', str(tmp / 'keep.npz')])
    assert json.loads(capsys.readouterr().out)['kept'] > 0
    with pytest.raises(SystemExit, match='2'):
        tool.main([*base, '--out-ply', str(tmp / 'bad.ply')])
    assert 'SOG is a delivery format; re-export from the archived PLY' in capsys.readouterr().err
    assert not (tmp / 'bad.ply').exists()


def test_angle_conversion():
    from tools.inspect_page import parse_angles

    views = [NS(R=np.eye(3), t=-np.array([0., 0., 4.])),
             NS(R=np.eye(3), t=-np.array([4., 0., 0.]))]
    angles = parse_angles(tool.review_angles(views, np.zeros(3), np.array([0, 1, 0]), 2.5))
    np.testing.assert_allclose(angles, [[180, 0, 2.5], [90, 0, 2.5]])
    angles = parse_angles(tool.review_angles(views[:1], np.zeros(3), np.array([0, 0, 1]), 3))
    np.testing.assert_allclose(angles[0][1:], [-90, 3])


def test_defaults_and_selection(sphere):
    _, _, _, views, _ = sphere
    for entry in tool.load_defaults().values():
        assert {'value', 'source', 'rationale'} == entry.keys()
        assert entry['source'] and entry['rationale']
    for key in ('inside_fraction', 'min_views', 'outlier_min_neighbours'):
        assert 'provisional' in tool.load_defaults()[key]['rationale'].lower()
    spread = tool.select_views(views, 2, 'spread', np.zeros(3))
    assert [v.name for v in spread] == ['view-0.jpg', 'view-4.jpg']
    assert tool.select_views(views[::-1], 2, 'spread', np.zeros(3)) == spread
    assert len(tool.select_views(views, 1, 'even', np.zeros(3))) == 1
    assert len(tool.select_views(views, 1, 'all', np.zeros(3))) == 8


def test_edge_ablation_and_unjudged_policy():
    cam = camera()
    mask = np.zeros((256, 256), bool)
    mask[128, 128] = True
    view = NS(name='edge', camera=cam, R=np.eye(3), t=np.zeros(3), mask=mask)
    p, s, a = np.array([[0., 0., 4.]]), np.ones((1, 3)) * .01, np.ones(1)
    for edge, judged, inside in [('abstain', 0, 0), ('inside', 1, 1), ('outside', 1, 0)]:
        V, count, _ = tool.view_votes(p, s, a, view, 1, tool.settings(edge=edge))
        assert V[0] == judged and count[0] == inside
    assert tool.vote(p, s, a, [view], 1, tool.settings())[0][0]
    assert not tool.vote(p, s, a, [view], 1, tool.settings(unjudged='drop'))[0][0]


def test_heldout_masks_do_not_influence_pruning(sphere):
    p, s, a, views, _ = sphere
    before, _ = tool.prune(p, s, a, views[:6], views[6:], tool.settings())
    for view in views[6:]:
        view.mask[:] = ~view.mask
    after, _ = tool.prune(p, s, a, views[:6], views[6:], tool.settings())
    assert before.tobytes() == after.tobytes()


def test_vote_empty_and_output_collision(cli, capsys):
    base, tmp, _, _ = cli
    with pytest.raises(SystemExit, match='2'):
        tool.main([*base, '--min-inside', '100', '--unjudged', 'drop', '--report', str(tmp / 'empty.json')])
    assert 'every splat removed' in capsys.readouterr().err
    assert not (tmp / 'empty.json').exists()
    source = Path(base[0]).read_bytes()
    with pytest.raises(SystemExit, match='2'):
        tool.main([*base, '--out-ply', base[0]])
    assert 'overlap inputs' in capsys.readouterr().err
    assert Path(base[0]).read_bytes() == source


@pytest.mark.parametrize('content', ['[]', '{"views": 2}',
    '{"views":{"value":0,"source":"test","rationale":"test"}}',
    '{"depth_abs":{"value":NaN,"source":"test","rationale":"test"}}'])
def test_bad_defaults(tmp_path, content):
    path = tmp_path / 'defaults.json'
    path.write_text(content)
    with pytest.raises(SystemExit, match='2'):
        tool.parse_args(['none.ply', '--solve', '.', '--masks', '.', '--defaults', str(path)])


def test_defaults_override_and_names(cli, capsys):
    import json

    base, tmp, _, _ = cli
    defaults = tmp / 'defaults.json'
    defaults.write_text(json.dumps({'views': {'value': 3, 'source': 'test', 'rationale': 'test override'}}))
    names = tmp / 'names.txt'
    names.write_text('\n'.join(f'view-{i}.jpg' for i in range(7)))
    tool.main([*base, '--defaults', str(defaults), '--names', str(names), '--json'])
    report = json.loads(capsys.readouterr().out)
    assert len(report['jury']) == 3
    assert report['thresholds']['views'] == 3
    assert 'view-7.jpg' not in report['jury'] + report['holdout_views']
