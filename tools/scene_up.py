"""The gravity direction of a COLMAP-solved scene, for scenes.json.

    ~/.venvs/photogram/bin/python tools/scene_up.py \
        ~/Documents/squatch-captures/photo-subjects/cannon [sparse-dir] [--axis -y|+y|-x|+x]

COLMAP solves carry no gravity, so a splat renders at whatever tilt the first
camera had. Handheld photographs are taken roughly level, so the mean of the
solved cameras' own up axes is a good estimate of world up. This prints that
vector in the frame the gallery viewer uses: the viewer flips the splat
180 degrees about x on load (y and z negate), then aligns `entry.up` to +y,
so the value here is the world-up estimate with y and z negated.

Validated against the cannon, whose `up` in scenes.json was measured by
hand: the tool reproduces it to three decimals.
"""
import json
import pathlib
import sys

import numpy as np
import pycolmap

AXES = {"-y": (0, -1, 0), "+y": (0, 1, 0), "-x": (-1, 0, 0), "+x": (1, 0, 0)}


def main():
    subject = pathlib.Path(sys.argv[1]).expanduser()
    args = [x for x in sys.argv[1:] if not x.startswith("--") and x not in AXES]
    sparse = subject / (args[1] if len(args) > 1 else "sparse")
    sub = sparse / "0" if (sparse / "0").is_dir() else sparse
    rec = pycolmap.Reconstruction(sub)
    Rs = [im.cam_from_world().rotation.matrix() for im in rec.images.values()]  # world -> camera
    # Which camera axis pointed up depends on how the phone was held: -y for
    # landscape frames (COLMAP's camera y points down), +/-x for portrait
    # frames stored sideways with an EXIF rotation. Try the four candidates
    # and keep the one the views agree on most.
    # Default to -y: it is the axis that reproduced the cannon's hand-measured,
    # visually levelled up vector. The other three are printed so a scene that
    # renders tilted can be re-run with --axis; the lowest spread is NOT a
    # reliable pick (it chose -x for the cannon, which the gallery disproves).
    axis_name = sys.argv[sys.argv.index("--axis") + 1] if "--axis" in sys.argv else "-y"
    ups = np.array([R.T @ np.array(AXES[axis_name], dtype=float) for R in Rs])
    up = ups.mean(axis=0)
    up /= np.linalg.norm(up)
    spread = float(np.degrees(np.mean([np.arccos(np.clip(u @ up, -1, 1)) for u in ups])))
    # Where the cameras converge: the least-squares point closest to every
    # optical axis. For an orbit around a subject that is the subject, and the
    # mean camera distance to it is the orbit radius, both in the solve's own
    # (arbitrary) units - a far better crop centre than the splat mass, which
    # for a building on a lawn sits in the lawn.
    A = np.zeros((3, 3))
    b = np.zeros(3)
    centers = []
    for im in rec.images.values():
        pose = im.cam_from_world()
        R = pose.rotation.matrix()
        c = -R.T @ pose.translation
        d = R.T @ np.array([0.0, 0.0, 1.0])
        d /= np.linalg.norm(d)
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ c
        centers.append(c)
    focus = np.linalg.solve(A, b)
    orbit = float(np.mean([np.linalg.norm(c - focus) for c in centers]))
    alt = {n: None for n in ("-y", "+y", "-x", "+x")}
    for name, axis in AXES.items():
        u = np.array([R.T @ np.array(axis, dtype=float) for R in Rs]).mean(axis=0)
        u /= np.linalg.norm(u)
        alt[name] = [round(float(v), 4) for v in (u[0], -u[1], -u[2])]
    viewer = np.array([up[0], -up[1], -up[2]])
    print(json.dumps({"subject": subject.name, "views": len(Rs), "camera_up_axis": axis_name,
                      "spread_deg": round(spread, 1), "up": [round(float(v), 4) for v in viewer],
                      "up_by_axis": alt,
                      "focus": [round(float(v), 3) for v in focus], "orbit_radius": round(orbit, 3)}))


if __name__ == "__main__":
    main()
