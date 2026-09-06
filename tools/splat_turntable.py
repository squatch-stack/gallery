#!/usr/bin/env python3
"""Capture turntable and elevation frames of a splat scene from the gallery viewer.

Blender cannot do this. The KIRI 3DGS add-on renders Gaussians through a GPU
shader system it builds interactively, so `blender -b` fails outright ("GPU
functions for drawing requires the gpu module to be initialized") and even a
GUI session driven by a script produces empty geometry because the add-on wants
a viewport pass first. The gallery viewer already renders these scenes
correctly, so the viewer is the renderer and this drives it.

Frames come from headless Chrome against tools/shot_server.py, which holds the
load event open until the scene has actually drawn — see that file for why
`--virtual-time-budget` cannot do the job.

    python3 tools/splat_turntable.py cannon --out out/cannon --frames 24
"""
from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHROME_CANDIDATES = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    'google-chrome', 'chromium', 'chromium-browser',
)


def find_chrome(explicit=None):
    """Resolve the browser. An explicit path that is wrong is an error.

    Falling back to a different browser than the one named would silently
    render the frames with something the operator did not choose.
    """
    if explicit:
        if Path(explicit).exists() or shutil.which(explicit):
            return explicit
        raise SystemExit(f'--chrome {explicit}: not found')
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    raise SystemExit('no Chrome or Chromium found; pass --chrome')


def frame_urls(base, scene, mode, frames, elevation, distance, heading, wait_ms):
    """(name, url) per frame. Elevations are the four cardinals at eye level."""
    views = []
    if mode in ('elevation', 'both'):
        for name, offset in (('front', 0), ('right', 90), ('back', 180), ('left', 270)):
            views.append((f'elevation-{name}', (offset + heading) % 360, 0.0))
    if mode in ('turntable', 'both'):
        for i in range(frames):
            az = (heading + 360.0 * i / frames) % 360
            views.append((f'turntable-{i:03d}', az, elevation))
    return [(name, f'{base}/shot.html?scene={scene}&az={az:g}&el={el:g}'
                   f'&d={distance:g}&bare=1&ms={wait_ms}') for name, az, el in views]


def capture(chrome, url, path, size, timeout):
    cmd = [chrome, '--headless=new', '--enable-unsafe-swiftshader', '--hide-scrollbars',
           f'--window-size={size},{size}', f'--screenshot={path}', url]
    subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    return path.exists() and path.stat().st_size > 20_000


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('scene')
    p.add_argument('--out', required=True, help='output prefix')
    p.add_argument('--base', default='http://localhost:8875')
    p.add_argument('--mode', choices=('turntable', 'elevation', 'both'), default='both')
    p.add_argument('--frames', type=int, default=24)
    p.add_argument('--elevation-deg', type=float, default=12.0)
    p.add_argument('--distance', type=float, default=2.2)
    p.add_argument('--heading', type=float, default=0.0)
    p.add_argument('--size', type=int, default=900)
    p.add_argument('--wait-ms', type=int, default=12000,
                   help='how long to hold the load event; raise it if frames come out empty')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--chrome')
    p.add_argument('--encode', metavar='DIR',
                   help='also write DIR/<scene>.mp4 and .jpg for the gallery cards')
    p.add_argument('--seconds', type=float, default=3.2,
                   help='rotation length for --encode; every card must match or the grid '
                        'spins at several speeds')
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    if args.frames < 1:
        raise SystemExit('--frames must be positive')

    chrome = find_chrome(args.chrome)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    views = frame_urls(args.base, args.scene, args.mode, args.frames,
                       args.elevation_deg, args.distance, args.heading, args.wait_ms)
    timeout = args.wait_ms / 1000 + 60
    started = time.time()
    empty = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(capture, chrome, url,
                               Path(f'{args.out}-{name}.png'), args.size, timeout): name
                   for name, url in views}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            if not future.result():
                empty.append(name)
    print(f'{len(views) - len(empty)}/{len(views)} frames in {time.time() - started:.0f}s '
          f'-> {args.out}-*.png')
    if args.encode and not empty:
        video, poster, actual = encode(args.scene, f'{args.out}-turntable-*.png',
                                       args.encode, args.seconds, args.frames)
        print(f'encoded {video.name} ({actual:g}s) and {poster.name}')
    if empty:
        # A too-short wait shows up as a spinner, which is a small PNG, not a crash.
        raise SystemExit(f'{len(empty)} frame(s) came out empty ({", ".join(sorted(empty)[:4])}'
                         f'{"..." if len(empty) > 4 else ""}); raise --wait-ms')


def encode(scene, frames_glob, out_dir, seconds, frames, width=480, crf=28):
    """One mp4 plus a poster still, at a duration that is actually the one asked for.

    minterpolate alone is not deterministic here: fed the same 16 frames it
    returned clips of 2.27, 2.53 and 3.53 seconds depending on how well it could
    estimate motion, so the cards span at three different speeds. Pinning the
    output with a trailing `fps` filter and `-r` makes the length exact, and the
    duration is verified rather than assumed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    video, poster = out_dir / f'{scene}.mp4', out_dir / f'{scene}.jpg'
    rate = frames / seconds
    chain = (f'scale={width}:-2,minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:vsbmc=1,fps=30')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', f'{rate:g}',
                    '-pattern_type', 'glob', '-i', frames_glob, '-vf', chain, '-r', '30',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', str(crf),
                    '-movflags', '+faststart', str(video)], check=True)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i',
                    frames_glob.replace('*', '000'), '-vf', f'scale={width}:-2',
                    '-q:v', '4', str(poster)], check=True)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', str(video)], capture_output=True, text=True)
    actual = float(probe.stdout.strip() or 0)
    if abs(actual - seconds) > 0.05:
        raise SystemExit(f'{video.name}: asked for {seconds:g}s, got {actual:g}s')
    return video, poster, actual


if __name__ == '__main__':
    main()
