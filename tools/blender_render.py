#!/usr/bin/env python3
"""Headless Blender turntables and orthographic elevations of a gallery mesh.

Run through Blender, not through this interpreter:

    /Applications/Blender.app/Contents/MacOS/Blender -b \
        --python tools/blender_render.py -- --mesh scenes/cannon-mesh.glb --out out/cannon

Headless on purpose. The Blender MCP bridge is for looking at a scene
interactively; a deliverable render has to be reproducible from a file, and a
bridge session is not. Nothing here imports bpy at module scope, so the
argument parser and the framing maths stay testable without Blender.

A headless Blender loads the enabled add-ons too, so it briefly starts a second
blender-mcp server. When the GUI Blender already holds 127.0.0.1:9876 that bind
fails, harmlessly and noisily, and the GUI's own bridge is unaffected. Nothing
to fix here; it is just why a render log mentions a server it never needed.
"""
from __future__ import annotations

import argparse
import math
import sys


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--mesh', required=True, help='.glb/.gltf/.obj/.ply to render')
    p.add_argument('--out', required=True, help='output prefix; frames get suffixes')
    p.add_argument('--mode', choices=('turntable', 'elevation', 'both'), default='both')
    p.add_argument('--frames', type=int, default=36, help='turntable frames (default 36 = 10 deg)')
    p.add_argument('--elevation-deg', type=float, default=12.0, help='camera pitch above horizon')
    p.add_argument('--samples', type=int, default=64, help='Cycles samples')
    p.add_argument('--resolution', type=int, default=1080)
    # Engine identifiers move between Blender releases: 4.2 called EEVEE
    # 'BLENDER_EEVEE_NEXT', 5.x calls it 'BLENDER_EEVEE' again. Take any string
    # and validate against the running Blender's own enum, so a rename is a
    # clear message rather than a TypeError forty lines in.
    p.add_argument('--engine', default='CYCLES',
                   help='CYCLES (default), BLENDER_EEVEE for speed, BLENDER_WORKBENCH for flat')
    p.add_argument('--margin', type=float, default=1.10, help='framing slack around the subject')
    p.add_argument('--film-transparent', action='store_true', help='alpha background instead of world grey')
    p.add_argument('--heading', type=float, default=0.0,
                   help='degrees added to every azimuth, so "front" can be aimed at the '
                        "subject's actual front. A photogrammetry mesh has no canonical "
                        'front and no estimator can invent one; this is the operator saying '
                        'which way the building faces.')
    p.add_argument('--up', default='z',
                   help="which way is up in the mesh's own frame: 'z' (default, correct for "
                        "RealityKit/posekit output), 'auto' to estimate it, or 'x,y,z'. "
                        "Photogrammetry meshes are arbitrarily oriented and an elevation "
                        "rendered against the wrong up is a picture of the roof.")
    return p.parse_args(argv if argv is not None else _blender_argv())


def _blender_argv():
    """Args for this script, whether Blender or a shell is the caller.

    Blender passes everything after a bare "--" to the script and keeps the
    rest for itself. Run directly (`python tools/blender_render.py --help`)
    there is no "--", and falling back to [] there made --help print a missing
    -mesh error instead of the help.
    """
    return sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]


def orbit_position(centre, radius, azimuth_deg, elevation_deg):
    """Camera position on a sphere around centre. Z is up, matching Blender."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    return (centre[0] + radius * math.cos(el) * math.sin(az),
            centre[1] - radius * math.cos(el) * math.cos(az),
            centre[2] + radius * math.sin(el))


def parse_up(text, estimate=None):
    """Resolve --up to a unit vector in the mesh's own frame.

    A gallery mesh carries no orientation of its own: cannon-mesh.glb happens
    to land Z-up because RealityKit writes it that way, while a Scaniverse
    export does not, and rendering an elevation against the wrong up produced a
    photograph of the springhouse's roof labelled "front".
    """
    text = (text or 'z').strip().lower()
    if text == 'z':
        return (0.0, 0.0, 1.0)
    if text == 'auto':
        if estimate is None:
            raise SystemExit("--up auto needs vertices to estimate from")
        return estimate
    parts = text.split(',')
    if len(parts) != 3:
        raise SystemExit(f"--up {text}: expected 'z', 'auto', or three comma-separated numbers")
    try:
        v = [float(x) for x in parts]
    except ValueError as exc:
        raise SystemExit(f"--up {text}: {exc}") from exc
    n = math.sqrt(sum(x * x for x in v))
    if not n or not all(math.isfinite(x) for x in v):
        raise SystemExit(f"--up {text}: must be a finite non-zero vector")
    return tuple(x / n for x in v)


def rotation_bringing_up_to_z(up):
    """Rotation matrix (list of 3 rows) taking `up` onto +Z, as Blender wants."""
    ux, uy, uz = up
    if uz > 1 - 1e-12:
        return [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
    if uz < -1 + 1e-12:
        return [[1., 0., 0.], [0., -1., 0.], [0., 0., -1.]]
    vx, vy, vz = uy, -ux, 0.0          # up x z, in that order: we rotate up ONTO z
    c = uz                              # up . z
    k = [[0., -vz, vy], [vz, 0., -vx], [-vy, vx, 0.]]
    kk = [[sum(k[i][m] * k[m][j] for m in range(3)) for j in range(3)] for i in range(3)]
    f = 1.0 / (1.0 + c)
    return [[(1.0 if i == j else 0.0) + k[i][j] + kk[i][j] * f for j in range(3)] for i in range(3)]


def frame_radius(size, margin, fov_rad):
    """Distance at which a sphere enclosing the subject fills the frame."""
    enclosing = math.sqrt(sum(s * s for s in size)) / 2
    return margin * enclosing / math.tan(fov_rad / 2)


def elevation_frame(size, azimuth_deg, margin, longest_px):
    """Ortho scale and pixel resolution for one elevation.

    A square frame is the wrong frame: the cannon is 3.8 m across and 1.3 m
    tall, so rendered 1:1 it fills a third of its own elevation no matter what
    ortho scale is chosen. An elevation is a drawing a client measures off, so
    the frame takes the subject's own aspect and the pixels follow.

    Returns (ortho_scale, res_x, res_y). Blender applies ortho_scale to the
    larger pixel dimension, which is why the scale is the larger extent.
    """
    az = math.radians(azimuth_deg)
    across = abs(size[0] * math.cos(az)) + abs(size[1] * math.sin(az))
    up = size[2]
    if across <= 0 or up <= 0:
        return margin * max(across, up, 1e-6), longest_px, longest_px
    if across >= up:
        return margin * across, longest_px, max(1, round(longest_px * up / across))
    return margin * up, max(1, round(longest_px * across / up)), longest_px


def main():
    args = parse_args()
    import bpy
    import mathutils

    bpy.ops.wm.read_homefile(use_empty=True)  # NOT read_factory_settings: that
    # resets add-on state and, over the MCP bridge, kills the connection.

    lower = args.mesh.lower()
    if lower.endswith(('.glb', '.gltf')):
        bpy.ops.import_scene.gltf(filepath=args.mesh)
    elif lower.endswith('.obj'):
        bpy.ops.wm.obj_import(filepath=args.mesh)
    elif lower.endswith('.ply'):
        bpy.ops.wm.ply_import(filepath=args.mesh)
    else:
        raise SystemExit(f'unsupported mesh format: {args.mesh}')

    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not meshes:
        raise SystemExit(f'no mesh objects imported from {args.mesh}')

    estimate = None
    if args.up.strip().lower() == 'auto':
        # The thinnest principal axis of the vertex cloud. A building or an
        # object standing on ground varies least vertically, so the smallest
        # eigenvector is the ground normal. Reported, never silent.
        import numpy as np
        pts = np.array([list(o.matrix_world @ v.co) for o in meshes for v in o.data.vertices],
                       dtype=float)
        centred = pts - pts.mean(0)
        _, _, vt = np.linalg.svd(centred[::max(1, len(centred) // 50000)], full_matrices=False)
        estimate = tuple(float(x) for x in vt[2])
        print(f'--up auto estimated {tuple(round(x, 4) for x in estimate)} '
              f'from {len(pts):,} vertices')

    up = parse_up(args.up, estimate)
    if up != (0.0, 0.0, 1.0):
        rot = mathutils.Matrix([mathutils.Vector(r) for r in rotation_bringing_up_to_z(up)]).to_4x4()
        for o in meshes:
            o.matrix_world = rot @ o.matrix_world
        bpy.context.view_layer.update()
        print(f'rotated the subject so {tuple(round(x, 4) for x in up)} points up')

    corners = [o.matrix_world @ mathutils.Vector(c) for o in meshes for c in o.bound_box]
    lo = mathutils.Vector((min(p.x for p in corners), min(p.y for p in corners), min(p.z for p in corners)))
    hi = mathutils.Vector((max(p.x for p in corners), max(p.y for p in corners), max(p.z for p in corners)))
    centre, size = (lo + hi) / 2, (hi - lo)
    print(f'subject size {tuple(round(v, 3) for v in size)} centred {tuple(round(v, 3) for v in centre)}')

    scene = bpy.context.scene
    # Set it and report what happened, rather than pre-checking against
    # RenderSettings' enum: that enum is populated lazily, so reading it early
    # can omit CYCLES and reject a perfectly valid default.
    try:
        scene.render.engine = args.engine
    except TypeError as exc:
        raise SystemExit(f'--engine {args.engine} rejected by this Blender: {exc}') from exc
    if args.engine == 'CYCLES':
        scene.cycles.samples = args.samples
    scene.render.resolution_x = scene.render.resolution_y = args.resolution
    scene.render.film_transparent = args.film_transparent

    world = bpy.data.worlds.new('studio')
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs[0].default_value = (0.05, 0.05, 0.055, 1)
    scene.world = world

    key = bpy.data.objects.new('key', bpy.data.lights.new('key', type='SUN'))
    key.data.energy, key.rotation_euler = 4.0, (math.radians(55), 0, math.radians(35))
    scene.collection.objects.link(key)

    cam_data = bpy.data.cameras.new('cam')
    cam = bpy.data.objects.new('cam', cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    track = cam.constraints.new('TRACK_TO')
    target = bpy.data.objects.new('target', None)
    target.location = centre
    scene.collection.objects.link(target)
    track.target, track.track_axis, track.up_axis = target, 'TRACK_NEGATIVE_Z', 'UP_Y'

    radius = frame_radius(size, args.margin, cam_data.angle)

    def shoot(path):
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        print('wrote', path)

    if args.mode in ('elevation', 'both'):
        # Orthographic: the drawing a client measures off, with no perspective.
        cam_data.type = 'ORTHO'
        for name, offset in (('front', 0), ('right', 90), ('back', 180), ('left', 270)):
            az = offset + args.heading
            scale, rx, ry = elevation_frame(size, az, args.margin, args.resolution)
            cam_data.ortho_scale = scale
            scene.render.resolution_x, scene.render.resolution_y = rx, ry
            cam.location = orbit_position(centre, radius, az, 0.0)
            shoot(f'{args.out}-elevation-{name}.png')
        scene.render.resolution_x = scene.render.resolution_y = args.resolution
        cam_data.type = 'PERSP'

    if args.mode in ('turntable', 'both'):
        for i in range(args.frames):
            cam.location = orbit_position(centre, radius,
                                          args.heading + 360.0 * i / args.frames,
                                          args.elevation_deg)
            shoot(f'{args.out}-turntable-{i:03d}.png')


if __name__ == '__main__':
    main()
