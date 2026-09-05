#!/usr/bin/env python3
"""CPU multi-view pruning in the original COLMAP/cloud frame.

Prior art: Clean-GS, arXiv:2601.00913 (https://arxiv.org/abs/2601.00913).
Independent implementation from repository primitives and standard geometry;
no Clean-GS code was consulted. In-mask coverage and held-out recall are the
acceptance signals; the splat count and the isolation statistics are not.
Coarse depth cells bias toward abstention and keeping uncertain splats.
Review angles flip file (x, -y, -z), then align scene up to +Y; their d is
--sheet-distance, not derived from the camera's distance.
"""

import argparse
import contextlib
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import zipfile
from types import SimpleNamespace


def shared():
    if __package__:
        from .check_deliverable import ply_header, read_sog
        from .clean_export import footprint_score
        from .scene_up import camera_focus, axis_candidates
    else:
        from check_deliverable import ply_header, read_sog
        from clean_export import footprint_score
        from scene_up import camera_focus, axis_candidates
    return ply_header, read_sog, footprint_score, camera_focus, axis_candidates


def load_defaults(path=None):
    return json.loads((Path(path) if path else Path(__file__).with_name('prune_defaults.json')).read_text())


def settings(**overrides):
    values = {k: v['value'] for k, v in load_defaults().items()}
    values.update(view_select='spread', edge='abstain', unjudged='keep', outlier_weight='count',
                  no_depth_test=False, no_outlier=False)
    values.update(overrides)
    return SimpleNamespace(**values)


def project(R, t, camera, pos, near=0):
    """Return pixel coordinates, camera depth, and positive-depth in-frame mask."""
    import numpy as np

    model = getattr(camera.model, 'name', str(camera.model))
    if model not in {'SIMPLE_PINHOLE', 'PINHOLE', 'SIMPLE_RADIAL', 'RADIAL', 'OPENCV'}:
        raise ValueError(f'unsupported camera model {model}; solve with SIMPLE_RADIAL or PINHOLE')
    p = np.asarray(camera.params)
    X = pos @ np.asarray(R).T + t
    z = X[:, 2]
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        u, v = X[:, 0] / z, X[:, 1] / z
        r2 = u * u + v * v
        if model in {'SIMPLE_PINHOLE', 'SIMPLE_RADIAL', 'RADIAL'}:
            fx = fy = p[0]
            cx, cy = p[1:3]
            radial = 1
            if model != 'SIMPLE_PINHOLE':
                radial = 1 + p[3] * r2
            if model == 'RADIAL':
                radial = radial + p[4] * r2 * r2
            u, v = u * radial, v * radial
        else:
            fx, fy, cx, cy = p[:4]
            if model == 'OPENCV':
                k1, k2, p1, p2 = p[4:8]
                radial = 1 + k1 * r2 + k2 * r2 * r2
                u, v = (u * radial + 2 * p1 * u * v + p2 * (r2 + 2 * u * u),
                        v * radial + p1 * (r2 + 2 * v * v) + 2 * p2 * u * v)
        xy = np.column_stack((fx * u + cx, fy * v + cy))
    valid = ((z > near) & np.isfinite(xy).all(1) & (xy[:, 0] >= 0) & (xy[:, 0] < camera.width)
             & (xy[:, 1] >= 0) & (xy[:, 1] < camera.height))
    return xy, z, valid


def mask_grid(mask, camera, tolerance):
    h, w = mask.shape
    sx, sy = w / camera.width, h / camera.height
    if abs(sx - sy) > tolerance:
        raise ValueError(f'mask grid {w}x{h} differs from camera {camera.width}x{camera.height}; '
                         'regenerate masks on the solved raw grid or rescale cameras uniformly')
    return sx, sy


def mask_states(mask, scale, dilate):
    """0 outside, 1 core, 2 uncertain edge; partial border cells abstain."""
    import numpy as np
    from PIL import Image, ImageFilter

    h, w = mask.shape
    bh, bw = math.ceil(h / scale), math.ceil(w / scale)
    padded = np.pad(mask.astype(bool), ((0, bh * scale - h), (0, bw * scale - w)))
    b = padded.reshape(bh, scale, bw, scale)
    core, any_ = b.all(axis=(1, 3)), b.any(axis=(1, 3))
    if dilate:
        core = np.asarray(Image.fromarray(core.astype('uint8') * 255).filter(
            ImageFilter.MinFilter(2 * dilate + 1))) > 0
        any_ = np.asarray(Image.fromarray(any_.astype('uint8') * 255).filter(
            ImageFilter.MaxFilter(2 * dilate + 1))) > 0
    return np.where(core, 1, np.where(any_, 2, 0)).astype('uint8')


def minimum_fill(buffer, passes):
    import numpy as np

    h, w = buffer.shape
    for _ in range(passes):
        padded = np.pad(buffer, 1, constant_values=np.inf)
        buffer = np.minimum.reduce([padded[y:y + h, x:x + w] for y in range(3) for x in range(3)])
    return buffer


def depth_buffer(xy, z, in_frame, alpha, width, height, scale, alpha_min, fill):
    import numpy as np

    bw, bh = math.ceil(width / scale), math.ceil(height / scale)
    eligible = in_frame & (alpha >= alpha_min)
    px = np.floor(xy[eligible, 0] / scale).astype('int64')
    py = np.floor(xy[eligible, 1] / scale).astype('int64')
    Z = np.full(bw * bh, np.inf, dtype='float32')
    np.minimum.at(Z, py * bw + px, z[eligible])
    return minimum_fill(Z.reshape(bh, bw), fill)


def view_mask(view):
    import numpy as np
    from PIL import Image

    if hasattr(view, 'mask'):
        return view.mask
    with Image.open(view.path) as image:
        return np.asarray(image.convert('L')) > 0


def view_votes(pos, scale, alpha, view, extent, cfg):
    import numpy as np

    xy, z, frame = project(view.R, view.t, view.camera, pos, cfg.near_fraction * extent)
    mask = view_mask(view)
    xy *= mask_grid(mask, view.camera, cfg.grid_tolerance)
    h, w = mask.shape
    ids = np.flatnonzero(frame)
    px, py = np.floor(xy[ids]).astype('int64').T
    state = mask_states(mask, cfg.mask_scale, cfg.mask_dilate)[py // cfg.mask_scale, px // cfg.mask_scale]
    judged = np.ones(len(ids), dtype=bool)
    if not cfg.no_depth_test:
        Z = depth_buffer(xy, z, frame, alpha, w, h, cfg.depth_scale, cfg.depth_alpha_min, cfg.depth_fill)
        tau = cfg.depth_abs * extent + cfg.depth_rel * z[ids] + cfg.depth_extent * scale[ids].max(1)
        judged &= z[ids] <= Z[py // cfg.depth_scale, px // cfg.depth_scale] + tau
    if cfg.edge == 'abstain':
        judged &= state != 2
    inside = (state == 1) | ((state == 2) & (cfg.edge == 'inside'))
    V, inside_count = np.zeros(len(pos), dtype=bool), np.zeros(len(pos), dtype=bool)
    V[ids] = judged
    inside_count[ids] = judged & inside
    return V, inside_count, frame


def vote(pos, scale, alpha, views, extent, cfg):
    import numpy as np

    V, inside_count = np.zeros(len(pos), dtype='uint32'), np.zeros(len(pos), dtype='uint32')
    frame = np.zeros(len(pos), dtype=bool)
    for view in sorted(views, key=lambda v: v.name):
        judged, inside, in_frame = view_votes(pos, scale, alpha, view, extent, cfg)
        V += judged
        inside_count += inside
        frame |= in_frame
    fraction = float(frame.mean())
    if fraction < cfg.frame_check_min:
        raise ValueError(f'only {fraction:.1%} of splats project into a jury view with positive depth; '
                         "the cloud does not appear to be in the solve's frame; "
                         "use the matching archived PLY and solve")
    unjudged = V < cfg.min_views
    keep = np.where(unjudged, cfg.unjudged == 'keep',
                    (inside_count >= cfg.min_inside) & (inside_count >= cfg.inside_fraction * V))
    return keep, V, inside_count, fraction


def outlier_keep(pos, alpha, extent, cfg):
    """27-cell support including self; +1 padding prevents lattice-row wrapping."""
    import numpy as np

    if cfg.no_outlier or cfg.outlier_min_neighbours == 0 or not len(pos):
        return np.ones(len(pos), dtype=bool)
    cell = cfg.outlier_cell_fraction * extent
    coords = np.floor((pos - pos.min(0)) / cell)
    dims_float = coords.max(0) + 3
    if not np.isfinite(dims_float).all() or math.prod(int(x) for x in dims_float) > np.iinfo('int64').max:
        raise ValueError('outlier lattice too large; increase --outlier-cell-fraction')
    coords = coords.astype('int64') + 1
    dims = dims_float.astype('int64')
    stride = np.array([dims[1] * dims[2], dims[2], 1], dtype='int64')
    keys, inverse, counts = np.unique(coords @ stride, return_inverse=True, return_counts=True)
    mass = counts if cfg.outlier_weight == 'count' else np.bincount(inverse, weights=alpha)
    support = np.zeros(len(keys), dtype='float64')
    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                wanted = keys + np.array([x, y, z]) @ stride
                found = np.searchsorted(keys, wanted)
                safe = np.minimum(found, len(keys) - 1)
                matched = (found < len(keys)) & (keys[safe] == wanted)
                support[matched] += mass[safe[matched]]
    return support[inverse] >= cfg.outlier_min_neighbours


def select_views(views, count, mode, focus):
    import numpy as np

    views = sorted(views, key=lambda v: v.name)
    if mode == 'all' or count >= len(views):
        return views
    if mode == 'even':
        return [views[round(i * (len(views) - 1) / (count - 1))] for i in range(count)] if count > 1 else views[:1]
    directions = np.array([-v.R.T @ v.t - focus for v in views])
    lengths = np.linalg.norm(directions, axis=1)
    if np.any(lengths == 0):
        raise ValueError('camera at convergence point; provide a solve with a usable orbit')
    directions /= lengths[:, None]
    chosen = [0]
    distance = np.full(len(views), np.inf)
    while len(chosen) < count:
        distance = np.minimum(distance, np.sum((directions - directions[chosen[-1]]) ** 2, axis=1))
        distance[chosen] = -1
        chosen.append(int(np.argmax(distance)))
    return sorted((views[i] for i in chosen), key=lambda v: v.name)


def read_cloud(path):
    """Memmap named PLY columns only; never materialize all SH properties."""
    import numpy as np

    ply_header, read_sog, *_ = shared()
    if path.suffix.lower() == '.sog':
        n, (pos, scale, alpha, _) = read_sog(path)
        return pos, scale, alpha, None
    if path.suffix.lower() != '.ply':
        raise ValueError('expected a binary scalar PLY or SOG v2; re-export the archived PLY')
    with path.open('rb') as f:
        n, fmt, props, header = ply_header(f)
        offset = f.tell()
    if fmt not in {'binary_little_endian', 'binary_big_endian'}:
        raise ValueError('byte-exact pruning requires fixed-width binary PLY; export a binary archived PLY')
    endian = '<' if fmt == 'binary_little_endian' else '>'
    dtype = np.dtype([(name, endian + kind) for name, kind in props])
    if path.stat().st_size < offset + n * dtype.itemsize:
        raise ValueError('truncated PLY vertices; recover the complete archived PLY')
    rec = np.memmap(path, mode='r', dtype=dtype, offset=offset, shape=(n,))
    pos = np.column_stack([rec[k] for k in ('x', 'y', 'z')]).astype('float64')
    with np.errstate(over='ignore', invalid='ignore'):
        scale = np.exp(np.column_stack([rec[f'scale_{i}'] for i in range(3)]).astype('float64'))
        alpha = np.exp(-np.logaddexp(0, -rec['opacity'].astype('float64')))
    return pos, scale, alpha, (header, rec, offset + n * dtype.itemsize)


def write_subset(source, destination, records, keep):
    """Preserve every property and each selected record byte, including SH bands."""
    import numpy as np

    header, rec, tail = records
    # Non-vertex element payloads may reference old vertex indices: refuse
    # rather than copying stale mesh topology into a Gaussian cloud.
    for name, count in re.findall(rb'^element\s+(\S+)\s+(\d+)', header, re.MULTILINE):
        if name != b'vertex' and int(count):
            raise ValueError('PLY has non-vertex elements; export a Gaussian-only binary PLY')
    header = re.sub(rb'(?m)^(element\s+vertex\s+)\d+',
                    lambda m: m[1] + str(int(keep.sum())).encode(), header)
    with destination.open('wb') as out:
        out.write(header)
        # Bounded chunks avoid a second whole-cloud allocation.
        raw = rec.view('uint8').reshape(len(rec), rec.dtype.itemsize)
        for start in range(0, len(rec), 65536):
            out.write(raw[start:start + 65536][keep[start:start + 65536]].tobytes())
        with source.open('rb') as original:
            original.seek(tail)
            while block := original.read(1024 * 1024):
                out.write(block)
    return int(np.count_nonzero(keep))


def stage(name, before, after, mass):
    import numpy as np

    total = mass[before].sum(dtype=np.float64)
    removed_mass = mass[before & ~after].sum(dtype=np.float64)
    return {'name': name, 'removed': int((before & ~after).sum()),
            'removed_mass_fraction': float(removed_mass / total) if total > 0 else 0.0}


def holdout_metrics(pos, views, cfg, extent):
    """Micro precision/recall of projected-centre occupancy on mask-scale cells.

    Target cells contain any mask pixel. This is an explicit silhouette proxy,
    not a Gaussian render or a claim of photometric quality. Same fixed grid
    before/after; held-out masks never influence the keep decision.
    """
    import numpy as np

    rows = []
    for view in views:
        xy, _, valid = project(view.R, view.t, view.camera, pos, cfg.near_fraction * extent)
        mask = view_mask(view)
        xy *= mask_grid(mask, view.camera, cfg.grid_tolerance)
        target = mask_states(mask, cfg.mask_scale, 0) != 0
        predicted = np.zeros_like(target)
        px, py = np.floor(xy[valid] / cfg.mask_scale).astype('int64').T
        predicted[py, px] = True
        tp = int((predicted & target).sum())
        predicted_count, target_count = int(predicted.sum()), int(target.sum())
        rows.append({'name': view.name, 'intersection': tp, 'predicted': predicted_count, 'target': target_count,
                     'precision': tp / predicted_count if predicted_count else 0.0,
                     'recall': tp / target_count if target_count else 0.0})
    tp = sum(r['intersection'] for r in rows)
    predicted_count, target_count = sum(r['predicted'] for r in rows), sum(r['target'] for r in rows)
    return {'precision': tp / predicted_count if predicted_count else None,
            'recall': tp / target_count if target_count else None, 'views': rows,
            'method': 'projected-centre cell occupancy; micro average; no photometric rendering'}


def mask_coverage(pos, views, cfg, extent, dilate=0):
    """Fraction of in-mask cells still holding a splat centre, on the jury's own views.

    The isolation statistics cannot be an acceptance signal for a pruner: they
    improve monotonically as the subject is deleted, so amputation is the
    cheapest way to make them look good. Measured on the GPU host's cannon at
    71% removal, the 99th-percentile radius improved by a factor of 10.8 while
    coverage fell 0.876 to 0.646 and the rule deleted half the splats its own
    jury called subject.

    Coverage moves the other way, and unlike held-out recall it costs no jury
    views: it is a grid statistic rather than a splat-set one, so removing a
    wheel's rim empties in-mask cells even in the views that voted to keep the
    rest of the wheel. `dilate` grows occupancy by one cell, which checks the
    reading is not an artefact of scoring centres instead of footprints.
    """
    import numpy as np

    covered = target_total = 0
    rows = []
    for view in views:
        xy, _, valid = project(view.R, view.t, view.camera, pos, cfg.near_fraction * extent)
        mask = view_mask(view)
        xy *= mask_grid(mask, view.camera, cfg.grid_tolerance)
        target = mask_states(mask, cfg.mask_scale, 0) == 1
        occupied = np.zeros_like(target)
        px, py = np.floor(xy[valid] / cfg.mask_scale).astype('int64').T
        occupied[py, px] = True
        for _ in range(dilate):
            grown = occupied.copy()
            for axis in (0, 1):
                for shift in (-1, 1):
                    grown |= np.roll(occupied, shift, axis=axis)
            occupied = grown
        hit, need = int((occupied & target).sum()), int(target.sum())
        covered, target_total = covered + hit, target_total + need
        rows.append({'name': view.name, 'covered': hit, 'in_mask': need,
                     'coverage': hit / need if need else None})
    return {'coverage': covered / target_total if target_total else None,
            'dilate': dilate, 'views': rows,
            'method': 'in-mask cell occupancy by projected centres on the jury views; micro average'}


def prune(pos, scale, alpha, jury, holdout, cfg):
    import numpy as np

    mass = shared()[2](scale, alpha)
    mass = np.where(np.isfinite(mass) & (mass >= 0), mass, 0)
    all_ = np.ones(len(pos), dtype=bool)
    keep = (np.isfinite(pos).all(1) & np.isfinite(scale).all(1) & np.isfinite(alpha)
            & (alpha >= cfg.alpha_min) & (alpha > 0))
    stages = [stage('alpha/finite', all_, keep, mass)]
    if not keep.any():
        raise ValueError('every splat removed by alpha/finite floor; lower --alpha-min; no exports written')
    extent = float(np.diff(np.percentile(pos[keep], [5, 95], axis=0), axis=0).max())
    if not math.isfinite(extent) or extent <= 0:
        raise ValueError('cloud has zero robust extent; supply a spatially resolved cloud')
    baseline = keep.copy()
    voted, V, inside_count, frame = vote(pos[keep], scale[keep], alpha[keep], jury, extent, cfg)
    before = keep.copy()
    keep[np.flatnonzero(keep)] = voted
    stages.append(stage('view vote', before, keep, mass))
    before = keep.copy()
    keep[np.flatnonzero(keep)] = outlier_keep(pos[keep], alpha[keep], extent, cfg)
    stages.append(stage('neighbourhood', before, keep, mass))
    if not keep.any():
        raise ValueError('every splat removed; relax voting/outlier thresholds or use --unjudged keep; '
                         'no exports written')
    return keep, {'stages': stages, 'extent': extent, 'frame_fraction': frame,
                  'unjudged': int((V < cfg.min_views).sum()), 'unjudged_policy': cfg.unjudged,
                  'judged_views_max': int(V.max()), 'inside_views_max': int(inside_count.max()),
                  'holdout': {'before': holdout_metrics(pos[baseline], holdout, cfg, extent),
                              'after': holdout_metrics(pos[keep], holdout, cfg, extent)},
                  'coverage': {f'{when}_d{d}': mask_coverage(pos[which], jury, cfg, extent, d)
                               for when, which in (('before', baseline), ('after', keep))
                               for d in (0, 1)}}


def review_angles(views, focus, up, distance):
    """inspect_page angle grammar; d is fixed, never inferred from camera range."""
    import numpy as np

    up = np.asarray(up, dtype=float)
    if not np.isfinite(up).all() or np.linalg.norm(up) == 0:
        raise ValueError('camera up is indeterminate; use a solve with consistent camera orientation for --angles-out')
    up /= np.linalg.norm(up)
    target = np.array([0., 1., 0.])
    v, cosine = np.cross(up, target), float(up @ target)
    if cosine < -1 + 1e-12:
        rotation = np.diag([1., -1., -1.])
    else:
        K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        rotation = np.eye(3) + K + K @ K / (1 + cosine)
    result = []
    for view in views:
        center = (-view.R.T @ view.t - focus) * [1, -1, -1]
        x, y, z = rotation @ center
        norm = np.linalg.norm(center)
        if norm == 0:
            raise ValueError('camera at focus; cannot emit review angle')
        result.append(f'{np.degrees(np.arctan2(x, z)):.6f},{np.degrees(np.arcsin(np.clip(y / norm, -1, 1))):.6f}'
                      f',{distance:g}')
    return ';'.join(result)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_solve(args):
    import pycolmap

    sparse = args.solve / args.sparse
    if (sparse / 'cameras.bin').is_file() or (sparse / 'cameras.txt').is_file():
        if args.model is not None:
            raise ValueError('--model selects a subdirectory; omit it for a direct sparse model')
        return sparse, pycolmap.Reconstruction(sparse)
    if __package__:
        from .make_scene_payload import pick_model
    else:
        from make_scene_payload import pick_model
    with contextlib.redirect_stdout(sys.stderr):
        name, rec = pick_model(sparse, args.model)
    return sparse / name, rec


def prepare_views(rec, args, cfg):
    import numpy as np
    from PIL import Image

    registered = {im.name: im for im in rec.images.values()}
    names = sorted(registered)
    if args.names:
        names = sorted(set(n.strip() for n in args.names.read_text().splitlines() if n.strip()))
        missing = set(names) - registered.keys()
        if missing:
            raise ValueError(f'names not registered in solve: {sorted(missing)}; supply registered filenames')
    explicit = sorted(set(args.view_list.split(','))) if args.view_list else None
    if explicit and set(explicit) - set(names):
        raise ValueError('--view-list contains unavailable names; use registered names included by --names')
    views, excluded = [], []
    for name in names:
        path = args.masks / (Path(name).stem + '.png')
        # Validate all candidates before choosing a jury; no silent loss of views.
        if not path.is_file():
            raise ValueError(f'missing mask for jury/holdout candidate {name}: {path}; run make_subject_masks.py')
        with Image.open(path) as image:
            mask = np.asarray(image.convert('L')) > 0
        im = registered[name]
        cam = rec.cameras[im.camera_id]
        sx, sy = mask_grid(mask, cam, cfg.grid_tolerance)
        if sx != 1 or sy != 1:
            print(f'{name}: camera {cam.width}x{cam.height} -> mask {mask.shape[1]}x{mask.shape[0]}; '
                  f'scale pixel coordinates by {sx:g},{sy:g}', file=sys.stderr)
        coverage = float(mask.mean())
        if coverage < cfg.min_coverage or coverage >= cfg.max_coverage:
            print(f'warning: excluded {name}: degenerate mask coverage {coverage:.1%}', file=sys.stderr)
            excluded.append({'name': name, 'coverage': coverage})
            continue
        pose = im.cam_from_world()
        view = SimpleNamespace(name=name, camera=cam, R=np.asarray(pose.rotation.matrix()),
                                        t=np.asarray(pose.translation), path=path)
        # Refuse unsupported models even when all points would be behind them.
        project(view.R, view.t, cam, np.zeros((1, 3)))
        views.append(view)
    if len(views) <= cfg.holdout:
        raise ValueError('not enough usable masks for jury and holdout; regenerate masks or reduce --holdout')
    focus, _ = shared()[3](rec)
    if explicit:
        jury = [v for v in views if v.name in explicit]
        remaining = [v for v in views if v.name not in explicit]
        if not jury or len(remaining) < cfg.holdout:
            raise ValueError('--view-list leaves no jury or too few held-out views; reserve --holdout views')
        holdout = select_views(remaining, cfg.holdout, 'spread', focus) if cfg.holdout else []
    else:
        holdout = select_views(views, cfg.holdout, 'even', focus) if cfg.holdout else []
        reserved = {v.name for v in holdout}
        jury = select_views([v for v in views if v.name not in reserved], cfg.views, cfg.view_select, focus)
    return jury, holdout, focus, excluded


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('cloud', type=Path)
    parser.add_argument('--solve', type=Path, required=True)
    parser.add_argument('--sparse', type=Path, default=Path('sparse'))
    parser.add_argument('--model', type=int)
    parser.add_argument('--masks', type=Path, required=True)
    for key in ('names', 'out-ply', 'keep-out', 'report', 'angles-out', 'defaults'):
        parser.add_argument('--' + key, type=Path)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--view-list', help='comma-separated registered filenames; order is canonicalized')
    parser.add_argument('--view-select', choices=['spread', 'even', 'all'], default='spread')
    parser.add_argument('--edge', choices=['abstain', 'inside', 'outside'], default='abstain')
    parser.add_argument('--unjudged', choices=['keep', 'drop'], default='keep')
    parser.add_argument('--outlier-weight', choices=['count', 'alpha'], default='count')
    parser.add_argument('--no-depth-test', action='store_true')
    parser.add_argument('--no-outlier', action='store_true')
    defaults = load_defaults()
    groups = {'view selection': ('views', 'holdout'),
              'membership': ('alpha_min', 'mask_scale', 'mask_dilate'),
              'visibility': ('depth_alpha_min', 'depth_scale', 'depth_rel', 'depth_abs', 'depth_extent', 'depth_fill'),
              'decision': ('min_views', 'min_inside', 'inside_fraction'),
              'neighbourhood': ('outlier_cell_fraction', 'outlier_min_neighbours'),
              'review': ('frame_check_min', 'sheet_distance')}
    for title, keys in groups.items():
        group = parser.add_argument_group(title)
        for key in keys:
            value = defaults[key]['value']
            group.add_argument('--' + key.replace('_', '-'), type=int if type(value) is int else float,
                               default=None, help=defaults[key]['rationale'] + f' Default: {value}.')
    args = parser.parse_args(argv)
    if args.defaults:
        try:
            custom = load_defaults(args.defaults)
            if not isinstance(custom, dict):
                raise ValueError('expected an object mapping thresholds to value/source/rationale entries')
            for key, entry in custom.items():
                if not isinstance(entry, dict) or not {'value', 'source', 'rationale'} <= entry.keys():
                    raise ValueError(f'{key}: expected value, source, rationale')
            unknown = custom.keys() - defaults.keys()
            if unknown:
                raise ValueError(f'unknown defaults: {sorted(unknown)}')
            defaults.update(custom)
        except (OSError, ValueError, TypeError) as exc:
            parser.error(f'--defaults: {exc}')
    for key, entry in defaults.items():
        value = getattr(args, key, None)
        if value is None:
            value = entry['value']
        original = load_defaults()[key]['value']
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            parser.error(f'--{key.replace("_", "-")} must be finite and nonnegative')
        if type(original) is int and type(value) is not int:
            parser.error(f'--{key.replace("_", "-")} must be an integer')
        if key in {'views', 'mask_scale', 'depth_scale', 'outlier_cell_fraction', 'sheet_distance', 'near_fraction'}:
            if value <= 0:
                parser.error(f'--{key.replace("_", "-")} must be positive')
        if key in {'inside_fraction', 'alpha_min', 'depth_alpha_min', 'frame_check_min',
                   'min_coverage', 'max_coverage'} and value > 1:
            parser.error(f'--{key.replace("_", "-")} must be in [0,1]')
        setattr(args, key, value)
    if args.min_coverage >= args.max_coverage:
        parser.error('min_coverage must be below max_coverage in --defaults')
    if args.keep_out and args.keep_out.suffix != '.npz':
        parser.error('--keep-out must end in .npz')
    if args.out_ply and args.cloud.suffix.lower() == '.sog':
        parser.error('SOG is a delivery format; re-export from the archived PLY')
    args.defaults_entries = defaults
    return parser, args


def main(argv=None):
    parser, args = parse_args(argv)
    import numpy as np

    try:
        sparse, rec = load_solve(args)
        jury, holdout, focus, excluded = prepare_views(rec, args, args)
        pos, scale, alpha, records = read_cloud(args.cloud)
        keep, result = prune(pos, scale, alpha, jury, holdout, args)
        paths = [args.cloud, *[v.path for v in jury + holdout],
                 *[p for p in sorted(sparse.iterdir()) if p.is_file()]]
        if args.names:
            paths.append(args.names)
        paths.append(args.defaults or Path(__file__).with_name('prune_defaults.json'))
        outputs = [p for p in (args.out_ply, args.keep_out, args.report, args.angles_out) if p]
        if len({p.resolve() for p in outputs}) != len(outputs) or {p.resolve() for p in outputs} & {
                p.resolve() for p in paths}:
            raise ValueError('output paths overlap inputs or each other; choose distinct candidate paths')
        # Validate PLY topology before creating any outputs.
        if args.out_ply and any(name != b'vertex' and int(count) for name, count in
                               re.findall(rb'^element\s+(\S+)\s+(\d+)', records[0], re.MULTILINE)):
            raise ValueError('PLY has non-vertex elements; export a Gaussian-only binary PLY')
        angles = None
        if args.angles_out:
            up = shared()[4]([v.R for v in jury + holdout])['-y']
            if up is None:
                raise ValueError('camera up is indeterminate; use consistently oriented cameras for --angles-out')
            angles = review_angles(holdout or jury, focus, up, args.sheet_distance)
        thresholds = {k: getattr(args, k) for k in args.defaults_entries}
        for key in ('view_select', 'edge', 'unjudged', 'outlier_weight', 'no_depth_test', 'no_outlier'):
            thresholds[key] = getattr(args, key)
        inputs = [{'path': str(p), 'sha256': sha256(p)} for p in dict.fromkeys(paths)]
        result.update(schema_version=1, tool='prune_by_views', inputs=inputs, n=len(pos), kept=int(keep.sum()),
                      source_sha256=inputs[0]['sha256'], jury=[v.name for v in jury],
                      holdout_views=[v.name for v in holdout], excluded_views=excluded, thresholds=thresholds,
                      defaults_source=str(args.defaults or Path(__file__).with_name('prune_defaults.json')),
                      defaults=args.defaults_entries, flags=list(sys.argv[1:] if argv is None else argv))
        encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
        for p in outputs:
            p.parent.mkdir(parents=True, exist_ok=True)
        if args.out_ply:
            write_subset(args.cloud, args.out_ply, records, keep)
        if args.keep_out:
            np.savez(args.keep_out, keep=keep, n=len(pos), source_sha256=inputs[0]['sha256'])
        if args.report:
            args.report.write_text(encoded)
        if args.angles_out:
            args.angles_out.write_text(angles + '\n')
        stream = sys.stderr if args.json else sys.stdout
        for row in result['stages']:
            print(f"{row['name']}: removed {row['removed']:,} splats; "
                  f"removed-mass fraction {row['removed_mass_fraction']:.12g} "
                  '(of pre-stage alpha-weighted area)', file=stream)
        print(f"extent {result['extent']:.6g} solve units; {result['kept']:,}/{result['n']:,} splats; "
              f"unjudged {result['unjudged']:,} ({args.unjudged})", file=stream)
        before, after = result['holdout']['before'], result['holdout']['after']
        print(f"holdout precision {before['precision']} -> {after['precision']}; "
              f"recall {before['recall']} -> {after['recall']}", file=stream)
        cov = result['coverage']
        print(f"in-mask coverage {cov['before_d0']['coverage']:.4f} -> {cov['after_d0']['coverage']:.4f} "
              f"(dilated {cov['before_d1']['coverage']:.4f} -> {cov['after_d1']['coverage']:.4f}); "
              'coverage and held-out recall are the acceptance signals, not the count; '
              'view the candidate', file=stream)
        if angles:
            print(f'angles {args.angles_out}: {angles}', file=stream)
        if args.json:
            print(encoded, end='')
    except (OSError, ValueError, KeyError, IndexError, TypeError, ImportError,
            zipfile.BadZipFile, np.linalg.LinAlgError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
