"""Framing and orbit arithmetic for the headless Blender renders.

Nothing here needs Blender: blender_render.py imports bpy only inside main(),
precisely so this geometry is testable in the ordinary suite.
"""
import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('blender_render', ROOT / 'tools/blender_render.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

CANNON = (3.511, 3.803, 1.271)  # the real cannon-mesh.glb bounds, in metres


@pytest.mark.parametrize('azimuth,expected', [
    (0, (0, -1, 0)), (90, (1, 0, 0)), (180, (0, 1, 0)), (270, (-1, 0, 0)),
])
def test_orbit_azimuth_is_clockwise_from_front(azimuth, expected):
    x, y, z = tool.orbit_position((0, 0, 0), 1.0, azimuth, 0.0)
    assert (x, y, z) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize('elevation', [0, 12, 45, 89])
def test_orbit_keeps_radius_and_raises_with_elevation(elevation):
    centre = (1.0, -2.0, 0.5)
    p = tool.orbit_position(centre, 7.0, 33.0, elevation)
    assert math.dist(p, centre) == pytest.approx(7.0)
    assert p[2] - centre[2] == pytest.approx(7.0 * math.sin(math.radians(elevation)))


def test_frame_radius_encloses_the_whole_subject():
    fov = math.radians(39.6)  # Blender's default 50 mm lens
    r = tool.frame_radius(CANNON, 1.0, fov)
    half_diagonal = math.sqrt(sum(s * s for s in CANNON)) / 2
    # At that distance the enclosing sphere subtends exactly the field of view.
    assert math.atan(half_diagonal / r) == pytest.approx(fov / 2)


def test_elevation_frame_takes_the_subjects_own_aspect():
    # A 3.8 m wide, 1.3 m tall subject in a square frame fills a third of it.
    scale, rx, ry = tool.elevation_frame(CANNON, 90, 1.0, 900)
    assert (rx, ry) == (900, round(900 * CANNON[2] / CANNON[1]))
    assert scale == pytest.approx(CANNON[1])
    assert ry < rx, 'a wide subject must not be rendered into a square frame'


def test_elevation_frame_sees_different_axes_from_front_and_side():
    front = tool.elevation_frame(CANNON, 0, 1.0, 900)
    side = tool.elevation_frame(CANNON, 90, 1.0, 900)
    assert front[0] == pytest.approx(CANNON[0])
    assert side[0] == pytest.approx(CANNON[1])


def test_elevation_frame_flips_when_the_subject_is_taller_than_wide():
    scale, rx, ry = tool.elevation_frame((2.0, 2.0, 10.0), 0, 1.0, 900)
    assert (rx, ry) == (180, 900)
    assert scale == pytest.approx(10.0)


def test_elevation_frame_survives_a_flat_subject():
    scale, rx, ry = tool.elevation_frame((4.0, 4.0, 0.0), 0, 1.1, 800)
    assert rx == ry == 800 and scale > 0


def test_margin_scales_the_frame_linearly():
    tight = tool.elevation_frame(CANNON, 90, 1.0, 900)[0]
    loose = tool.elevation_frame(CANNON, 90, 1.25, 900)[0]
    assert loose == pytest.approx(tight * 1.25)


def test_parse_args_requires_mesh_and_out():
    a = tool.parse_args(['--mesh', 'x.glb', '--out', 'o'])
    assert (a.mesh, a.out, a.mode, a.engine) == ('x.glb', 'o', 'both', 'CYCLES')
    with pytest.raises(SystemExit):
        tool.parse_args(['--mesh', 'x.glb'])


@pytest.mark.parametrize('text,expected', [
    ('z', (0, 0, 1)), ('Z', (0, 0, 1)),
    ('0,0,1', (0, 0, 1)), ('0,0,-2', (0, 0, -1)), ('0,3,0', (0, 1, 0)),
])
def test_parse_up_normalises(text, expected):
    assert tool.parse_up(text) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize('bad', ['', '1,2', 'a,1,2', '0,0,0', 'nan,0,1', '1,2,3,4'])
def test_parse_up_refuses_nonsense(bad):
    if bad == '':
        assert tool.parse_up(bad) == (0.0, 0.0, 1.0)  # empty means the default
        return
    with pytest.raises(SystemExit):
        tool.parse_up(bad)


def test_parse_up_auto_needs_an_estimate():
    assert tool.parse_up('auto', (0, 1, 0)) == (0, 1, 0)
    with pytest.raises(SystemExit):
        tool.parse_up('auto')


@pytest.mark.parametrize('up', [
    (0, 0, 1), (0, 0, -1), (0, 1, 0), (1, 0, 0),
    (-0.3973, -0.5569, 0.7294), (0.0514, -0.2445, 0.9683), (1, 1, 1),
])
def test_rotation_takes_up_onto_z_and_stays_a_rotation(up):
    n = math.sqrt(sum(c * c for c in up))
    unit = tuple(c / n for c in up)
    R = tool.rotation_bringing_up_to_z(unit)
    # It must map up onto +Z, not -Z: the sign error here rendered elevations
    # upside down while still looking like a plausible picture.
    image = [sum(R[i][j] * unit[j] for j in range(3)) for i in range(3)]
    assert image == pytest.approx((0, 0, 1), abs=1e-9)
    # Proper rotation: orthonormal with determinant +1, so nothing is mirrored.
    for i in range(3):
        for j in range(3):
            dot = sum(R[i][k] * R[j][k] for k in range(3))
            assert dot == pytest.approx(1.0 if i == j else 0.0, abs=1e-9)
    det = (R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
           - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
           + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]))
    assert det == pytest.approx(1.0, abs=1e-9)


def test_heading_shifts_every_elevation_together():
    a = tool.parse_args(['--mesh', 'm.glb', '--out', 'o'])
    b = tool.parse_args(['--mesh', 'm.glb', '--out', 'o', '--heading', '90'])
    assert (a.heading, b.heading) == (0.0, 90.0)
    # front at heading 90 is the same camera as right at heading 0
    assert tool.orbit_position((0, 0, 0), 5, 0 + 90, 0) == pytest.approx(
        tool.orbit_position((0, 0, 0), 5, 90 + 0, 0))
