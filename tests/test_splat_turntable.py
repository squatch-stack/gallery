"""Frame planning and the shot wrapper. No browser and no Blender involved."""
import importlib.util
from itertools import pairwise
import re
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load('splat_turntable', 'tools/splat_turntable.py')
server = _load('shot_server', 'tools/shot_server.py')
BASE = 'http://h:1'


def params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def test_elevations_are_the_four_cardinals_at_eye_level():
    views = tool.frame_urls(BASE, 's', 'elevation', 24, 12.0, 2.2, 0.0, 9000)
    assert [n for n, _ in views] == ['elevation-front', 'elevation-right',
                                     'elevation-back', 'elevation-left']
    assert [float(params(u)['az']) for _, u in views] == [0, 90, 180, 270]
    # An elevation is a level view; a tilted one is not a drawing you can measure.
    assert all(float(params(u)['el']) == 0 for _, u in views)


@pytest.mark.parametrize('frames', [1, 8, 24, 36])
def test_turntable_steps_evenly_all_the_way_round(frames):
    views = tool.frame_urls(BASE, 's', 'turntable', frames, 12.0, 2.2, 0.0, 9000)
    az = [float(params(u)['az']) for _, u in views]
    assert len(az) == frames and az[0] == 0
    assert all(0 <= a < 360 for a in az)
    if frames > 1:
        step = 360.0 / frames
        assert all(b - a == pytest.approx(step) for a, b in pairwise(az))
    assert all(float(params(u)['el']) == 12.0 for _, u in views)


def test_heading_rotates_both_sets_and_stays_in_range():
    views = tool.frame_urls(BASE, 's', 'both', 4, 12.0, 2.2, 270.0, 9000)
    by = {n: float(params(u)['az']) for n, u in views}
    assert by['elevation-front'] == 270 and by['elevation-right'] == 0  # wrapped, not 360
    assert all(0 <= v < 360 for v in by.values())


def test_both_mode_is_elevations_then_turntable():
    views = tool.frame_urls(BASE, 's', 'both', 6, 12.0, 2.2, 0.0, 9000)
    assert len(views) == 10
    assert [n for n, _ in views][:4] == ['elevation-front', 'elevation-right',
                                         'elevation-back', 'elevation-left']


def test_every_frame_asks_for_bare_and_carries_the_wait():
    for _, url in tool.frame_urls(BASE, 'sc', 'both', 3, 9.0, 1.7, 0.0, 12345):
        q = params(url)
        assert q['bare'] == '1' and q['ms'] == '12345' and q['scene'] == 'sc'
        assert q['d'] == '1.7'


def test_names_are_unique_and_sort_into_frame_order():
    names = [n for n, _ in tool.frame_urls(BASE, 's', 'turntable', 12, 12.0, 2.2, 0.0, 9000)]
    assert len(set(names)) == len(names)
    assert names == sorted(names), 'zero-padded so a glob feeds ffmpeg in order'


def test_find_chrome_refuses_rather_than_guessing():
    with pytest.raises(SystemExit):
        tool.find_chrome('/nonexistent/chrome-binary-xyz')


def test_wrapper_hides_chrome_only_when_asked():
    bare = server.WRAPPER.format(src='viewer.html?scene=x', ms='9000', bare='1')
    plain = server.WRAPPER.format(src='viewer.html?scene=x', ms='9000', bare='0')
    assert '#back,#hud,#load' in bare and 'var bare = 1;' in bare
    assert 'var bare = 0;' in plain
    # The delay is what makes the capture wait for a drawn frame.
    for page in (bare, plain):
        assert '/slow?ms=9000' in page and 'viewer.html?scene=x' in page


def test_slow_endpoint_is_capped():
    assert server.MAX_WAIT_MS <= 120_000


def test_encode_verifies_the_duration_it_was_asked_for(tmp_path, monkeypatch):
    """The encoder must not accept whatever length ffmpeg happens to produce.

    minterpolate returned 2.27, 2.53 and 3.53 second clips from equal frame
    counts, which put the gallery cards at three different speeds.
    """
    calls = []

    class Result:
        def __init__(self, out=''):
            self.stdout = out

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == 'ffprobe':
            return Result(fake_run.duration)
        Path(cmd[-1]).write_bytes(b'x')
        return Result()

    monkeypatch.setattr(tool.subprocess, 'run', fake_run)
    fake_run.duration = '3.2'
    video, poster, actual = tool.encode('s', str(tmp_path / 's-turntable-*.png'),
                                        tmp_path, 3.2, 16)
    assert actual == pytest.approx(3.2) and video.exists() and poster.exists()
    assert calls.count('ffmpeg') == 2 and 'ffprobe' in calls

    fake_run.duration = '2.53'
    with pytest.raises(SystemExit, match=re.escape('asked for 3.2s, got 2.53s')):
        tool.encode('s', str(tmp_path / 's-turntable-*.png'), tmp_path, 3.2, 16)


def test_encode_rate_makes_the_requested_length():
    # frames / rate == seconds is the whole arithmetic; a wrong rate is a
    # grid that spins at the wrong speed and nothing else complains.
    for frames, seconds in ((16, 3.2), (12, 3.0), (24, 4.0)):
        assert frames / (frames / seconds) == pytest.approx(seconds)
