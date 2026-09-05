#!/usr/bin/env python3
"""Record third-party artefact hashes without installing or executing anything."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def https_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must use https with a hostname and no credentials")
    return value


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fingerprint(stream):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def download(url):
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    with opener.open(https_url(url), timeout=60) as response, tempfile.TemporaryFile() as artifact:
        https_url(response.geturl())
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            artifact.write(chunk)
        artifact.seek(0)
        return fingerprint(artifact)


def load_entries(root):
    path = root / "third-party.json"
    if not path.exists():
        return []
    entries = json.loads(path.read_text())
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("third-party.json must contain an array of records")
    return entries


def local_path(entry, root):
    return root / Path(entry["file"]).expanduser() if entry.get("file") else None


def save_entries(root, entries):
    path = root / "third-party.json"
    if path.is_symlink():
        raise ValueError("refusing a symlink registry")
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(entries, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def record(args, root):
    if not args.name.strip() or not args.version.strip():
        raise ValueError("name and version must not be blank")
    if not args.licence or not args.licence.strip():
        raise ValueError("--licence is required; use UNKNOWN with --note explaining why")
    if args.licence.upper() == "UNKNOWN":
        args.licence = "UNKNOWN"
        if not any(note.strip() for note in args.note):
            raise ValueError("--licence UNKNOWN requires --note explaining why")
    if args.url:
        https_url(args.url)
    if not args.url and not args.file:
        raise ValueError("record requires a URL or --file")
    entry = {
        "name": args.name, "version": args.version, "url": args.url,
        "file": os.path.relpath(args.file.expanduser().resolve(), root) if args.file else None,
        "licence": args.licence, "notes": args.note,
    }
    if args.no_fetch:
        if args.sha256 is None or args.size is None or args.date_checked is None:
            raise ValueError("--no-fetch requires --sha256, --size and --date-checked (use UNKNOWN for unknowns)")
        sha = args.sha256
        if sha != "UNKNOWN" and not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            raise ValueError("--sha256 must be 64 hex characters or UNKNOWN")
        size = None if args.size == "UNKNOWN" else int(args.size)
        if size is not None and size < 0:
            raise ValueError("--size must be nonnegative or UNKNOWN")
        checked = date.fromisoformat(args.date_checked).isoformat()
        entry.update(sha256=None if sha == "UNKNOWN" else sha.lower(), size_bytes=size,
                     date_checked=checked, verification="unverified", verified_at=None)
    else:
        if any(value is not None for value in (args.sha256, args.size, args.date_checked)):
            raise ValueError("supplied hash, size and date require --no-fetch")
        if args.file:
            with args.file.expanduser().open("rb") as stream:
                sha, size = fingerprint(stream)
        else:
            sha, size = download(args.url)
        checked = date.today().isoformat()
        entry.update(sha256=sha, size_bytes=size, date_checked=checked,
                     verification="hashed", verified_at=checked)
    entries = load_entries(root)
    for older in entries:
        if older["name"] == args.name and older["version"] != args.version and not older.get("superseded_by"):
            older["superseded_by"] = args.version
    entries.append(entry)
    save_entries(root, entries)
    print(f"Recorded {args.name} {args.version}: {entry['verification']}")


def verify(args, root):
    entries = [entry for entry in load_entries(root) if not args.name or entry["name"] == args.name]
    if args.name and not entries:
        print(f"FAIL {args.name}: no matching entries")
        return 1
    failed = False
    checked = 0
    for entry in entries:
        label = f"{entry['name']} {entry['version']}"
        path = args.file.expanduser() if args.file else local_path(entry, root)
        if args.offline and not entry.get("file"):
            print(f"SKIP {label}: no recorded local path")
            continue
        checked += 1
        try:
            if not entry.get("sha256"):
                raise ValueError("no recorded sha256; cannot verify")
            if path is not None:
                with path.open("rb") as stream:
                    sha, _ = fingerprint(stream)
            elif entry.get("url"):
                sha, _ = download(entry["url"])
            else:
                raise ValueError("no artefact source")
            if sha != entry["sha256"]:
                raise ValueError(f"sha256 mismatch: expected {entry['sha256']}, got {sha}")
            print(f"PASS {label}: sha256 matches")
        except (OSError, ValueError) as exc:
            failed = True
            print(f"FAIL {label}: {exc}")
    print(f"Verified {checked} entry(s)")
    return int(failed)


def table(entries):
    lines = ["| Name | Version | Licence | Date checked | Local artefact |",
             "|---|---|---|---|---|"]
    for entry in entries:
        fields = [entry[key] for key in ("name", "version", "licence", "date_checked")]
        fields.append("yes" if entry["local_present"] else "no")
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in fields) + " |")
    return "\n".join(lines)


def main(argv=None, root=None):
    root = Path(root) if root is not None else ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record", help="hash an artefact, or explicitly seed supplied metadata")
    rec.add_argument("url", nargs="?")
    rec.add_argument("--file", type=Path)
    rec.add_argument("--name", required=True)
    rec.add_argument("--version", required=True)
    rec.add_argument("--licence")
    rec.add_argument("--note", action="append", default=[])
    rec.add_argument("--no-fetch", action="store_true")
    rec.add_argument("--sha256")
    rec.add_argument("--size", help="byte count or UNKNOWN for --no-fetch")
    rec.add_argument("--date-checked", help="ISO date of supplied metadata review for --no-fetch")
    check = commands.add_parser("verify", help="compare current bytes to recorded hashes; does not edit records")
    check.add_argument("--name")
    check.add_argument("--file", type=Path, help="override source for a named artefact")
    check.add_argument("--offline", action="store_true")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "verify" and args.file and not args.name:
        parser.error("verify --file requires --name")
    try:
        if args.command == "record":
            record(args, root)
        elif args.command == "verify":
            return verify(args, root)
        else:
            entries = []
            for entry in load_entries(root):
                path = local_path(entry, root)
                entries.append({**entry, "local_present": path is not None and path.is_file()})
            print(json.dumps(entries, indent=2) if args.json else table(entries))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"vet_download: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
