#!/usr/bin/env python3
"""Offline DJI Air 3S subject planning; aircraft schema remains unverified."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import io
from itertools import pairwise
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

DEFAULTS_PATH = Path(__file__).with_name('drone_defaults.json')
SKELETON_PATH = Path(__file__).with_name('dji_air3s_skeleton.json')
KML = 'http://www.opengis.net/kml/2.2'
WPML = 'http://www.dji.com/wpmz/1.0.6'
MEMBERS = ('wpmz/template.kml', 'wpmz/waylines.wpml')


class Refusal(Exception):
    """An actionable planning refusal, presented by argparse."""


def load_defaults():
    return json.loads(DEFAULTS_PATH.read_text())


def curvature_radii(lat):
    a, e2 = 6378137.0, 6.6943799901413165e-3
    q = 1 - e2 * math.sin(math.radians(lat)) ** 2
    return a * (1 - e2) / q ** 1.5, a / math.sqrt(q)


def enu_to_wgs84(lat0, lon0, e, n):
    m, prime = curvature_radii(lat0)
    return lat0 + math.degrees(n / m), lon0 + math.degrees(e / (prime * math.cos(math.radians(lat0))))


def wgs84_to_enu(lat0, lon0, lat, lon):
    m, prime = curvature_radii(lat0)
    return math.radians(lon - lon0) * prime * math.cos(math.radians(lat0)), math.radians(lat - lat0) * m


def ground_distance_m(lat1, lon1, lat2, lon2):
    p, q = math.radians(lat1), math.radians(lat2)
    h = math.sin((q - p) / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def camera_geometry(resolution='50mp', values=None):
    v = values or {k: x['value'] for k, x in load_defaults().items()}
    w_px, h_px = v['resolutions'][resolution]
    sensor_w, focal_equiv = v['equivalent_sensor_width_mm'], v['focal_length_equivalent_mm']
    # Same two lines as capture_plan.make_plan, using equivalent sensor width.
    hfov = 2 * math.atan(sensor_w / (2 * focal_equiv))
    vfov = 2 * math.atan(math.tan(hfov / 2) * h_px / w_px)
    return dict(hfov=hfov, vfov=vfov, width_px=w_px, height_px=h_px, resolution=resolution)


def gsd(altitude, camera=None):
    c = camera or camera_geometry()
    return 2 * altitude * math.tan(c['hfov'] / 2) / c['width_px']


def altitude_for_gsd(target, camera=None):
    c = camera or camera_geometry()
    return target * c['width_px'] / (2 * math.tan(c['hfov'] / 2))


def check_number(value, key, minimum=None, maximum=None, positive=False):
    if not math.isfinite(value) or (positive and value <= 0) or (
        minimum is not None and value < minimum
    ) or (maximum is not None and value > maximum):
        raise Refusal(f'{key}={value}: policy {key} requires finite '
                      f'{"positive " if positive else ""}value in [{minimum}, {maximum}]')


def limit(value, key, v, minimum=False):
    # Compare quantized metres/fractions before policy decisions across libm implementations.
    value, bound = round(value, 9), round(v[key], 9)
    if (value < bound) if minimum else (value > bound):
        raise Refusal(f'{value:.6g} violates {key}={v[key]}')


def validate_coords(lat, lon):
    check_number(lat, 'latitude_limit_deg', -85, 85)
    check_number(lon, 'longitude_limit_deg', -180, 180)


def photo_centre(path):
    from PIL import Image

    try:
        with Image.open(path) as image:
            gps = image.getexif().get_ifd(34853)
        if not gps:
            raise Refusal('GPS IFD missing: from_photo requires valid GPS coordinates')
        coords = []
        for tag, ref, allowed in ((2, 1, ('N', 'S')), (4, 3, ('E', 'W'))):
            direction = gps[ref]
            if isinstance(direction, bytes):
                direction = direction.decode().strip('\x00')
            if direction not in allowed or len(gps[tag]) != 3:
                raise ValueError('invalid GPS reference or DMS')
            d, m, s = [float(x) for x in gps[tag]]
            if not all(math.isfinite(x) for x in (d, m, s)) or d < 0 or not 0 <= m < 60 or not 0 <= s < 60:
                raise ValueError('invalid GPS DMS')
            coords.append((d + m / 60 + s / 3600) * (1 if direction == allowed[0] else -1))
        validate_coords(*coords)
        if coords == [0, 0]:
            raise Refusal('GPS=(0, 0): from_photo requires a non-placeholder position')
        return tuple(coords)
    except (OSError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise Refusal(f'Invalid GPS: {exc}; from_photo requires valid rational coordinates') from exc


def interval_table(photo_spacing, footprint, ground_gsd, v):
    rows = []
    for seconds in v['intervals_s']:
        speed = photo_spacing / seconds
        blur = speed * v['shutter_seconds'] / ground_gsd
        reasons = []
        if not v['min_mission_speed_ms'] <= speed <= v['max_mission_speed_ms']:
            reasons.append(f"speed outside min_mission_speed_ms={v['min_mission_speed_ms']}.."
                           f"max_mission_speed_ms={v['max_mission_speed_ms']}")
        if blur > v['max_motion_blur_px']:
            reasons.append(f"blur exceeds max_motion_blur_px={v['max_motion_blur_px']}")
        rows.append(dict(interval_s=seconds, speed_ms=speed, motion_blur_px=blur,
                         forward_overlap=1 - speed * seconds / footprint, reasons=reasons, chosen=False))
    return rows


def choose_timing(spacing, footprint, ground_gsd, v, interval=None, speed=None):
    table = interval_table(spacing, footprint, ground_gsd, v)
    admissible = [r for r in table if not r['reasons']]
    if not admissible:
        detail = '; '.join(f"{r['interval_s']} s -> {r['speed_ms']:.3f} m/s -> "
                           f"{r['motion_blur_px']:.3f} px: {', '.join(r['reasons'])}" for r in table)
        raise Refusal('No admissible interval: ' + detail)
    chosen = min(admissible, key=lambda r: r['interval_s'])
    seconds = interval if interval is not None else chosen['interval_s']
    velocity = speed if speed is not None else spacing / seconds
    if seconds not in v['intervals_s']:
        raise Refusal(f"interval={seconds}: intervals_s={v['intervals_s']}")
    overlap = 1 - velocity * seconds / footprint
    if overlap < v['min_forward_overlap']:
        raise Refusal(f"Achieved forward overlap {overlap:.2%} ({overlap:.6f}) below "
                      f"min_forward_overlap={v['min_forward_overlap']}; pair that works: "
                      f"--interval {chosen['interval_s']} --speed {chosen['speed_ms']:.6g}")
    limit(velocity, 'min_mission_speed_ms', v, minimum=True)
    limit(velocity, 'max_mission_speed_ms', v)
    blur = velocity * v['shutter_seconds'] / ground_gsd
    limit(blur, 'max_motion_blur_px', v)
    for row in table:
        row['chosen'] = row['interval_s'] == seconds and math.isclose(row['speed_ms'], velocity)
    if not any(row['chosen'] for row in table):
        table.append(dict(interval_s=seconds, speed_ms=velocity, motion_blur_px=blur,
                          forward_overlap=overlap, reasons=[], chosen=True, operator_forced=True))
    return dict(interval_s=seconds, speed_ms=velocity, forward_overlap=overlap,
                motion_blur_px=blur, table=table)


def load_capture_plan():
    spec = importlib.util.spec_from_file_location('drone_capture_plan', Path(__file__).with_name('capture_plan.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def orbit_geometry(radius, size, altitude, subject_height, camera, v):
    # ring_geometry was written for a level camera: supply SLANT clearance, not horizontal clearance.
    return load_capture_plan().ring_geometry(
        radius, math.hypot(radius - size / 2, altitude - subject_height / 2),
        camera['hfov'], v['forward_overlap'], v['orbit_max_step_deg'])


def sample_line(start, end, spacing):
    count = max(1, math.ceil(math.dist(start, end) / spacing))
    return [(start[0] + (end[0] - start[0]) * i / count,
             start[1] + (end[1] - start[1]) * i / count) for i in range(count + 1)]


def grid_points(width, depth, heading, line_spacing, photo_spacing, extension):
    theta = math.radians(heading)
    # Heading is clockwise from north; project the subject rectangle onto flight axes.
    across = abs(width * math.cos(theta)) + abs(depth * math.sin(theta))
    along = abs(width * math.sin(theta)) + abs(depth * math.cos(theta))
    count = max(1, math.ceil(across / line_spacing))
    result = []
    for i in range(count + 1):
        x = -across / 2 + across * i / count
        endpoints = [(x, -along / 2 - extension), (x, along / 2 + extension)]
        if i % 2:
            endpoints.reverse()
        points = sample_line(*endpoints, photo_spacing)
        result.extend((x * math.cos(theta) + y * math.sin(theta),
                       -x * math.sin(theta) + y * math.cos(theta), i) for x, y in points)
    return result


def make_plan(args):
    defaults = load_defaults()
    v = {k: x['value'] for k, x in defaults.items()}
    for name, key in (('takeoff_offset', 'takeoff_offset_m'), ('rth_altitude', 'rth_altitude_m'),
                      ('resolution', 'resolution')):
        if getattr(args, name) is None:
            setattr(args, name, v[key])
    for name in ('subject', 'place', 'authorization'):
        if not getattr(args, name) or not getattr(args, name).strip():
            raise Refusal(f'{name} must be non-empty; {name}_required=true')
    for term in v['prohibited_place_terms']:
        if term.casefold() in args.place.casefold():
            raise Refusal(f'place matches {term!r}: prohibited_place_terms={v["prohibited_place_terms"]}. '
                          'A substring match on operator text is a screening aid and not a legal determination; '
                          'the absence of a match is not permission. No override.')
    for name in ('subject_height', 'radius', 'gsd', 'altitude', 'interval', 'speed', 'rth_altitude'):
        value = getattr(args, name)
        if value is not None:
            check_number(value, name, positive=True)
    for name in ('heading', 'takeoff_offset'):
        check_number(getattr(args, name), name)
    try:
        created = datetime.fromisoformat(args.created.replace('Z', '+00:00'))
        if created.tzinfo is None:
            raise ValueError('timezone required')
    except ValueError as exc:
        raise Refusal(f'created={args.created!r}: created_iso8601 requires a timestamp with timezone') from exc
    if args.from_photo:
        if args.lat is not None or args.lon is not None:
            raise Refusal('centre policy: use lat/lon OR from_photo')
        lat, lon = photo_centre(args.from_photo)
    else:
        if args.lat is None or args.lon is None:
            raise Refusal('centre policy: lat and lon are both required without from_photo')
        lat, lon = args.lat, args.lon
    validate_coords(lat, lon)
    if (args.radius is None) == (args.bbox is None):
        raise Refusal('geometry policy: exactly one positive radius or bbox required')
    if args.bbox:
        try:
            south, west, north, east = map(float, args.bbox.split(','))
        except ValueError as exc:
            raise Refusal(f'bbox={args.bbox}: bbox requires S,W,N,E') from exc
        validate_coords(south, west)
        validate_coords(north, east)
        if south >= north or west >= east:
            raise Refusal(f'bbox={args.bbox}: bbox_order requires S<N and W<E')
        if not south <= lat <= north or not west <= lon <= east:
            raise Refusal(f'centre=({lat}, {lon}): bbox must contain centre')
        e0, n0 = wgs84_to_enu(lat, lon, south, west)
        e1, n1 = wgs84_to_enu(lat, lon, north, east)
        width, depth = 2 * max(abs(e0), abs(e1)), 2 * max(abs(n0), abs(n1))
    else:
        width = depth = 2 * args.radius
    limit(max(width, depth), 'max_subject_span_m', v)
    c = camera_geometry(args.resolution, v)
    # Without an explicit ground sample, resolve one from the subject itself:
    # the subject's longest horizontal dimension should span at least
    # subject_pixels_across pixels in one nadir frame. Falling back to
    # max_gsd_m instead would fly every subject at the legal ceiling, which
    # puts a six-metre building 120 m away.
    # The subject's own height is the scale, not the coverage envelope: a
    # cannon inside a 30 m radius is still a 3 m cannon.
    subject_gsd = args.subject_height / v['subject_pixels_across']
    target = args.gsd if args.gsd is not None else min(subject_gsd, v['gsd_m'])
    limit(target, 'max_gsd_m', v)
    ceiling_gsd = altitude_for_gsd(target, c)
    ceiling = min(ceiling_gsd, v['max_altitude_agl_m'])
    floor = args.subject_height + v['min_obstacle_clearance_m']
    if floor > ceiling:
        message = (f'floor={floor:.3f} (subject_height={args.subject_height} + '
                   f'min_obstacle_clearance_m={v["min_obstacle_clearance_m"]}) exceeds ceiling={ceiling:.3f} '
                   f'(gsd_m={target}, max_altitude_agl_m={v["max_altitude_agl_m"]})')
        if args.gsd is None and args.altitude is None:
            message += ('; the subject is small enough that resolving it needs the aircraft closer than '
                        'the obstacle clearance allows. Capture it on foot with tools/capture_plan.py, '
                        'or state --gsd or --altitude to overrule the subject-derived ground sample')
        raise Refusal(message)
    altitude = args.altitude if args.altitude is not None else ceiling
    limit(altitude, 'max_altitude_agl_m', v)
    if altitude < floor or altitude > ceiling:
        raise Refusal(f'altitude={altitude}: floor={floor:.3f} from min_obstacle_clearance_m='
                      f'{v["min_obstacle_clearance_m"]}; ceiling={ceiling:.3f} from gsd_m={target}')
    if args.altitude is not None:
        binding = 'operator_altitude'
    elif ceiling_gsd >= v['max_altitude_agl_m']:
        binding = 'max_altitude_agl_m'
    elif args.gsd is None and subject_gsd < v['gsd_m']:
        binding = 'subject_pixels_across'
    else:
        binding = 'gsd_m'
    ground_gsd = gsd(altitude, c)
    limit(ground_gsd, 'max_gsd_m', v)
    pitch = v['oblique_pitch_below_horizon_deg']
    oblique_gsd = gsd(altitude / math.sin(math.radians(pitch)), c)
    if args.mode == 'double-grid':
        limit(oblique_gsd, 'max_gsd_m', v)
    rth = args.rth_altitude
    if rth + args.takeoff_offset < floor:
        raise Refusal(f'rth_altitude={rth} + takeoff_offset={args.takeoff_offset} below floor={floor}: '
                      f'min_obstacle_clearance_m={v["min_obstacle_clearance_m"]}')
    limit(rth + args.takeoff_offset, 'max_altitude_agl_m', v)
    across = 2 * altitude * math.tan(c['hfov'] / 2)
    along = 2 * altitude * math.tan(c['vfov'] / 2)
    line_spacing = (1 - v['side_overlap']) * across
    photo_spacing = (1 - v['forward_overlap']) * along
    timing = choose_timing(photo_spacing, along, ground_gsd, v, args.interval, args.speed)
    extension = photo_spacing / 2 + v['turn_radius_m']
    passes = []
    ring = None

    def add_pass(name, purpose, points, height, gimbal, heading):
        if height < floor or height > ceiling:
            raise Refusal(f'pass altitude={height}: min_obstacle_clearance_m={v["min_obstacle_clearance_m"]} '
                          f'requires floor={floor}; max_altitude_agl_m/gsd_m ceiling={ceiling}')
        execute = height - args.takeoff_offset
        check_number(execute, 'execute_height_above_takeoff_m', positive=True)
        height_gsd = gsd(height, c)
        limit(height_gsd, 'max_gsd_m', v)
        local_timing = choose_timing((1 - v['forward_overlap']) * 2 * height * math.tan(c['vfov'] / 2),
                                    2 * height * math.tan(c['vfov'] / 2), height_gsd, v,
                                    timing['interval_s'], args.speed)
        waypoints = []
        for e, n, line in points:
            plat, plon = enu_to_wgs84(lat, lon, e, n)
            validate_coords(plat, plon)
            waypoints.append(dict(lat=round(plat, 7), lon=round(plon, 7),
                                  execute_height_m=round(execute, 2), speed_ms=round(local_timing['speed_ms'], 2),
                                  heading_mode=v['heading_mode'],
                                  heading_deg=round((math.degrees(math.atan2(-e, -n)) if heading is None
                                                     else heading) % 360, 1),
                                  gimbal_pitch_deg=round(v['gimbal_pitch_sign'] * gimbal, 1),
                                  turn_mode=v['turn_mode'], damping_m=v['turn_radius_m'],
                                  hover_seconds=v['hover_seconds'],
                                  action='takePhoto' if args.photo_mode == 'waypoint' else None,
                                  pass_name=name, purpose=purpose, line=line))
        passes.append(dict(name=name, purpose=purpose, waypoints=waypoints, altitude_agl_m=height))

    if args.mode == 'double-grid':
        for name, heading in (('grid-A', args.heading), ('grid-B', args.heading + 90)):
            add_pass(name, 'nadir coverage', grid_points(width, depth, heading, line_spacing,
                                                       photo_spacing, extension), altitude, 90, heading)
        for i, (start, end, heading) in enumerate((
            ((-width / 2 - extension, -depth / 2 - extension),
             (width / 2 + extension, -depth / 2 - extension), 0),
            ((width / 2 + extension, -depth / 2 - extension),
             (width / 2 + extension, depth / 2 + extension), 270),
            ((width / 2 + extension, depth / 2 + extension),
             (-width / 2 - extension, depth / 2 + extension), 180),
            ((-width / 2 - extension, depth / 2 + extension),
             (-width / 2 - extension, -depth / 2 - extension), 90),
        )):
            add_pass(f'oblique-{i + 1}', 'side oblique coverage',
                     [(e, n, 0) for e, n in sample_line(start, end, photo_spacing)], altitude, pitch, heading)
    else:
        size = math.hypot(width, depth)
        radius = size / 2 + v['orbit_clearance_m']
        ring = orbit_geometry(radius, size, altitude, args.subject_height, c, v)
        orbit_pitch = math.degrees(math.atan2(altitude - args.subject_height / 2, radius))
        oblique_gsd = gsd(math.hypot(radius, altitude - args.subject_height / 2), c)
        limit(oblique_gsd, 'max_gsd_m', v)
        points = [(radius * math.sin(math.tau * i / ring['stations']),
                   radius * math.cos(math.tau * i / ring['stations']), 0) for i in range(ring['stations'] + 1)]
        add_pass('orbit', 'convergent subject ring', points, altitude, orbit_pitch, None)
        h = v['nadir_grid_altitude_m']
        scale = h / altitude
        add_pass('nadir-grid', 'roof coverage', grid_points(width, depth, args.heading,
                                                         line_spacing * scale, photo_spacing * scale,
                                                         photo_spacing * scale / 2 + v['turn_radius_m']),
                 h, 90, args.heading)
    waypoints = [w for p in passes for w in p['waypoints']]
    for i, w in enumerate(waypoints):
        w['index'] = i
    farthest = max(ground_distance_m(lat, lon, w['lat'], w['lon']) for w in waypoints)
    limit(farthest, 'max_distance_from_home_m', v)

    def duration(group):
        points = [w for p in group for w in p['waypoints']]
        distance = sum(ground_distance_m(a['lat'], a['lon'], b['lat'], b['lon'])
                       for a, b in pairwise(points))
        travel = sum(ground_distance_m(a['lat'], a['lon'], b['lat'], b['lon']) / min(a['speed_ms'], b['speed_ms'])
                     for a, b in pairwise(points))
        turns = sum(a['line'] != b['line'] or a['pass_name'] != b['pass_name']
                    for a, b in pairwise(points))
        home_distance = sum(ground_distance_m(lat, lon, w['lat'], w['lon']) for w in (points[0], points[-1]))
        climb = 2 * max(w['execute_height_m'] for w in points) / v['climb_speed_ms']
        total = (travel + home_distance / timing['speed_ms'] + turns * v['turn_seconds'] + climb
                 + v['transit_seconds'] + v['rth_seconds'] + sum(w['hover_seconds'] for w in points))
        return dict(path_length_m=distance, home_transit_m=home_distance, turns=turns, climb_seconds=climb,
                    transit_allowance_seconds=v['transit_seconds'], rth_allowance_seconds=v['rth_seconds'],
                    total_seconds=total)

    budget = v['usable_battery_minutes'] * 60
    whole = duration(passes)
    groups = [passes]
    if whole['total_seconds'] > budget:
        if not args.allow_multi_battery:
            raise Refusal(f"duration={whole['total_seconds'] / 60:.3f} min exceeds "
                          f"usable_battery_minutes={v['usable_battery_minutes']}; use --allow-multi-battery")
        groups = []
        current = []
        for p in passes:
            if duration([p])['total_seconds'] > budget:
                raise Refusal(f"pass {p['name']} duration={duration([p])['total_seconds'] / 60:.3f} min exceeds "
                              f"usable_battery_minutes={v['usable_battery_minutes']}; cannot split inside a pass")
            if current and duration([*current, p])['total_seconds'] > budget:
                groups.append(current)
                current = []
            current.append(p)
        if current:
            groups.append(current)
    legs = [dict(passes=[p['name'] for p in group], **duration(group)) for group in groups]
    total = sum(leg['total_seconds'] for leg in legs)
    warnings = ['NO terrain model; no terrain following.',
                f'Takeoff offset {args.takeoff_offset:g} m; default 0 assumes level home and subject ground.',
                'GNSS only, no RTK.', 'The tool never talks to the aircraft.',
                'Schema evidence does not certify flight compatibility; ground-check the actual controller.',
                'Absence of a prohibited-place substring match is not permission.']
    if args.photo_mode == 'interval':
        warnings.append('Operator starts the timed interval by hand; turn frames are redundant.')
    else:
        warnings.append('Waypoint photo action support in DJI Fly remains unverified; '
                        'frame estimate uses timed cadence.')
    checklist = ['Verify site/airspace authorization, wind, visibility and obstacle clearance on the ground.',
                 'Survey takeoff elevation, home location, RTH route and terrain; verify all execute heights.',
                 'Inspect import in DJI Fly; verify aircraft, payload, headings, gimbal sign and action support.',
                 'Set camera settings and test the actual interval; check sharpness and exposure.',
                 'Start timer by hand for interval mode; stop it on landing; change battery only at pass boundaries.',
                 'Capture scale, identifying signage and original EXIF; inspect registration before leaving.',
                 'Measure payload against 256 MiB using runbook stage 6; reduce views/resolution if needed.']
    margins = dict(altitude_ceiling_m=ceiling - altitude, obstacle_clearance_m=altitude - floor,
                   nadir_gsd_m=v['max_gsd_m'] - ground_gsd, oblique_gsd_m=v['max_gsd_m'] - oblique_gsd,
                   forward_overlap=timing['forward_overlap'] - v['min_forward_overlap'],
                   motion_blur_px=v['max_motion_blur_px'] - timing['motion_blur_px'],
                   home_distance_m=v['max_distance_from_home_m'] - farthest,
                   rth_clearance_m=rth + args.takeoff_offset - floor,
                   battery_seconds=min(budget - leg['total_seconds'] for leg in legs))
    inputs = {k: (str(x) if isinstance(x, Path) else x) for k, x in vars(args).items()
              if k not in ('out', 'force', 'json', 'from_export', 'write_skeleton', 'diff', 'against')}
    inputs['from_photo'] = args.from_photo.name if args.from_photo else None
    inputs['skeleton'] = args.skeleton.name if args.skeleton else None
    return dict(inputs=inputs, created=created.isoformat(), centre=dict(lat=lat, lon=lon),
                camera=c, altitude=dict(agl_m=altitude, floor_m=floor, ceiling_m=ceiling,
                                       binding_constraint=binding, gsd_m=ground_gsd, oblique_gsd_m=oblique_gsd),
                spacing=dict(line_m=line_spacing, photo_m=photo_spacing, across_footprint_m=across,
                             along_footprint_m=along, extension_m=extension), timing=timing,
                waypoints=waypoints, ring=ring, legs=legs, duration=dict(total_seconds=total),
                expected_frames=math.ceil(total / timing['interval_s']),
                payload=dict(limit_mib=256, note='Frame count is not compressed bytes; measure using runbook stage 6.'),
                arithmetic='GSD=2*h*tan(HFOV/2)/width; line=(1-side_overlap)*HFOV footprint; '
                'photo=(1-forward_overlap)*VFOV footprint; speed=photo/interval; blur=speed*shutter/GSD; '
                'orbit ring_geometry receives SLANT hypot(radius-size/2, altitude-subject_height/2); '
                'duration=path/speed + turns + climb + transit + RTH; frames=ceil(total_seconds/interval).',
                margins=margins, warnings=warnings, checklist=checklist, defaults=defaults,
                operator_defaults={k: x for k, x in defaults.items() if x['source'] == 'operator default'})


def render_markdown(plan):
    lines = ([] if plan['schema_verified'] else ['SCHEMA UNVERIFIED', ''])
    lines += [f"# Mission: {plan['inputs']['subject']}", '', '## Permission', '',
              f"Place: {plan['inputs']['place']}", f"Authorization: {plan['inputs']['authorization']}", '',
              '## Arithmetic and settings', '', plan['arithmetic'], '',
              f"Altitude {plan['altitude']['agl_m']:.3f} m AGL; binding: "
              f"{plan['altitude']['binding_constraint']}; GSD {plan['altitude']['gsd_m']:.8f} m.",
              f"Line spacing {plan['spacing']['line_m']:.3f} m; photo spacing {plan['spacing']['photo_m']:.3f} m.",
              f"Interval {plan['timing']['interval_s']} s; speed {plan['timing']['speed_ms']:.3f} m/s; "
              f"forward overlap {plan['timing']['forward_overlap']:.2%}; "
              f"blur {plan['timing']['motion_blur_px']:.4f} px.",
              plan['defaults']['camera_settings']['value'],
              f"Resolution: {plan['camera']['resolution']} ({plan['camera']['width_px']} x "
              f"{plan['camera']['height_px']}); photo mode: {plan['inputs']['photo_mode']}.",
              f"Duration {plan['duration']['total_seconds'] / 60:.2f} min; "
              f"{plan['expected_frames']} expected frames; {len(plan['legs'])} battery leg(s).",
              'Payload limit: 256 MiB; frame count does not establish byte size. See runbook stage 6.', '',
              '## Interval candidates', '', '| seconds | m/s | blur px | decision |', '|---|---|---|---|']
    for row in plan['timing']['table']:
        decision = 'chosen' if row['chosen'] else (', '.join(row['reasons']) or 'admissible')
        lines.append(f"| {row['interval_s']} | {row['speed_ms']:.3f} | {row['motion_blur_px']:.4f} | {decision} |")
    lines += ['', '## Refusals that did not trigger: remaining margins', '']
    lines += [f'- {key}: {value:.6f}' for key, value in plan['margins'].items()]
    lines += ['- Inputs finite and in range; authorization present; place screening passed.',
              '- Output overwrite and schema checks are recorded separately; no flight certification implied.', '',
              '## Warnings', '', *('- ' + w for w in plan['warnings']), '', '## Ground checklist', '',
              *('- [ ] ' + c for c in plan['checklist']), '', '## Defaults and sources', '']
    lines += [f"- `{k}` = {json.dumps(x['value'])}; {x['source']}: {x['rationale']}"
              for k, x in plan['defaults'].items()]
    return '\n'.join(lines) + '\n'


def local(tag):
    return tag.rsplit('}', 1)[-1]


# Tokenized fields are the only values the writer is permitted to change.
TOKENS = {'coordinates': 'coordinates', 'index': 'index', 'executeHeight': 'height', 'height': 'height',
          'ellipsoidHeight': 'height', 'waypointSpeed': 'speed', 'waypointHeadingAngle': 'heading',
          'gimbalPitchAngle': 'pitch', 'waypointTurnDampingDist': 'damping',
          'actionGroupId': 'index', 'actionGroupStartIndex': 'index', 'actionGroupEndIndex': 'index',
          'actionId': 'index', 'hoverTime': 'hover', 'createTime': 'created', 'updateTime': 'created',
          'globalHeight': 'height', 'globalEllipsoidHeight': 'height', 'autoFlightSpeed': 'speed',
          'globalRTHHeight': 'rth'}


def tree_data(node):
    return dict(tag=node.tag, attrs=dict(node.attrib), text=(node.text or '').strip(),
                children=[tree_data(child) for child in node])


def data_tree(data):
    node = ET.Element(data['tag'], data.get('attrs', {}))
    node.text = data.get('text') or None
    node.extend(data_tree(child) for child in data.get('children', []))
    return node


def read_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise Refusal('archive policy: duplicate members')
            if any(info.file_size > 8 * 1024 * 1024 for info in archive.infolist()):
                raise Refusal('archive policy: member exceeds 8 MiB')
            roots = {name: ET.fromstring(archive.read(name)) for name in names if name.endswith(('.kml', '.wpml'))}
            if not all(name in roots for name in MEMBERS):
                raise Refusal(f'archive policy: required members={MEMBERS}')
            return names, roots
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise Refusal(f'archive policy: invalid KMZ: {exc}') from exc


def structure(path):
    names, roots = read_archive(path)
    paths = set(names)

    def walk(node, prefix):
        here = prefix + '/' + node.tag
        paths.add(here)
        for attr in node.attrib:
            paths.add(here + '/@' + attr)
        for child in node:
            walk(child, here)
    for name, root in roots.items():
        walk(root, name)
    return paths


def diff_archives(export, ours):
    a, b = structure(export), structure(ours)
    for title, paths in (('Paths in both', a & b), ('Only in export', a - b), ('Only in ours', b - a)):
        print(title + ':')
        for path in sorted(paths):
            print('  ' + path)
    return int(bool(a - b))


def normalize_skeleton(roots, source):
    documents = {}
    namespaces = {}
    for name in MEMBERS:
        root = copy.deepcopy(roots[name])
        placemarks = [n for n in root.iter() if local(n.tag) == 'Placemark']
        if not placemarks:
            raise Refusal(f'skeleton policy: no Placemark in {name}')
        for parent in root.iter():
            found = [child for child in parent if local(child.tag) == 'Placemark']
            for child in found[1:]:
                parent.remove(child)
        if sum(local(n.tag) == 'Placemark' for n in root.iter()) != 1:
            raise Refusal('skeleton policy: exactly one waypoint Folder supported')
        for node in root.iter():
            tag = local(node.tag)
            if node.tag.startswith('{'):
                uri = node.tag[1:].split('}')[0]
                if uri not in namespaces.values():
                    prefix = '' if uri == KML else ('wpml' if 'wpml' not in namespaces else f'ext{len(namespaces)}')
                    namespaces[prefix] = uri
            if tag in TOKENS:
                node.text = '{' + TOKENS[tag] + '}'
            elif tag in ('name', 'description', 'author'):
                node.text = 'Subject mission'
            elif tag != 'payloadPositionIndex' and any(
                word in tag.lower() for word in ('coordinate', 'location', 'latitude', 'longitude', 'position')
            ):
                raise Refusal(f'skeleton policy: unsupported geometry field {tag}; inspect the export manually')
            text = node.text or ''
            if re.search(r'(?:/[^ ]+/|[+-]?\d+\.\d+\s*,\s*[+-]?\d+\.\d+)', text):
                raise Refusal(f'skeleton policy: potential path/coordinate text in {tag}; manual review required')
            if node.attrib:
                raise Refusal(f'skeleton policy: attributes on {tag} require manual schema review')
        documents[name] = tree_data(root)
    return dict(source=source, schema_verified=False, namespaces=namespaces, documents=documents)


def synthetic_skeleton():
    roots = {}
    for name in MEMBERS:
        root = ET.Element(f'{{{KML}}}kml')
        doc = ET.SubElement(root, f'{{{KML}}}Document')

        def w(parent, name, value=None):
            node = ET.SubElement(parent, f'{{{WPML}}}{name}')
            node.text = value
            return node
        w(doc, 'createTime', '0')
        config = w(doc, 'missionConfig')
        w(config, 'flyToWaylineMode', 'safely')
        w(config, 'finishAction', 'goHome')
        w(config, 'exitOnRCLost', 'executeLostAction')
        w(config, 'executeRCLostAction', 'goBack')
        w(config, 'globalRTHHeight', '120')
        drone = w(config, 'droneInfo')
        w(drone, 'droneEnumValue', '0')
        payload = w(config, 'payloadInfo')
        w(payload, 'payloadEnumValue', '0')
        w(payload, 'payloadPositionIndex', '0')
        folder = ET.SubElement(doc, f'{{{KML}}}Folder')
        w(folder, 'templateId', '0')
        w(folder, 'executeHeightMode', 'relativeToStartPoint')
        pm = ET.SubElement(folder, f'{{{KML}}}Placemark')
        point = ET.SubElement(pm, f'{{{KML}}}Point')
        ET.SubElement(point, f'{{{KML}}}coordinates').text = '0,0'
        for key, val in (('index', '0'), ('executeHeight', '120'), ('waypointSpeed', '5.4'),
                         ('gimbalPitchAngle', '-90')):
            w(pm, key, val)
        head = w(pm, 'waypointHeadingParam')
        w(head, 'waypointHeadingMode', 'fixed')
        w(head, 'waypointHeadingAngle', '0')
        turn = w(pm, 'waypointTurnParam')
        w(turn, 'waypointTurnMode', 'toPointAndStopWithDiscontinuityCurvature')
        w(turn, 'waypointTurnDampingDist', '5')
        group = w(pm, 'actionGroup')
        for key in ('actionGroupId', 'actionGroupStartIndex', 'actionGroupEndIndex'):
            w(group, key, '0')
        w(group, 'actionGroupMode', 'sequence')
        trigger = w(group, 'actionTrigger')
        w(trigger, 'actionTriggerType', 'reachPoint')
        action = w(group, 'action')
        w(action, 'actionId', '0')
        w(action, 'actionActuatorFunc', 'takePhoto')
        params = w(action, 'actionActuatorFuncParam')
        w(params, 'payloadPositionIndex', '0')
        roots[name] = root
    return normalize_skeleton(roots, dict(dji_fly_version='UNVERIFIED', aircraft='Air 3S / RC 2; synthetic'))


def load_skeleton(path):
    try:
        skeleton = json.loads(path.read_text())
        if not all(skeleton['source'].get(k) for k in ('dji_fly_version', 'aircraft')):
            raise ValueError('source must name DJI Fly version and aircraft')
        if set(skeleton['documents']) != set(MEMBERS):
            raise ValueError('documents must contain exactly the two required members')
        for data in skeleton['documents'].values():
            root = data_tree(data)
            if sum(local(n.tag) == 'Placemark' for n in root.iter()) != 1:
                raise ValueError('one Placemark template required per document')
        return skeleton
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refusal(f'skeleton policy: invalid skeleton: {exc}') from exc



def apply_skeleton(plan, skeleton):
    """Require explicit geometry slots and expose the template enums in the plan."""
    expected = {'coordinates', 'index', 'height', 'speed', 'heading', 'pitch'}
    enums = None
    for member in MEMBERS:
        root = data_tree(skeleton['documents'][member])
        placemark = next(n for n in root.iter() if local(n.tag) == 'Placemark')
        tokens = {(n.text or '')[1:-1] for n in placemark.iter()
                  if (n.text or '').startswith('{') and (n.text or '').endswith('}')}
        missing = expected - tokens
        if missing:
            raise Refusal(f'skeleton policy: {member} missing geometry slots {sorted(missing)}')
        current = {}
        for tag, key in (('waypointHeadingMode', 'heading_mode'), ('waypointTurnMode', 'turn_mode')):
            nodes = [n for n in placemark.iter() if local(n.tag) == tag]
            if len(nodes) != 1 or not nodes[0].text:
                raise Refusal(f'skeleton policy: {member} requires one {tag} enum')
            current[key] = nodes[0].text
        if enums is not None and current != enums:
            raise Refusal('skeleton policy: template and waylines heading/turn enums disagree')
        enums = current
    for waypoint in plan['waypoints']:
        waypoint.update(enums)


def write_xml(plan, skeleton, member, waypoints):
    for prefix, uri in skeleton['namespaces'].items():
        ET.register_namespace(prefix, uri)
    root = data_tree(skeleton['documents'][member])
    parent = next(n for n in root.iter() if any(local(c.tag) == 'Placemark' for c in n))
    template = next(n for n in parent if local(n.tag) == 'Placemark')
    position = list(parent).index(template)
    parent.remove(template)
    stamp = str(int(datetime.fromisoformat(plan['created']).timestamp() * 1000))

    def fill(node, waypoint, index):
        values = dict(coordinates=f"{waypoint['lon']:.7f},{waypoint['lat']:.7f}", index=str(index),
                      height=f"{waypoint['execute_height_m']:.2f}", speed=f"{waypoint['speed_ms']:.2f}",
                      heading=f"{waypoint['heading_deg']:.1f}", pitch=f"{waypoint['gimbal_pitch_deg']:.1f}",
                      damping=f"{waypoint['damping_m']:.2f}", hover=f"{waypoint['hover_seconds']:.2f}",
                      created=stamp, rth=f"{plan['inputs']['rth_altitude']:.2f}")
        for child in node.iter():
            if child.text and child.text.startswith('{') and child.text.endswith('}'):
                token = child.text[1:-1]
                if token not in values:
                    raise Refusal(f'skeleton policy: unsupported token {token}')
                child.text = values[token]
    fill(root, waypoints[0], 0)
    for index, waypoint in enumerate(waypoints):
        node = copy.deepcopy(template)
        groups = [c for c in node if local(c.tag) == 'actionGroup']
        if waypoint['action'] is None:
            for group in groups:
                node.remove(group)
        elif not any(local(c.tag) == 'actionActuatorFunc' and c.text == waypoint['action'] for c in node.iter()):
            raise Refusal('skeleton policy: waypoint takePhoto action missing; export a mission with this action')
        fill(node, waypoint, index)
        parent.insert(position + index, node)
    ET.indent(root, '  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def kmz_bytes(plan, skeleton, waypoints):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for member in MEMBERS:
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, write_xml(plan, skeleton, member, waypoints))
    return buffer.getvalue()


def parser_for_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('subject', 'place', 'authorization', 'bbox', 'created'):
        parser.add_argument('--' + name)
    for name in ('lat', 'lon', 'radius', 'subject-height', 'gsd', 'altitude', 'interval', 'speed'):
        parser.add_argument('--' + name, type=float)
    parser.add_argument('--heading', type=float, default=0)
    parser.add_argument('--takeoff-offset', type=float, help='takeoff elevation minus subject ground; default 0 m')
    parser.add_argument('--rth-altitude', type=float, help='RTH height relative to takeoff; default from policy')
    parser.add_argument('--mode', choices=('double-grid', 'orbit'), default='double-grid')
    parser.add_argument('--resolution', choices=('50mp', '12mp'), help='default from drone_defaults.json')
    parser.add_argument('--photo-mode', choices=('interval', 'waypoint'), default='interval')
    for name in ('from-photo', 'skeleton', 'out', 'from-export', 'diff', 'against'):
        parser.add_argument('--' + name, type=Path)
    for name in ('allow-multi-battery', 'unverified-schema', 'force', 'json', 'write-skeleton'):
        parser.add_argument('--' + name, action='store_true')
    return parser


def main(argv=None):
    parser = parser_for_cli()
    args = parser.parse_args(argv)
    try:
        if args.from_export and args.diff:
            raise Refusal('CLI policy: from-export and diff are mutually exclusive')
        if args.diff:
            if not args.against:
                raise Refusal('CLI policy: --diff requires --against')
            return diff_archives(args.diff, args.against)
        if args.against or (args.write_skeleton and not args.from_export):
            raise Refusal('CLI policy: --against requires --diff; --write-skeleton requires --from-export')
        if args.from_export:
            for path in sorted(structure(args.from_export)):
                print(path)
            if args.write_skeleton:
                names, roots = read_archive(args.from_export)
                if set(names) != set(MEMBERS):
                    raise Refusal('skeleton policy: extra resource members require manual review; '
                                  'exactly two supported')
                if SKELETON_PATH.exists() and not args.force:
                    raise Refusal('skeleton output exists: force=false; use --force')
                source = dict(dji_fly_version=input('DJI Fly version: ').strip(),
                              aircraft=input('Aircraft / controller: ').strip())
                if not all(source.values()) or any(re.search(r'[/\\]|\d+\.\d+\.\d+\.\d+', x) for x in source.values()):
                    raise Refusal('source policy: nonempty version and aircraft labels, no paths or IP addresses')
                skeleton = normalize_skeleton(roots, source)
                SKELETON_PATH.write_text(json.dumps(skeleton, indent=2, allow_nan=False) + '\n')
                print('Saved tools/dji_air3s_skeleton.json; compatibility remains unverified.')
            return 0
        for name in ('subject', 'place', 'authorization', 'subject_height', 'out'):
            if getattr(args, name) is None:
                raise Refusal(f'--{name.replace("_", "-")} is required')
        if args.created is None:
            args.created = datetime.now(timezone.utc).isoformat()
        plan = make_plan(args)
        path = args.skeleton or SKELETON_PATH
        skeleton = load_skeleton(path) if path.exists() else None
        if args.skeleton and skeleton is None:
            raise Refusal(f'skeleton policy: selected skeleton {args.skeleton.name} does not exist')
        if skeleton is None and args.unverified_schema:
            skeleton = synthetic_skeleton()
        plan['schema_verified'] = bool(skeleton and skeleton.get('schema_verified', False))
        plan['schema_source'] = skeleton['source'] if skeleton else None
        outputs = {Path(str(args.out) + '.json'): None, Path(str(args.out) + '.md'): None}
        if skeleton:
            apply_skeleton(plan, skeleton)
            for i, leg in enumerate(plan['legs'], 1):
                suffix = '.kmz' if len(plan['legs']) == 1 else f'-leg-{i:02d}.kmz'
                points = [w for w in plan['waypoints'] if w['pass_name'] in leg['passes']]
                outputs[Path(str(args.out) + suffix)] = kmz_bytes(plan, skeleton, points)
        existing = [p for p in args.out.parent.glob(args.out.name + '*.kmz')
                    if re.fullmatch(re.escape(args.out.name) + r'(?:-leg-\d+)?\.kmz', p.name)]
        for path in [args.out, *outputs, *existing]:
            if path.exists() and not args.force:
                raise Refusal(f'output {path.name} exists: force=false; use --force')
        encoded = json.dumps(plan, indent=2, allow_nan=False) + '\n'
        outputs[Path(str(args.out) + '.json')] = encoded.encode()
        outputs[Path(str(args.out) + '.md')] = render_markdown(plan).encode()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        for path, content in outputs.items():
            path.write_bytes(content)
        # Remove stale leg files only when explicitly replacing this exact mission family.
        if args.force:
            for path in existing:
                if path not in outputs and re.fullmatch(re.escape(args.out.name) + r'(?:-leg-\d+)?\.kmz', path.name):
                    path.unlink()
        stream = sys.stderr if args.json else sys.stdout
        if not plan['schema_verified']:
            print('SCHEMA UNVERIFIED', file=stream)
        for warning in plan['warnings']:
            print('WARNING: ' + warning, file=stream)
        if args.json:
            print(encoded, end='')
        else:
            print(render_markdown(plan), end='')
        if skeleton is None:
            raise Refusal('missing skeleton: KMZ not written; JSON plan and operator card written. '
                          'Export a mission by hand from DJI Fly on RC 2 / Air 3S, then run '
                          '--from-export EXPORT.kmz --write-skeleton; record DJI Fly version and aircraft. '
                          'Or explicitly use --unverified-schema. Missing namespace, element order, mission config, '
                          'aircraft/payload enums, height reference, Placemark/action template '
                          'and resource requirements.')
        return 0
    except (Refusal, OSError, ValueError, ET.ParseError, EOFError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
