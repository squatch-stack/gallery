"""Offline mission arithmetic, refusals, export structure and deterministic fixtures."""
import importlib.util
from itertools import pairwise
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('drone_plan', ROOT / 'tools/drone_plan.py')
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)
DATA = ROOT / 'tests/data/drone_plan'
SUBJECT_BASE = ['--subject', 'shed', '--place', 'Private field', '--authorization', 'Owner approval recorded',
        '--lat', '37', '--lon', '-93', '--radius', '30', '--subject-height', '10',
        '--created', '2026-09-04T12:00:00Z']

# Arithmetic/export fixtures pin the ground sample independently of subject framing policy.
BASE = [*SUBJECT_BASE, '--gsd', '.05']
GOLDEN_ALTITUDE = ['--altitude', '120']


def args(extra=()):
    return planner.parser_for_cli().parse_args([*BASE, *extra])


def plan(extra=()):
    p = planner.make_plan(args(extra))
    p['schema_verified'] = False
    return p


@pytest.mark.parametrize('lat', [0, 37, 60, 84])
@pytest.mark.parametrize('east,north', [(-2000, -2000), (2000, 2000), (2000, -2000), (-2000, 2000)])
def test_projection_roundtrip(lat, east, north):
    point = planner.enu_to_wgs84(lat, 0, east, north)
    actual = planner.wgs84_to_enu(lat, 0, *point)
    assert actual == pytest.approx((east, north), abs=.001)


def test_north_independent_haversine():
    lat, lon = planner.enu_to_wgs84(37, -93, 0, 1000)
    # Independently evaluated WGS84 meridian radius at 37N: 6358550.52025703 m.
    assert lat == pytest.approx(37 + math.degrees(1000 / 6358550.52025703), abs=1e-10)
    distance = 2 * 6371008.8 * math.asin(abs(math.sin(math.radians(lat - 37) / 2)))
    assert distance == pytest.approx(1001.9592956, abs=.001)
    assert planner.ground_distance_m(37, -93, lat, lon) == pytest.approx(distance)


def test_camera_and_timing():
    assert math.tan(planner.camera_geometry()['hfov'] / 2) == pytest.approx(.75, abs=1e-12)
    assert planner.gsd(120) == pytest.approx(.0219727, abs=1e-7)
    assert planner.altitude_for_gsd(.05) == pytest.approx(273.067, abs=.001)
    p = plan()
    assert p['altitude']['binding_constraint'] == 'max_altitude_agl_m'
    rows = p['timing']['table'][:3]
    assert [r['speed_ms'] for r in rows] == pytest.approx([13.5, 9, 5.4])
    assert [r['motion_blur_px'] for r in rows] == pytest.approx([1.536, 1.024, .6144])
    assert [r['chosen'] for r in rows] == [False, False, True]
    assert p['expected_frames'] == math.ceil(p['duration']['total_seconds'] / p['timing']['interval_s'])


def default_plan(extra=()):
    return planner.make_plan(planner.parser_for_cli().parse_args([*SUBJECT_BASE, *extra]))


@pytest.mark.parametrize('height', [20, 30])
def test_subject_derived_target_and_binding(height):
    p = default_plan(['--subject-height', str(height), '--radius', '40'])
    target = height / 2000
    assert target < planner.load_defaults()['gsd_m']['value']
    assert p['inputs']['gsd'] is None
    assert p['altitude']['gsd_m'] == pytest.approx(target)
    assert p['altitude']['agl_m'] == pytest.approx(target * 8192 / 1.5)
    assert p['altitude']['binding_constraint'] == 'subject_pixels_across'


def test_explicit_gsd_overrides_subject_target():
    p = default_plan(['--subject-height', '20', '--gsd', '.015'])
    assert p['altitude']['gsd_m'] == pytest.approx(.015)
    assert p['altitude']['agl_m'] == pytest.approx(.015 * 8192 / 1.5)
    assert p['altitude']['binding_constraint'] == 'gsd_m'


def test_default_altitude_ceiling():
    p = default_plan(['--subject-height', '50'])
    assert p['altitude']['agl_m'] == 120
    assert p['altitude']['binding_constraint'] == 'max_altitude_agl_m'


@pytest.mark.parametrize('height', [3, 6, 10])
def test_small_subject_refusal_points_to_capture(height, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        planner.main([*SUBJECT_BASE, '--subject-height', str(height),
                      '--out', str(tmp_path / 'mission'), '--unverified-schema'])
    assert exc.value.code == 2
    message = capsys.readouterr().err
    assert 'exceeds ceiling=' in message
    assert 'the obstacle clearance allows' in message
    assert 'Capture it on foot with tools/capture_plan.py' in message
    assert '--gsd or --altitude' in message
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('height', [3, 6, 10])
def test_stated_altitude_clears_the_refusal_it_is_offered_for(height):
    # The refusal above offers --altitude as a way past the subject-derived
    # ceiling, so it has to actually work: the subject sets the ground sample
    # only when the operator has not stated an altitude.
    floor = height + planner.load_defaults()['min_obstacle_clearance_m']['value']
    # A small radius keeps the flight inside one battery, so the only thing
    # under test is the altitude window.
    site = ['--subject-height', str(height), '--radius', '12']
    p = default_plan([*site, '--altitude', str(floor + 2)])
    assert p['altitude']['agl_m'] == pytest.approx(floor + 2)
    assert p['altitude']['binding_constraint'] == 'operator_altitude'
    assert p['altitude']['gsd_m'] == pytest.approx(planner.gsd(floor + 2))
    # It is a genuine overrule: the same subject without it is refused.
    with pytest.raises(planner.Refusal, match='exceeds ceiling='):
        default_plan(site)


@pytest.mark.parametrize('height', [20, 30, 40])
def test_orbit_roof_pass_stays_inside_the_subject_window(height):
    # nadir_grid_altitude_m is pinned at the legal ceiling, which refused every
    # orbit whose subject was shorter than about 50 m.
    p = default_plan(['--subject-height', str(height), '--radius', '40', '--mode', 'orbit'])
    floor, ceiling = p['altitude']['floor_m'], p['altitude']['ceiling_m']
    roof = [w for w in p['waypoints'] if w['pass_name'] == 'nadir-grid']
    assert roof, 'orbit mode must still fly a roof pass'
    # takeoff_offset defaults to 0, so execute height above takeoff is AGL.
    assert p['inputs']['takeoff_offset'] == 0
    for w in roof:
        assert floor <= w['execute_height_m'] <= ceiling
    configured = planner.load_defaults()['nadir_grid_altitude_m']['value']
    if configured > ceiling:
        assert any('Nadir grid flown at' in x for x in p['warnings']), \
            'a clamped roof pass must say so on the operator card'


@pytest.mark.parametrize('radius', [5, 30, 90])
@pytest.mark.parametrize('height', [1, 20, 50])
@pytest.mark.parametrize('heading', [0, 37, 90, 173])
def test_overlap_from_emitted_coordinates(radius, height, heading):
    p = plan(['--radius', str(radius), '--subject-height', str(height), '--heading', str(heading)])
    for a, b in pairwise(p['waypoints']):
        if a['pass_name'] != b['pass_name'] or a['line'] != b['line']:
            continue
        step = planner.ground_distance_m(a['lat'], a['lon'], b['lat'], b['lon'])
        assert 1 - step / p['spacing']['along_footprint_m'] >= .798
    # Recover signed ENU and compare with the independently requested grid coordinates.
    expected = planner.grid_points(2 * radius, 2 * radius, heading, p['spacing']['line_m'],
                                   p['spacing']['photo_m'], p['spacing']['extension_m'])
    emitted = [w for w in p['waypoints'] if w['pass_name'] == 'grid-A']
    for w, (e, n, _) in zip(emitted, expected, strict=True):
        assert planner.wgs84_to_enu(37, -93, w['lat'], w['lon']) == pytest.approx((e, n), abs=.01)


def test_orbit_uses_capture_solver(monkeypatch):
    module = planner.load_capture_plan()
    calls = []

    def fake(radius, clearance, *rest):
        calls.append((radius, clearance))
        return {'stations': 17}
    monkeypatch.setattr(module, 'ring_geometry', fake)
    monkeypatch.setattr(planner, 'load_capture_plan', lambda: module)
    p = plan(['--mode', 'orbit'])
    assert len([w for w in p['waypoints'] if w['pass_name'] == 'orbit']) == 18
    r, clearance = calls[0]
    assert clearance == pytest.approx(math.hypot(r - math.hypot(60, 60) / 2, 115))


REFUSALS = [
    (['--altitude', '121'], 'max_altitude_agl_m=120'),
    (['--subject-height', '110'], 'floor=130.000'),
    (['--gsd', '.06'], 'max_gsd_m=0.05'),
    (['--resolution', '12mp', '--mode', 'orbit', '--radius', '90'], 'max_gsd_m=0.05'),
    (['--interval', '5', '--speed', '10'], '62.96%'),
    (['--interval', '3'], 'max_motion_blur_px=1'),
    (['--interval', '4'], 'intervals_s='),
    (['--speed', '.1'], 'min_mission_speed_ms=1'),
    (['--radius', '300'], 'usable_battery_minutes=25'),
    (['--radius', '1000'], 'max_distance_from_home_m=1000'),
    (['--radius', '1001'], 'max_subject_span_m=2000'),
    (['--rth-altitude', '29'], 'min_obstacle_clearance_m=20'),
    (['--lat', '86'], 'latitude_limit_deg'),
    (['--lon', '181'], 'longitude_limit_deg'),
    (['--lat', 'nan'], 'finite'),
    (['--radius', '0'], 'positive'),
    (['--subject-height', '-1'], 'positive'),
    (['--altitude', 'inf'], 'finite'),
    (['--created', 'yesterday'], 'created_iso8601'),
    (['--created', '2026-09-04'], 'timezone'),
    (['--authorization', ' '], 'authorization_required'),
    (['--place', 'National Park picnic'], 'screening aid'),
    (['--takeoff-offset', '120'], 'max_altitude_agl_m=120'),
    (['--altitude', '20'], 'floor=30.000'),
    (['--skeleton', 'absent.json'], 'does not exist'),
]


@pytest.mark.parametrize('extra,message', REFUSALS)
def test_refusals_write_nothing(extra, message, tmp_path, capsys):
    # The floor/ceiling conflict must also be refused under automatic subject framing.
    base = SUBJECT_BASE if extra == ['--subject-height', '110'] else BASE
    with pytest.raises(SystemExit) as exc:
        planner.main([*base, '--out', str(tmp_path / 'mission'), '--unverified-schema', *extra])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_bbox_and_bad_geometry(tmp_path, capsys):
    base = BASE.copy()
    i = base.index('--radius')
    del base[i:i + 2]
    for bbox in ('38,-94,36,-92', 'bad', '36,-94,38,-92', '37.001,-93,37.002,-92.999'):
        with pytest.raises(SystemExit) as exc:
            planner.main([*base, '--bbox', bbox, '--out', str(tmp_path / 'mission'), '--unverified-schema'])
        assert exc.value.code == 2
        assert not list(tmp_path.iterdir())
    assert 'bbox' in capsys.readouterr().err
    valid = planner.make_plan(planner.parser_for_cli().parse_args(
        [*base, '--bbox', '36.9999,-93.0001,37.0001,-92.9999']))
    assert valid['waypoints']


def test_no_admissible_interval(monkeypatch, tmp_path, capsys):
    defaults = planner.load_defaults()
    defaults['intervals_s']['value'] = [2, 3]
    monkeypatch.setattr(planner, 'load_defaults', lambda: defaults)
    with pytest.raises(SystemExit) as exc:
        planner.main([*BASE, '--out', str(tmp_path / 'mission')])
    assert exc.value.code == 2
    message = capsys.readouterr().err
    for text in ('No admissible interval', '2 s ->', '3 s ->', 'max_motion_blur_px=1'):
        assert text in message
    assert not list(tmp_path.iterdir())


def test_defaults_sources():
    doc = (ROOT / 'docs/drone.md').read_text()
    for value in planner.load_defaults().values():
        assert set(value) == {'value', 'source', 'rationale'}
        assert value['rationale']
        if value['source'] != 'operator default':
            assert '## ' + value['source'] in doc


def test_skeleton_gate_and_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(planner, 'SKELETON_PATH', tmp_path / 'missing.json')
    with pytest.raises(SystemExit) as exc:
        planner.main([*BASE, '--out', str(tmp_path / 'mission')])
    assert exc.value.code == 2
    assert 'missing skeleton' in capsys.readouterr().err
    assert sorted(x.suffix for x in tmp_path.iterdir()) == ['.json', '.md']
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(SystemExit) as exc:
        planner.main([*BASE, '--out', str(tmp_path / 'mission'), '--unverified-schema'])
    assert exc.value.code == 2
    assert 'force=false' in capsys.readouterr().err
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert planner.main([*BASE, '--out', str(tmp_path / 'mission'), '--unverified-schema', '--force']) == 0
    assert 'SCHEMA UNVERIFIED' in capsys.readouterr().out
    assert (tmp_path / 'mission.md').read_text().startswith('SCHEMA UNVERIFIED\n')


@pytest.mark.parametrize('mode', ['double-grid', 'orbit'])
def test_golden_and_determinism(mode, tmp_path):
    p = plan([*GOLDEN_ALTITUDE, '--mode', mode])
    skeleton = planner.load_skeleton(DATA / 'skeleton.json')
    a = planner.kmz_bytes(p, skeleton, p['waypoints'])
    b = planner.kmz_bytes(plan([*GOLDEN_ALTITUDE, '--mode', mode]), skeleton, p['waypoints'])
    assert a == b
    path = tmp_path / 'mission.kmz'
    path.write_bytes(a)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == list(planner.MEMBERS)
        for member in planner.MEMBERS:
            actual = archive.read(member)
            expected = (DATA / mode.replace('-', '_') / Path(member).name).read_bytes()
            if actual != expected:
                def nums(data):
                    return [float(x) for x in re.findall(rb'-?\d+\.?\d*', data)]
                x, y = nums(actual), nums(expected)
                equal = len(x) == len(y) and all(math.isclose(i, j, rel_tol=1e-9, abs_tol=1e-7)
                                                for i, j in zip(x, y, strict=True))
                pytest.fail(('numerically equal, bytes differ; ' if equal else 'numeric mismatch; ') +
                            'regenerate: python tests/test_drone_plan.py --regenerate-goldens')
            root = ET.fromstring(actual)
            assert sum(planner.local(n.tag) == 'Placemark' for n in root.iter()) == len(p['waypoints'])
            for node in root.iter():
                if planner.local(node.tag) == 'coordinates':
                    lon, lat = map(float, node.text.split(','))
                    assert -180 <= lon <= 180 and -85 <= lat <= 85
            assert archive.getinfo(member).date_time == (1980, 1, 1, 0, 0, 0)


def test_diff_and_export(tmp_path, monkeypatch, capsys):
    p = plan()
    data = planner.kmz_bytes(p, planner.synthetic_skeleton(), p['waypoints'])
    ours, export = tmp_path / 'ours.kmz', tmp_path / 'export.kmz'
    ours.write_bytes(data)
    with zipfile.ZipFile(ours) as old, zipfile.ZipFile(export, 'w') as new:
        for member in planner.MEMBERS:
            root = ET.fromstring(old.read(member))
            ET.SubElement(root, 'extraExportField')
            next(n for n in root.iter() if planner.local(n.tag) == 'missionConfig').remove(
                next(n for n in root.iter() if planner.local(n.tag) == 'finishAction'))
            new.writestr(member, ET.tostring(root))
    assert planner.main(['--diff', str(export), '--against', str(ours)]) == 1
    output = capsys.readouterr().out
    assert 'extraExportField' in output and 'finishAction' in output
    monkeypatch.setattr(planner, 'SKELETON_PATH', tmp_path / 'skeleton.json')
    answers = iter(['test-version', 'Air 3S RC 2'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    assert planner.main(['--from-export', str(ours), '--write-skeleton']) == 0
    skeleton = planner.load_skeleton(tmp_path / 'skeleton.json')
    assert skeleton['source']['aircraft'] == 'Air 3S RC 2'
    content = (tmp_path / 'skeleton.json').read_text()
    assert '-93.' not in content and '37.000' not in content
    assert 'SCHEMA UNVERIFIED' in planner.render_markdown(p)


def write_gps(path, lat=(37, 0, 0), lon=(93, 0, 0)):
    from PIL import Image, TiffImagePlugin

    exif = Image.Exif()
    exif[34853] = {1: 'N', 2: tuple(TiffImagePlugin.IFDRational(x) for x in lat),
                   3: 'W', 4: tuple(TiffImagePlugin.IFDRational(x) for x in lon)}
    Image.new('RGB', (10, 10)).save(path, exif=exif)


def test_photo(tmp_path):
    from PIL import Image, TiffImagePlugin

    path = tmp_path / 'gps.jpg'
    write_gps(path)
    assert planner.photo_centre(path) == (37, -93)
    Image.new('RGB', (10, 10)).save(path)
    with pytest.raises(planner.Refusal, match='GPS IFD missing'):
        planner.photo_centre(path)
    for lat, lon in (((0, 0, 0), (0, 0, 0)), ((86, 0, 0), (93, 0, 0)),
                     ((TiffImagePlugin.IFDRational(1, 0), 0, 0), (93, 0, 0))):
        write_gps(path, lat, lon)
        with pytest.raises(planner.Refusal):
            planner.photo_centre(path)
    write_gps(path)
    base = BASE.copy()
    for flag in ('--lat', '--lon'):
        i = base.index(flag)
        del base[i:i + 2]
    p = planner.make_plan(planner.parser_for_cli().parse_args([*base, '--from-photo', str(path)]))
    assert p['centre'] == {'lat': 37, 'lon': -93}


def test_cli_json(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'tools/drone_plan.py'), *BASE,
                             '--out', str(tmp_path / 'mission'), '--unverified-schema', '--json'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (tmp_path / 'mission.json').read_text()
    assert json.loads(result.stdout)['schema_verified'] is False
    assert 'SCHEMA UNVERIFIED' in result.stderr


def test_battery_split(tmp_path, capsys):
    p = plan(['--radius', '180', '--allow-multi-battery'])
    assert len(p['legs']) > 1
    assert all(leg['total_seconds'] <= 1500 for leg in p['legs'])
    assert sum(len(leg['passes']) for leg in p['legs']) == 6
    assert planner.main([*BASE, '--radius', '180', '--allow-multi-battery', '--unverified-schema',
                         '--out', str(tmp_path / 'mission')]) == 0
    capsys.readouterr()
    assert len(list(tmp_path.glob('mission-leg-*.kmz'))) == len(p['legs'])


@pytest.mark.parametrize('key,value,extra,message', [
    ('oblique_pitch_below_horizon_deg', 20, [], 'max_gsd_m=0.05'),
    ('max_gsd_m', .01, ['--gsd', '.01', '--subject-height', '1', '--mode', 'orbit'], 'max_gsd_m=0.01'),
    ('intervals_s', [2], [], 'No admissible interval'),
    ('usable_battery_minutes', 1, ['--allow-multi-battery'], 'cannot split inside a pass'),
])
def test_policy_refusals(key, value, extra, message, monkeypatch, tmp_path, capsys):
    defaults = planner.load_defaults()
    defaults[key]['value'] = value
    monkeypatch.setattr(planner, 'load_defaults', lambda: defaults)
    with pytest.raises(SystemExit) as exc:
        planner.main([*BASE, *extra, '--out', str(tmp_path / 'mission'), '--unverified-schema'])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_forced_admissible_row():
    p = plan(['--interval', '5', '--speed', '4'])
    chosen = [r for r in p['timing']['table'] if r['chosen']]
    assert len(chosen) == 1 and chosen[0]['speed_ms'] == 4
    assert chosen[0]['forward_overlap'] == pytest.approx(1 - 20 / 135)


def test_skeleton_order_enums_and_actions(tmp_path):
    skeleton = planner.synthetic_skeleton()
    for data in skeleton['documents'].values():
        root = planner.data_tree(data)
        for node in root.iter():
            if planner.local(node.tag) == 'droneEnumValue':
                node.text = '12345'
        data.update(planner.tree_data(root))
    p = plan(['--photo-mode', 'waypoint'])
    planner.apply_skeleton(p, skeleton)
    xml = planner.write_xml(p, skeleton, planner.MEMBERS[0], p['waypoints'])
    assert b'>12345<' in xml and b'>takePhoto<' in xml
    root = ET.fromstring(xml)
    indices = [n.text for n in root.iter() if planner.local(n.tag) == 'index']
    assert indices == [str(i) for i in range(len(p['waypoints']))]
    # Action templates must be present; never manufacture actions for an export that lacks them.
    for data in skeleton['documents'].values():
        root = planner.data_tree(data)
        for node in root.iter():
            for child in list(node):
                if planner.local(child.tag) == 'actionGroup':
                    node.remove(child)
        data.update(planner.tree_data(root))
    with pytest.raises(planner.Refusal, match='takePhoto action missing'):
        planner.write_xml(p, skeleton, planner.MEMBERS[0], p['waypoints'])


def test_normalization_refuses_unknown_geometry():
    skeleton = planner.synthetic_skeleton()
    roots = {k: planner.data_tree(v) for k, v in skeleton['documents'].items()}
    ET.SubElement(roots[planner.MEMBERS[0]], 'takeOffRefPoint').text = '37.0,-93.0,0'
    with pytest.raises(planner.Refusal, match='coordinate text'):
        planner.normalize_skeleton(roots, skeleton['source'])


@pytest.mark.parametrize('kind', ['missing', 'zero', 'range', 'denominator'])
def test_photo_cli_refusals(kind, tmp_path, capsys):
    from PIL import Image, TiffImagePlugin

    image = tmp_path / 'input.jpg'
    if kind == 'missing':
        Image.new('RGB', (10, 10)).save(image)
    else:
        latitude = {'zero': (0, 0, 0), 'range': (86, 0, 0),
                    'denominator': (TiffImagePlugin.IFDRational(1, 0), 0, 0)}[kind]
        write_gps(image, latitude, (0, 0, 0) if kind == 'zero' else (93, 0, 0))
    base = BASE.copy()
    for flag in ('--lat', '--lon'):
        i = base.index(flag)
        del base[i:i + 2]
    with pytest.raises(SystemExit) as exc:
        planner.main([*base, '--from-photo', str(image), '--out', str(tmp_path / 'mission'), '--unverified-schema'])
    assert exc.value.code == 2
    assert capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [image]


if __name__ == '__main__':
    if sys.argv[1:] != ['--regenerate-goldens']:
        raise SystemExit('usage: python tests/test_drone_plan.py --regenerate-goldens')
    DATA.mkdir(parents=True, exist_ok=True)
    skeleton = planner.synthetic_skeleton()
    (DATA / 'skeleton.json').write_text(json.dumps(skeleton, indent=2) + '\n')
    for mode in ('double-grid', 'orbit'):
        p = plan([*GOLDEN_ALTITUDE, '--mode', mode])
        directory = DATA / mode.replace('-', '_')
        directory.mkdir(exist_ok=True)
        for member in planner.MEMBERS:
            (directory / Path(member).name).write_bytes(planner.write_xml(p, skeleton, member, p['waypoints']))
