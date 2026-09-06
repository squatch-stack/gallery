"""Write the provenance sheet for a scanned subject.

    ~/.venvs/photogram/bin/python tools/provenance.py \
        ~/Documents/squatch-captures/photo-subjects/cannon \
        --trained gpugate-<job>/metrics.json --export scenes/cannon.sog \
        --out provenance/cannon.md

Institutions that fund 3D documentation (battlefield-interpretation grants,
museum digitization) expect a record of how a model was made, not a promise
that it was made carefully. This produces that record from the files the
pipeline already leaves behind: EXIF from the photographs (device, dates,
focal length), the COLMAP solve (registered views, points, reprojection
error, camera model), the trainer's metrics.json (steps, resolution, seed,
splat count, wall time, machine), the export (format, size, checksum), and
the masks if any were used. Everything is measured from the inputs; nothing
is typed in by hand except the subject's title and place.

For deliveries whose raw inputs are unavailable, use the dependency-free mode:
    python tools/provenance.py oak --app-export --source 'Studio app; Brush training' \
        --source-commit f9357c4 --out provenance/oak.html
This mode reads the catalog and delivered files, quotes operator-selected git
messages, and omits unavailable solve/training statistics. --cleaning must quote
one of those messages; --note records evidence gaps without claiming measurements.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import pathlib
import struct
import subprocess
import sys


SIDECAR_SCHEMA_VERSION = 1


def json_value(value):
    """Return a stable, JSON-compatible representation of an argparse value."""
    if isinstance(value, pathlib.Path):
        return str(value)
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def portable(value, repo):
    """Paths in a tracked sidecar must not carry this machine's home directory:
    repo paths become repo-relative, other home paths start with ~."""
    if isinstance(value, list):
        return [portable(v, repo) for v in value]
    if isinstance(value, dict):
        return {k: portable(v, repo) for k, v in value.items()}
    if isinstance(value, pathlib.Path):
        value = str(value)
    if isinstance(value, str) and value.startswith("/"):
        try:
            return str(pathlib.Path(value).resolve().relative_to(repo.resolve()))
        except ValueError:
            home = str(pathlib.Path.home())
            if value.startswith(home + "/"):
                return "~" + value[len(home):]
            # Anywhere else on this machine (a scratch directory, a mounted
            # volume) can carry a username in its path too: keep the file
            # name only, marked as external.
            return "<external>/" + pathlib.Path(value).name
    return value


def write_sidecar(a, mode, argv=None, generated=None):
    """Save the inputs needed to reproduce a provenance sheet."""
    stamp = generated or dt.datetime.now(dt.timezone.utc)
    inputs = {key: json_value(value) for key, value in vars(a).items()}
    data = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "argv": list(sys.argv[1:] if argv is None else argv),
        "mode": mode,
        "date": stamp.isoformat(timespec="seconds"),
        "inputs": inputs,
    }
    data = portable(data, pathlib.Path(__file__).resolve().parent.parent)
    path = a.out.with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print("wrote", path)


def sha256(p, n=16):
    digest = hashlib.sha256()
    with pathlib.Path(p).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:n]


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
    return triangles, data.get("asset", {}).get("generator")


def git_output(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).rstrip()


def app_export(a, repo, argv=None):
    """Document catalog facts and attributed operator/history evidence only."""
    catalog = json.loads((repo / "scenes.json").read_text())
    scene = next((s for s in catalog if s["stem"] == str(a.subject)), None)
    if scene is None:
        raise ValueError(f"unknown catalog stem: {a.subject}")
    evidence = []
    for ref in a.source_commit:
        commit = git_output(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        evidence.append((commit, git_output(repo, "show", "-s", "--format=%B", commit)))
    cleaning_source = getattr(a, "cleaning_source", "source-commit")
    if cleaning_source == "source-commit":
        for cleaning in a.cleaning:
            if not any(" ".join(cleaning.split()) in " ".join(message.split()) for _, message in evidence):
                raise ValueError("--cleaning must quote a supplied source commit message")
    title = scene["title"]
    lines = [
        f"# Provenance: {title}",
        "",
        "## Catalog record",
        "",
        "Source: working-tree scenes.json; repository base identified below. These are catalog claims.",
        "",
        f"**Scene:** {scene['stem']}",
    ]
    for key, label in [("place", "Place"), ("captured", "Captured"), ("blurb", "Description")]:
        if scene.get(key):
            lines.append(f"**{label}:** {scene[key]}")
    if not scene.get("mesh") and "splats" in scene:
        lines.append(f"**Catalog splats:** {scene['splats']:,}")
    lines += [
        "",
        "## Capture and export",
        "",
        f"**Operator source summary:** {a.source}",
        "",
        (
            "The summary is supplied with --source and attributed to the quoted history below; "
            "it is not a measurement from raw capture files."
        ),
    ]
    if a.cleaning:
        cleaning_intro = (
            "Candidate cleaning command/flags recorded at promotion:"
            if cleaning_source == "candidate"
            else "Excerpts from the source commit messages:"
        )
        lines += ["", "## Recorded cleaning", "", cleaning_intro]
        lines += [f"> {cleaning}" for cleaning in a.cleaning]
    if a.note:
        lines += ["", "## Evidence limits", "", *a.note]
    if a.export:
        exports = [p if p.is_absolute() else repo / p for p in a.export]
    elif scene.get("mesh"):
        exports = [repo / scene["mesh"]]
    else:
        exports = [
            repo / "scenes" / f"{scene['stem']}{ext}"
            for ext in (".sog", ".spz", ".ply")
            if (repo / "scenes" / f"{scene['stem']}{ext}").is_file()
        ]
    if not exports:
        raise ValueError("no delivered files found")
    lines += ["", "## Delivered files", ""]
    for path in exports:
        relative = path.resolve().relative_to(repo.resolve())
        lines += [
            (
                f"- `{relative}`: {path.suffix[1:].upper()}, {path.stat().st_size:,} bytes "
                f"({path.stat().st_size / 1e6:.3f} MB); sha256 `{sha256(path, n=64)}`"
            )
        ]
        if path.suffix.lower() == ".glb":
            triangles, generator = glb_summary(path)
            lines.append(
                f"- GLB 2 mesh: {triangles:,} stored triangles (accessor counts; not multiplied by scene instances)."
            )
            if generator:
                lines.append(f"- GLB asset.generator: {generator}")
    lines += [
        "",
        "## Source commit messages",
        "",
        "Verbatim repository messages; historical claims may describe earlier versions of a scene.",
    ]
    for commit, message in evidence:
        lines += ["", f"**Source commit:** {commit}", ""]
        lines += [f"> {line}" for line in message.splitlines()]
    commit = git_output(repo, "rev-parse", "HEAD")
    generated = dt.datetime.now(dt.timezone.utc)
    lines += [
        "",
        "## Processing",
        "",
        f"- Repository HEAD: {commit}.",
        (
            "- Sheet generated from the current working-tree catalog and delivered files; "
            "HEAD identifies the repository base, not an assertion that this sheet is committed."
        ),
        f"- Generated: {generated.isoformat(timespec='seconds')}.",
        "- License: CC BY 4.0 unless the commissioning agreement states otherwise.",
        "",
    ]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    md = "\n".join(lines)
    a.out.write_text(render_html(title, md, a.out) if a.out.suffix.lower() == ".html" else md)
    print("wrote", a.out)
    write_sidecar(a, "app-export", argv=argv, generated=generated)


def exif_summary(images, names):
    from PIL import Image

    devices, dates, focals = set(), [], set()
    for n in names:
        try:
            ex = Image.open(images / n).getexif()
        except (OSError, ValueError):
            # Missing/unreadable EXIF contributes no capture evidence.
            continue
        make, model = ex.get(0x010F, ""), ex.get(0x0110, "")
        if model:
            devices.add(f"{make} {model}".strip())
        d = ex.get(0x0132) or ex.get_ifd(0x8769).get(0x9003)
        if d:
            try:
                # EXIF here has no offset: retain the camera's local wall time.
                dates.append(dt.datetime.strptime(str(d), "%Y:%m:%d %H:%M:%S"))
            except ValueError:
                pass
        f = ex.get_ifd(0x8769).get(0xA405)
        if f:
            focals.add(f"{int(f)} mm equiv.")
    span = ""
    if dates:
        a, b = min(dates), max(dates)
        span = a.strftime("%Y-%m-%d %H:%M") + (" to " + b.strftime("%H:%M") if b != a else "")
    return ", ".join(sorted(devices)) or "unknown", span or "unknown", ", ".join(sorted(focals)) or "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", type=pathlib.Path)
    ap.add_argument("--app-export", action="store_true", help="document a catalog stem without raw solve inputs")
    ap.add_argument("--source", help="operator summary of capture app/export format; state gaps explicitly")
    ap.add_argument("--source-commit", action="append", default=[], help="commit to quote verbatim; repeatable")
    ap.add_argument("--cleaning", action="append", default=[], help="cleaning excerpt from a source commit")
    ap.add_argument("--cleaning-source", choices=["source-commit", "candidate"], default="source-commit",
                    help=argparse.SUPPRESS)
    ap.add_argument("--note", action="append", default=[], help="evidence limitation or conflict; repeatable")
    ap.add_argument("--title", default=None)
    ap.add_argument("--place", default="")
    ap.add_argument("--sparse", default="sparse")
    ap.add_argument(
        "--mapper",
        choices=["incremental", "global"],
        default="incremental",
        help="which COLMAP mapper produced the solve (the files do not record it)",
    )
    ap.add_argument(
        "--masked",
        action="store_true",
        help="the trained job used the subject masks (a masks/ folder alone does not prove it)",
    )
    ap.add_argument(
        "--supervision",
        choices=["masks", "alpha"],
        default="masks",
        help="how the masks reached the trainer: a masks/ folder (loss ignored outside the subject) "
             "or the image alpha channel (transparency outside the subject is a target, which isolates it)",
    )
    ap.add_argument("--trained", type=pathlib.Path, help="trainer metrics.json")
    ap.add_argument("--export", type=pathlib.Path, action="append", default=[], help="delivered file(s)")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(parsed_argv)

    if a.app_export:
        if not a.source or not a.source.strip() or not a.source_commit:
            ap.error("--app-export requires --source and at least one --source-commit")
        try:
            app_export(a, pathlib.Path(__file__).resolve().parent.parent, argv=parsed_argv)
        except (ValueError, OSError, KeyError, IndexError, struct.error, subprocess.CalledProcessError) as exc:
            ap.error(str(exc))
        return

    import pycolmap

    sp = a.subject / a.sparse
    sub = sp / "0" if (sp / "0").is_dir() else sp
    rec = pycolmap.Reconstruction(sub)
    names = sorted(im.name for im in rec.images.values())
    total = len([p for p in (a.subject / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")])
    cam = next(iter(rec.cameras.values()))
    device, span, focal = exif_summary(a.subject / "images", names)
    masks = a.subject / "masks"
    n_masks = len(list(masks.glob("*.png"))) if masks.is_dir() else 0
    title = a.title or a.subject.name.replace("-", " ").title()

    lines = [f"# Provenance: {title}", ""]
    if a.place:
        lines += [f"**Place:** {a.place}  "]
    lines += [
        f"**Captured:** {span}  ",
        f"**Device:** {device}; {focal}  ",
        f"**Photographs:** {total} taken, {len(names)} registered by structure-from-motion  ",
        "",
    ]
    mapper = "global (GLOMAP)" if a.mapper == "global" else "incremental"
    cam_model = cam.model.name if hasattr(cam.model, "name") else cam.model
    lines += [
        "## Camera solve",
        "",
        f"- Solver: pycolmap {pycolmap.__version__}, {mapper} mapping",
        f"- Camera model: {cam_model}, {cam.width} x {cam.height} px",
        f"- Registered views: {len(names)} of {total}",
        f"- Sparse points: {rec.num_points3D():,}",
        f"- Mean reprojection error: {rec.compute_mean_reprojection_error():.2f} px",
        f"- Mean track length: {rec.compute_mean_track_length():.2f}",
        "",
    ]
    if a.masked and n_masks:
        if a.supervision == "alpha":
            how = [
                f"- {n_masks} subject masks (Grounding DINO + SAM 2), applied as the alpha channel of each "
                "training image with the background colour zeroed",
                "- The trainer matches transparency outside the subject, so nothing is rendered there: "
                "the piece is isolated and the wind-moved grass never enters the model",
            ]
        else:
            how = [
                f"- {n_masks} subject masks (Grounding DINO + SAM 2; white = supervised, black = ignored)",
                "- Purpose: exclude wind-moved vegetation and sky from the photometric loss",
            ]
        lines += ["## Masks", "", *how, ""]
    if a.trained and a.trained.is_file():
        m = json.loads(a.trained.read_text())
        argv = m.get("argv", [])
        trainer = pathlib.Path(argv[0]).name if argv else m.get("recipe", "unknown")
        cap = m.get("max_splats", m.get("cap_max", "?"))
        cap_s = f"{cap:,}" if isinstance(cap, int) else str(cap)
        splats = m.get("splats", "?")
        splats_s = f"{splats:,}" if isinstance(splats, int) else str(splats)
        lines += [
            "## Training",
            "",
            f"- Trainer: {trainer} via recipe `{m.get('recipe', '?')}`",
            (
                f"- Steps: {m.get('steps', '?')}; training resolution (long side): "
                f"{m.get('max_res', '?')} px; seed {m.get('seed', '?')}"
            ),
            f"- Splat cap: {cap_s}",
            f"- Result: {splats_s} splats in {m.get('wall_seconds', '?')} s",
            f"- Raw PLY sha256: {str(m.get('ply_sha256', ''))[:16]}...",
            "",
        ]
    if a.export:
        lines += ["## Delivered files", ""]
        for e in a.export:
            if e.is_file():
                lines.append(f"- `{e.name}` - {e.stat().st_size / 1048576:.1f} MB, sha256 {sha256(e)}...")
        lines.append("")
    repo = pathlib.Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    generated = dt.datetime.now(dt.timezone.utc)
    stamp = generated.strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        "## Processing",
        "",
        (
            f"- Pipeline: squatch-gallery tools at commit {commit}; cleaning by `tools/clean_export.py` "
            "(opacity floor, mass-centered subject crop, fog cull)"
        ),
        f"- Sheet generated {stamp} from the files above; nothing entered by hand except title and place.",
        "- License: CC BY 4.0 unless the commissioning agreement states otherwise.",
        "",
    ]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    md = "\n".join(lines)
    if a.out.suffix.lower() == ".html":
        a.out.write_text(render_html(title, md, a.out))
    else:
        a.out.write_text(md)
    print("wrote", a.out)
    write_sidecar(a, "solve", argv=parsed_argv, generated=generated)
    print("\n".join(lines[:12]))


def turntable_figure(out):
    """A rotation of the scan this sheet documents, if one has been captured.

    On a provenance sheet the turntable is evidence rather than decoration: it
    is what the reader is being told the provenance of. So it gets a caption
    and controls, unlike the silent decorative preview on the index cards, and
    the poster is a real still rather than a placeholder.
    """
    stem = out.stem
    root = out.parent.parent
    video, poster = root / "turntables" / f"{stem}.mp4", root / "turntables" / f"{stem}.jpg"
    if not (video.is_file() and poster.is_file()):
        return ""
    src, still = f"../turntables/{stem}.mp4", f"../turntables/{stem}.jpg"
    label = html.escape(f"Turntable of {stem}: the scan rotating once about its vertical axis")
    return f"""<figure class="turntable">
<video muted loop playsinline controls preload="none" poster="{still}"
       aria-label="{label}"></video>
<figcaption>A single rotation of the delivered scan. Captured from the same
viewer this sheet links to, at the resolution shipped; nothing is retouched.</figcaption>
</figure>
<script>
(function () {{
  var v = document.querySelector('.turntable video');
  if (!v) return;
  var still = window.matchMedia('(prefers-reduced-motion: reduce)');
  // The poster stays until the reader asks, if they have asked for less motion.
  function start() {{ if (!still.matches && !v.src) {{ v.src = {src!r}; v.play().catch(function () {{}}); }} }}
  function stop() {{ v.pause(); }}
  still.addEventListener('change', function () {{ still.matches ? stop() : start(); }});
  v.addEventListener('play', function () {{ if (!v.src) v.src = {src!r}; }});
  start();
}})();
</script>"""


def render_html(title, md, out_path=None):
    """A dependency-free rendering of the sheet: headings, bold, code, bullets.
    Served as a static page beside the scan it documents."""
    out, in_list = [], False
    for raw in md.splitlines():
        # Evidence is literal, including markdown characters in commit messages.
        if raw.startswith("> "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<blockquote>{html.escape(raw[2:]) or '&nbsp;'}</blockquote>")
            continue
        line = html.escape(raw.rstrip(), quote=False)
        line = line.replace("**", "\x00")
        parts = line.split("\x00")
        line = "".join(f"<b>{t}</b>" if i % 2 else t for i, t in enumerate(parts))
        while "`" in line:
            a_, b_ = line.find("`"), line.find("`", line.find("`") + 1)
            if b_ < 0:
                break
            line = line[:a_] + "<code>" + line[a_ + 1 : b_] + "</code>" + line[b_ + 1 :]
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    figure = turntable_figure(out_path) if out_path is not None else ""
    # After the h1 so the sheet still opens with what it is documenting.
    if figure and "</h1>" in body:
        head, rest = body.split("</h1>", 1)
        body = head + "</h1>" + figure + rest
    else:
        body = figure + body
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Provenance: {html.escape(title)}</title>
<style>body{{margin:0;background:#0d0b09;color:#e8e2d6;font:15px/1.6 ui-monospace,Menlo,monospace}}
main{{max-width:44rem;margin:0 auto;padding:2rem 1.25rem 4rem}}h1{{font-size:1.6rem;margin:0 0 1rem;color:#fff}}
h2{{font-size:1rem;letter-spacing:.06em;text-transform:uppercase;color:#e58a5e;margin:2rem 0 .5rem}}
p{{margin:.4rem 0}}ul{{padding-left:1.2rem;margin:.4rem 0}}li{{margin:.25rem 0}}
code{{background:#1a1612;padding:1px 5px;border-radius:3px}}
code{{overflow-wrap:anywhere}}
blockquote{{margin:0;padding-left:1rem;border-left:2px solid #3a332a;white-space:pre-wrap}}
a{{color:#e58a5e}}b{{color:#fff}}nav{{font-size:13px;margin-bottom:1.5rem}}
figure.turntable{{margin:1.5rem 0 2rem}}
figure.turntable video{{width:100%;max-width:26rem;display:block;border-radius:8px;background:#000}}
figure.turntable figcaption{{color:#97907f;font-size:13px;margin-top:.5rem;max-width:26rem}}</style></head>
<body><main><nav><a href="../index.html">&#8592; all scans</a></nav>{body}</main></body></html>
"""


if __name__ == "__main__":
    main()
