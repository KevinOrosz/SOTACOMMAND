#!/usr/bin/env python3
"""Find this repository's Claude Code plugins and package them for a release.

Standard library only, Python 3.9 or later, no network access. The workflows in
.github/workflows/ call it; you can run it locally too.

A plugin directory is ``engine/`` or any folder directly under ``packs/`` or
``labs/``. Each one must hold ``.claude-plugin/plugin.json``.

  plugins.py list [--require-manifest]
      Print the plugin directories, one per line, relative to the repository
      root. With --require-manifest, exit 1 if one has no manifest.

  plugins.py package --tag vX.Y.Z --out DIR [--notes FILE]
      Write DIR/<name>-<version>.zip for every plugin, then DIR/SHA256SUMS.
      Each zip holds the files committed at HEAD under one top-level folder
      named after the plugin. Entries are sorted and stamped with the commit
      time (or SOURCE_DATE_EPOCH), so the same commit gives the same bytes.
      With --notes, also write release notes in Markdown: the CHANGELOG.md
      section for the version, the assets and their SHA-256, and how to
      verify them.

Exit codes: 0 ok, 1 a check failed, 2 usage error.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PARENTS = ("packs", "labs")
MANIFEST = Path(".claude-plugin") / "plugin.json"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?"
TAG_RE = re.compile(r"^v(" + SEMVER + r")$")
VERSION_RE = re.compile(r"^" + SEMVER + r"(?:\+[0-9A-Za-z.-]+)?$")
LINK_DEFINITION_RE = re.compile(r"^\[[^\]]+\]:\s")
ZIP_EARLIEST = 315532800  # 1980-01-01T00:00:00Z, the earliest time a zip entry can hold
MODE_FILE = "100644"
MODE_EXECUTABLE = "100755"


class CheckError(Exception):
    """A problem with the repository contents (exit 1)."""


def report_error(message):
    # stderr in both cases: callers redirect stdout (the plugin list) to a file.
    prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") == "true" else "error: "
    print(prefix + message, file=sys.stderr)


def find_plugins(root):
    """Return plugin directories in a stable order: engine, then packs, then labs."""
    found = []
    engine = root / "engine"
    if engine.is_dir():
        found.append(engine)
    for parent in PLUGIN_PARENTS:
        base = root / parent
        if base.is_dir():
            children = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
            found.extend(sorted(children, key=lambda p: p.name))
    return found


def relative(root, path):
    return path.relative_to(root).as_posix()


def manifest_problems(root, plugin_dir):
    rel = relative(root, plugin_dir)
    if plugin_dir.is_symlink():
        return ["%s is a symbolic link; plugin directories must be real folders" % rel]
    if not (plugin_dir / MANIFEST).is_file():
        return ["%s has no %s" % (rel, MANIFEST.as_posix())]
    return []


def cmd_list(args):
    root = args.root
    problems = []
    for plugin_dir in find_plugins(root):
        print(relative(root, plugin_dir))
        if args.require_manifest:
            problems.extend(manifest_problems(root, plugin_dir))
    for problem in problems:
        report_error(problem)
    return 1 if problems else 0


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise CheckError("git %s failed: %s" % (" ".join(args), detail))
    return result.stdout


def commit_epoch(root):
    override = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if override:
        if not override.isdigit():
            raise CheckError("SOURCE_DATE_EPOCH must be a whole number of seconds")
        return int(override)
    return int(git(root, "log", "-1", "--format=%ct", "HEAD").decode().strip())


def read_manifest(root, plugin_dir):
    path = plugin_dir / MANIFEST
    rel = relative(root, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError("cannot read %s: %s" % (rel, exc))
    if not isinstance(data, dict):
        raise CheckError("%s must hold a JSON object" % rel)
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise CheckError("%s: name must be kebab-case (lowercase letters, digits, hyphens)" % rel)
    version = data.get("version")
    if version is not None and (not isinstance(version, str) or not VERSION_RE.match(version)):
        raise CheckError("%s: version must be a semantic version such as 1.2.3" % rel)
    return name, version


def committed_files(root, plugin_dir):
    """Return (path inside the plugin, git mode, blob id) for each file committed at HEAD."""
    prefix = relative(root, plugin_dir) + "/"
    listing = git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", prefix)
    files = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        mode, _kind, blob = meta.decode("ascii").split(" ")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            raise CheckError("%r under %s is not a UTF-8 file name" % (raw_path, prefix))
        if mode not in (MODE_FILE, MODE_EXECUTABLE):
            what = "a symbolic link" if mode == "120000" else "a submodule or special entry"
            raise CheckError("%s is %s; plugins may hold regular files only" % (path, what))
        files.append((path[len(prefix):], mode, blob))
    return sorted(files)


def write_zip(root, archive, top, files, date_time):
    with zipfile.ZipFile(archive, "w") as zf:
        for inner_path, mode, blob in files:
            info = zipfile.ZipInfo(top + "/" + inner_path, date_time=date_time)
            info.create_system = 3  # Unix, so unzip honours the permission bits
            permissions = 0o755 if mode == MODE_EXECUTABLE else 0o644
            info.external_attr = (0o100000 | permissions) << 16
            data = git(root, "cat-file", "blob", blob)
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changelog_section(root, version):
    """Return the body of the CHANGELOG.md section for version, or None."""
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return None
    heading = re.compile(r"^##\s+\[?v?" + re.escape(version) + r"\]?(?:\s|$)")
    lines = path.read_text(encoding="utf-8").splitlines()
    body = None
    for line in lines:
        if body is None:
            if heading.match(line):
                body = []
        elif line.startswith("## "):
            break
        elif not LINK_DEFINITION_RE.match(line):
            body.append(line)
    if body is None:
        return None
    text = "\n".join(body).strip()
    return text or None


def release_notes(root, tag, version, rows):
    repo = os.environ.get("GITHUB_REPOSITORY") or "OWNER/REPO"
    section = changelog_section(root, version)
    out = ["## Changes", ""]
    if section:
        out.append(section)
    else:
        out.append("No CHANGELOG.md section for %s; see the commits since the previous release." % version)
    out += ["", "## Assets", "", "| Plugin | Version | File | SHA-256 |", "| --- | --- | --- | --- |"]
    for name, plugin_version, filename, digest in rows:
        out.append("| %s | %s | `%s` | `%s` |" % (name, plugin_version, filename, digest))
    out += [
        "",
        "## Verify",
        "",
        "Every asset, `SHA256SUMS` included, has a build provenance attestation from this",
        "repository's release workflow, and the release itself is immutable.",
        "",
        "```sh",
        "gh release download %s --repo %s" % (tag, repo),
        "sha256sum --check SHA256SUMS",
        "gh release verify %s --repo %s" % (tag, repo),
        "gh attestation verify %s --repo %s" % (rows[0][2] if rows else "FILE.zip", repo),
        "```",
        "",
    ]
    return "\n".join(out)


def cmd_package(args):
    root = args.root
    match = TAG_RE.match(args.tag)
    if not match:
        raise CheckError("tag %r is not vMAJOR.MINOR.PATCH, optionally with a -prerelease suffix" % args.tag)
    release_version = match.group(1)

    plugins = find_plugins(root)
    if not plugins:
        raise CheckError("no plugin directories (engine/, packs/*/, labs/*/); nothing to release")
    problems = [p for d in plugins for p in manifest_problems(root, d)]
    if problems:
        raise CheckError("; ".join(problems))

    epoch = max(commit_epoch(root), ZIP_EARLIEST)
    date_time = time.gmtime(epoch)[:6]
    out_dir = args.out if args.out.is_absolute() else Path.cwd() / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    seen = {}
    for plugin_dir in plugins:
        name, version = read_manifest(root, plugin_dir)
        if name in seen:
            raise CheckError("%s and %s both use the plugin name %r" % (seen[name], relative(root, plugin_dir), name))
        seen[name] = relative(root, plugin_dir)
        files = committed_files(root, plugin_dir)
        if not files:
            raise CheckError("%s has no committed files at HEAD" % relative(root, plugin_dir))
        plugin_version = version or release_version
        filename = "%s-%s.zip" % (name, plugin_version)
        archive = out_dir / filename
        write_zip(root, archive, name, files, date_time)
        digest = sha256_of(archive)
        rows.append((name, plugin_version, filename, digest))
        print("%s  %s  (%s, %d files)" % (digest, filename, seen[name], len(files)))

    rows.sort(key=lambda row: row[2])
    sums = "".join("%s  %s\n" % (digest, filename) for _, _, filename, digest in rows)
    (out_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    print("wrote %d archives and SHA256SUMS to %s" % (len(rows), out_dir))
    if args.notes:
        args.notes.write_text(release_notes(root, args.tag, release_version, rows), encoding="utf-8")
        print("wrote release notes to %s" % args.notes)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="plugins.py",
        description="Find the repository's Claude Code plugins and package them for a release.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    sub = parser.add_subparsers(dest="command")

    listing = sub.add_parser("list", help="print plugin directories, one per line")
    listing.add_argument("--require-manifest", action="store_true",
                         help="exit 1 if a plugin directory has no .claude-plugin/plugin.json")
    listing.set_defaults(func=cmd_list)

    package = sub.add_parser("package", help="write one zip per plugin plus SHA256SUMS")
    package.add_argument("--tag", required=True, help="release tag, for example v1.2.3")
    package.add_argument("--out", type=Path, required=True, help="output directory")
    package.add_argument("--notes", type=Path, help="also write release notes (Markdown) to this file")
    package.set_defaults(func=cmd_package)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_usage(sys.stderr)
        return 2
    args.root = args.root.resolve()
    try:
        return args.func(args)
    except CheckError as exc:
        report_error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
