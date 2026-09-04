#!/usr/bin/env python3
"""File-based buyer checks. Budgets are project policy from LANE-B3-BRIEF.md.

Run with .venv-check/bin/python; requires numpy, and Pillow for SOG decoding.
Unknown/unreadable checks fail closed. JSON uses null for unavailable metrics.
"""

import argparse
import contextlib
import gzip
import io
import json
import re
import struct
import os
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUDGETS = {"web-mobile": (500_000, 20_000_000), "web-desktop": (1_500_000, 60_000_000), "fab": (None, None)}
FORMATS = {
    "web-mobile": {".sog", ".spz", ".ply", ".glb"},
    "web-desktop": {".sog", ".spz", ".ply", ".glb"},
    "fab": {".glb", ".obj", ".fbx"},
}
REQUIRED = {"x", "y", "z", "opacity"} | {
    f"{p}_{i}" for p, n in [("scale", 3), ("rot", 4), ("f_dc", 3)] for i in range(n)
}
TYPES = {
    "char": "i1",
    "uchar": "u1",
    "short": "i2",
    "ushort": "u2",
    "int": "i4",
    "uint": "u4",
    "float": "f4",
    "double": "f8",
    "float32": "f4",
    "float64": "f8",
}


def read_ply(path):
    """Read standard scalar 3DGS vertices, independent of property order."""
    import numpy as np

    with path.open("rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("invalid PLY magic")
        props, count, fmt, element = [], None, None, None
        for _ in range(10000):
            line = f.readline()
            if not line:
                raise ValueError("truncated PLY header")
            parts = line.decode("ascii").split()
            if not parts or parts[0] in {"comment", "obj_info"}:
                continue
            if parts[0] == "end_header":
                break
            if parts[0] == "format":
                fmt = parts[1]
            elif parts[0] == "element":
                element = parts[1]
                if element == "vertex":
                    count = int(parts[2])
                elif count is None and int(parts[2]):
                    raise ValueError("unsupported element before vertices")
            elif parts[0] == "property" and element == "vertex":
                if parts[1] not in TYPES or len(parts) != 3:
                    raise ValueError("unsupported vertex property")
                props.append((parts[2], TYPES[parts[1]]))
        else:
            raise ValueError("oversized PLY header")
        names = [p[0] for p in props]
        if count is None or count <= 0 or not REQUIRED <= set(names) or len(set(names)) != len(names):
            raise ValueError("PLY requires positive vertex count and standard 3DGS properties")
        if fmt == "ascii":
            rows = [f.readline().split() for _ in range(count)]
            if any(len(row) != len(props) for row in rows):
                raise ValueError("truncated or malformed PLY vertices")
            data = np.asarray(rows, dtype=float)
            rec = {name: data[:, i] for i, name in enumerate(names)}
        elif fmt in {"binary_little_endian", "binary_big_endian"}:
            endian = "<" if fmt == "binary_little_endian" else ">"
            dtype = np.dtype([(name, endian + kind) for name, kind in props])
            rec = np.frombuffer(f.read(count * dtype.itemsize), dtype=dtype, count=count)
        else:
            raise ValueError("unsupported PLY encoding")
        raw = np.column_stack([rec[name] for name in names])
        pos = np.column_stack([rec[k] for k in ("x", "y", "z")]).astype(float)
        with np.errstate(over="ignore", invalid="ignore"):
            scale = np.exp(np.column_stack([rec[f"scale_{i}"] for i in range(3)]).astype(float))
            alpha = np.exp(-np.logaddexp(0, -np.asarray(rec["opacity"], dtype=float)))
        return count, (pos, scale, alpha, raw)


def read_sog(path):
    import numpy as np

    from PIL import Image

    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("meta.json"))
        n = meta["count"]
        if meta.get("version") != 2 or type(n) is not int or n <= 0:
            raise ValueError("expected SOG v2 with positive count")
        for key in ("means", "scales", "sh0", "quats"):
            if not meta[key]["files"] or any(name not in z.namelist() for name in meta[key]["files"]):
                raise ValueError(f"missing SOG {key} planes")

        def plane(name):
            with Image.open(io.BytesIO(z.read(name))) as im:
                a = np.asarray(im.convert("RGBA")).reshape(-1, 4)
            if len(a) < n:
                raise ValueError("SOG plane smaller than count")
            return a[:n]

        low, high = [plane(name)[:, :3].astype(float) for name in meta["means"]["files"]]
        lo, hi = np.asarray(meta["means"]["mins"]), np.asarray(meta["means"]["maxs"])
        logpos = lo + (low + 256 * high) / 65535 * (hi - lo)
        pos = np.sign(logpos) * np.expm1(np.abs(logpos))
        scale = np.exp(np.asarray(meta["scales"]["codebook"])[plane(meta["scales"]["files"][0])[:, :3]])
        rgba = plane(meta["sh0"]["files"][0])
        color = np.asarray(meta["sh0"]["codebook"])[rgba[:, :3]]
        quat = plane(meta["quats"]["files"][0])
        if np.any(quat[:, 3] < 252):
            raise ValueError("invalid SOG quaternion encoding")
        return n, (pos, scale, rgba[:, 3] / 255, color)


def read_spz(path):
    import numpy as np

    with path.open("rb") as f:
        compressed = f.read(2) == b"\x1f\x8b"
    with gzip.open(path, "rb") if compressed else path.open("rb") as f:
        header = f.read(32)
    magic, version, n, degree, _bits, _flags, _reserved = struct.unpack("<IIIBBBB", header[:16])
    if magic != 0x5053474E or version not in (1, 2, 3, 4) or not n or degree > 3:
        raise ValueError("invalid SPZ header")
    if (version < 4) != compressed or (version == 4 and len(header) < 32):
        raise ValueError("invalid SPZ container")
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, os.environ.get("HOLO_REPO", os.path.expanduser("~/Documents/HDC-VSA-Gaussian-Splatting")))
        with contextlib.redirect_stdout(sys.stderr):
            from holo.capture import load_scene_file

            pos, scale, rgba, quat = load_scene_file(str(path))
        if len(pos) != n:
            raise ValueError("decoded count differs from header")
        return n, (pos, scale, rgba[:, 3], np.column_stack((rgba, quat))), None
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        ImportError,
        AssertionError,
        struct.error,
        zipfile.BadZipFile,
        EOFError,
    ) as exc:
        return n, None, f"SPZ geometry unavailable: {exc}"


def weighted_median(values, weights):
    import numpy as np

    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order[np.searchsorted(cumulative, cumulative[-1] / 2)]])


def cleanliness(arrays):
    import numpy as np

    pos, scale, alpha, raw = arrays
    finite = np.isfinite(pos).all(1) & np.isfinite(scale).all(1) & np.isfinite(alpha) & np.isfinite(raw).all(1)
    metrics = {
        "nonfinite_count": int((~finite).sum()),
        "floater_fraction": None,
        "fog_fraction": None,
        "translucent_fraction": None,
        "extent": None,
        "center": None,
        "mad": None,
    }
    if not finite.any():
        return metrics
    p, s, a = pos[finite], scale[finite], alpha[finite]
    metrics["translucent_fraction"] = float(np.mean(a < 0.05))
    extent = np.ptp(p, axis=0)
    metrics["extent"] = extent.tolist()
    metrics["fog_fraction"] = float(np.mean(np.any(s > 0.05 * extent.max(), axis=1)))
    if a.sum() > 0:
        center = np.array([weighted_median(p[:, i], a) for i in range(3)])
        distance = np.max(np.abs(p - center), axis=1)
        mad = weighted_median(distance, a)
        metrics.update(center=center.tolist(), mad=mad, floater_fraction=float(np.mean(distance > 3 * mad)))
    return metrics


def glb_summary(path):
    """Read GLB 2 JSON; count stored mesh triangles, not scene instances.

    Counts describe primitive topology (including degenerate triangles), without
    decoding geometry. Non-triangle primitives contribute no triangles.
    """
    with path.open("rb") as stream:
        magic, version, length = struct.unpack("<4sII", stream.read(12))
        if magic != b"glTF" or version != 2 or length != path.stat().st_size:
            raise ValueError("invalid GLB 2 header or length")
        size, kind = struct.unpack("<II", stream.read(8))
        if kind != 0x4E4F534A or size % 4 or size > length - 20:
            raise ValueError("invalid GLB JSON chunk")
        data = json.loads(stream.read(size))
    triangles = 0
    for mesh in data.get("meshes", []):
        for primitive in mesh["primitives"]:
            mode = primitive.get("mode", 4)
            if mode not in (4, 5, 6):
                continue
            accessor = primitive.get("indices", primitive["attributes"]["POSITION"])
            count = data["accessors"][accessor]["count"]
            if type(count) is not int or count < 0 or (mode == 4 and count % 3):
                raise ValueError("invalid triangle accessor count")
            triangles += count // 3 if mode == 4 else max(0, count - 2)
    texture_bytes = 0
    for image in data.get("images", []):
        if "bufferView" not in image:
            raise ValueError("GLB textures must be embedded in binary buffer views")
        view = data["bufferViews"][image["bufferView"]]
        size = view["byteLength"]
        if type(size) is not int or size < 0:
            raise ValueError("invalid texture byte length")
        texture_bytes += size
    return triangles, texture_bytes


def check_scene(target, platform="web-mobile", root=ROOT, subject=None):
    catalog = json.loads((root / "scenes.json").read_text())
    supplied = Path(target)
    scene = next((s for s in catalog if s["stem"] == supplied.stem), None)
    # An object stands alone on black, so splats far from the centre are
    # floaters. A place (a battlefield lawn, a desert wash, a room) IS its
    # surroundings; distance from the centre is scenery there, and the
    # floater rule would fail every honest landscape. The catalog says
    # which a scene is ("subject": "object" | "place"); --subject overrides.
    subject = subject or (scene or {}).get("subject", "object")
    if supplied.suffix:
        path = supplied if supplied.is_absolute() else root / supplied
    elif scene and scene.get("mesh"):
        path = root / scene["mesh"]
    else:
        path = next(
            (
                root / "scenes" / (target + ext)
                for ext in (".sog", ".spz", ".ply")
                if (root / "scenes" / (target + ext)).is_file()
            ),
            root / "scenes" / (target + ".sog"),
        )
    checks = []

    def add(name, passed, detail):
        checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})

    ext, count, arrays, issue = path.suffix.lower(), None, None, None
    size = path.stat().st_size if path.is_file() else None
    triangles, texture_bytes = None, None
    mesh = ext in {".glb", ".obj", ".fbx"}
    try:
        if size is None or size == 0:
            raise ValueError("file missing or empty")
        if ext == ".ply":
            count, arrays = read_ply(path)
        elif ext == ".sog":
            count, arrays = read_sog(path)
        elif ext == ".spz":
            count, arrays, issue = read_spz(path)
        elif mesh:
            if ext == ".glb":
                triangles, texture_bytes = glb_summary(path)
            count = 0
        else:
            raise ValueError("unrecognized format")
        add(
            "format",
            ext in FORMATS[platform],
            f"{ext}; {'mesh' if mesh else 'header parsed'}; allowed for {platform}: "
            + ", ".join(sorted(FORMATS[platform])),
        )
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        ImportError,
        AssertionError,
        struct.error,
        zipfile.BadZipFile,
        EOFError,
    ) as exc:
        add("format", False, f"{ext}: {exc}")
    if triangles is not None:
        checks.append({"name": "mesh", "status": "info",
                       "detail": f"{triangles} triangles; {texture_bytes} embedded texture bytes"})
    max_count, max_size = BUDGETS[platform]
    add(
        "count",
        count is not None and (max_count is None or count <= max_count),
        f"{count} splats; budget {max_count if max_count is not None else 'not applicable to meshes'}",
    )
    add(
        "size",
        size is not None and (max_size is None or size <= max_size),
        f"{size} bytes; budget {max_size if max_size is not None else 'unspecified'}",
    )
    expected = scene.get("splats") if scene else None
    delivered = path.resolve().is_relative_to((root / "scenes").resolve())
    if supplied.suffix and not delivered:
        checks.append({
            "name": "catalog",
            "status": "not_applicable",
            "detail": "candidate file outside scenes/; catalog is updated only on promotion",
        })
    else:
        match = (
            count is not None
            and isinstance(expected, (int, float))
            and abs(count - expected) <= 0.01 * count
        )
        add("catalog", match, f"file {count}; catalog {expected}; tolerance 1% of file count")
    metrics = None
    if mesh:
        checks.append({"name": "cleanliness", "status": "not_applicable", "detail": "mesh; splat proxies do not apply"})
    elif arrays is None:
        add("cleanliness", False, issue or "geometry unavailable; cleanliness unverified")
    else:
        metrics = cleanliness(arrays)
        for name, threshold in [("floater", 0.02), ("fog", 0.01), ("translucent", 0.10)]:
            value = metrics[name + "_fraction"]
            if name == "floater" and subject == "place":
                checks.append({
                    "name": name,
                    "status": "info",
                    "detail": (f"{value:.6%} far from centre; a place is its surroundings, so this is scenery, "
                               "not floaters (catalog subject: place)") if value is not None else "unverified",
                })
                continue
            add(
                name,
                value is not None and value < threshold,
                f"{value:.6%} < {threshold:.0%}" if value is not None else "unverified",
            )
        add("nonfinite", metrics["nonfinite_count"] == 0, f"{metrics['nonfinite_count']} non-finite splats; required 0")
    readme = (root / "README.md").read_text() if (root / "README.md").is_file() else ""
    stated = bool(
        re.search(r"scans?[^.\n]*(?:CC BY|licen[cs]e|public domain|all rights reserved)", readme, re.IGNORECASE)
    )
    provenance = scene.get("provenance") if scene else None
    add(
        "licence",
        (root / "LICENSE").is_file() and stated,
        f"LICENSE exists: {(root / 'LICENSE').is_file()}; README scan licence stated: {stated}",
    )
    exists = bool(provenance and (root / provenance).is_file())
    add("provenance", exists, f"{provenance or 'no catalog provenance page'}; exists: {exists}")
    return {
        "scene": scene["stem"] if scene else supplied.stem,
        # Repo-relative: checks.json is tracked and published, so it must not
        # carry this machine's home directory.
        "file": str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
        "platform": platform,
        "passed": all(c["status"] != "fail" for c in checks),
        "count": count,
        "triangles": triangles,
        "texture_bytes": texture_bytes,
        "size_bytes": size,
        "metrics": metrics,
        "checks": checks,
    }


def table(results):
    lines = ["| Scene | Splats | MB | Result | Failures |", "|---|---:|---:|---|---|"]
    for r in results:
        failures = ", ".join(c["name"] for c in r["checks"] if c["status"] == "fail") or "—"
        size = f"{r['size_bytes'] / 1e6:.3f}" if r["size_bytes"] is not None else "unknown"
        lines.append(f"| {r['scene']} | {r['count']} | {size} | {'PASS' if r['passed'] else 'FAIL'} | {failures} |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", nargs="?")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--platform", choices=BUDGETS, default="web-mobile")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--subject", choices=["object", "place"], default=None,
                        help="override the catalog: a place keeps its surroundings, so the floater rule is reported, "
                             "not enforced")
    args = parser.parse_args(argv)
    if bool(args.scene) == args.all:
        parser.error("specify a scene/file or --all")
    targets = [s["stem"] for s in json.loads((ROOT / "scenes.json").read_text())] if args.all else [args.scene]
    results = [check_scene(t, args.platform, subject=args.subject) for t in targets]
    if args.json:
        print(json.dumps({"schema_version": 1, "results": results}, indent=2, allow_nan=False))
    elif args.all:
        print(table(results))
    else:
        r = results[0]
        print(f"{r['scene']} — {args.platform}: {'PASS' if r['passed'] else 'FAIL'}")
        for c in r["checks"]:
            print(f"[{c['status'].upper()}] {c['name']}: {c['detail']}")
        print("Cleanliness metrics: " + json.dumps(r["metrics"], allow_nan=False))
    return int(any(not r["passed"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
