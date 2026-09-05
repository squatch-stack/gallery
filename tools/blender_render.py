#!/usr/bin/env python3
"""Headless Blender turntables and orthographic elevations of a gallery mesh.

Run through Blender, not through this interpreter:

    /Applications/Blender.app/Contents/MacOS/Blender -b \
        --python tools/blender_render.py -- --mesh scenes/cannon-mesh.glb --out out/cannon

Headless on purpose. The Blender MCP bridge is for looking at a scene
interactively; a deliverable render has to be reproducible from a file, and a
bridge session is not. Nothing here imports bpy at module scope, so the
argument parser and the framing maths stay testable without Blender.
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
    p.add_argument('--engine', choices=('CYCLES', 'BLENDER_EEVEE_NEXT'), default='CYCLES')
    p.add_argument('--margin', type=float, default=1.10, help='framing slack around the subject')
    p.add_argument('--film-transparent', action='store_true', help='alpha background instead of world grey')
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

    corners = [o.matrix_world @ mathutils.Vector(c) for o in meshes for c in o.bound_box]
    lo = mathutils.Vector((min(p.x for p in corners), min(p.y for p in corners), min(p.z for p in corners)))
    hi = mathutils.Vector((max(p.x for p in corners), max(p.y for p in corners), max(p.z for p in corners)))
    centre, size = (lo + hi) / 2, (hi - lo)
    print(f'subject size {tuple(round(v, 3) for v in size)} centred {tuple(round(v, 3) for v in centre)}')

    scene = bpy.context.scene
    scene.render.engine = args.engine
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
        for name, az in (('front', 0), ('right', 90), ('back', 180), ('left', 270)):
            scale, rx, ry = elevation_frame(size, az, args.margin, args.resolution)
            cam_data.ortho_scale = scale
            scene.render.resolution_x, scene.render.resolution_y = rx, ry
            cam.location = orbit_position(centre, radius, az, 0.0)
            shoot(f'{args.out}-elevation-{name}.png')
        scene.render.resolution_x = scene.render.resolution_y = args.resolution
        cam_data.type = 'PERSP'

    if args.mode in ('turntable', 'both'):
        for i in range(args.frames):
            cam.location = orbit_position(centre, radius, 360.0 * i / args.frames, args.elevation_deg)
            shoot(f'{args.out}-turntable-{i:03d}.png')


if __name__ == '__main__':
    main()
