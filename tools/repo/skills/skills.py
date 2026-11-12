#!/usr/bin/env python3
"""Skills toolchain: lint, registry, scaffolding and eval-result redaction.

Usage (from the repository root)::

    python3 tools/repo/skills/skills.py lint packs labs
    python3 tools/repo/skills/skills.py registry [--check]
    python3 tools/repo/skills/skills.py new <pack> <skill>
    python3 tools/repo/skills/skills.py redact-results <aggregate-result.json> --out <summary.json>
    python3 tools/repo/skills/skills.py plugins [--changed-files FILE]

Exit codes: 0 when everything is fine, 1 when there are problems (lint
errors, a stale registry, a refused redaction), 2 for usage errors.

The frontmatter contract, the YAML subset, the eval layout, the stable rule
and the summary format are documented in ``CONTRACT.md`` next to this file.
Standard-library Python 3.9+; no network calls, no model calls.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skillslib import lint as lint_mod  # noqa: E402
from skillslib import registry as registry_mod  # noqa: E402
from skillslib.model import discover_plugin_dirs, MANIFEST  # noqa: E402
from skillslib.redact import RedactError, markdown_table, redact_file  # noqa: E402
from skillslib.scaffold import ScaffoldError, scaffold  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2


def _display(path: Path) -> str:
    """A path relative to the working directory when it is inside it."""
    try:
        relative = os.path.relpath(str(path))
    except ValueError:  # a different drive on Windows
        return str(path)
    return str(path) if relative.startswith("..") else Path(relative).as_posix()


def _usage_error(message: str) -> int:
    sys.stderr.write("skills.py: {}\n".format(message))
    return EXIT_USAGE


def cmd_lint(args: argparse.Namespace) -> int:
    if args.list_rules:
        for code, (severity, summary) in lint_mod.RULES.items():
            print("{}  {:<7}  {}".format(code, severity, summary))
        return EXIT_OK
    roots = [Path(r) for r in args.roots] if args.roots else registry_mod.plugin_roots(REPO_ROOT)
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        return _usage_error("lint: not a directory: {}".format(", ".join(str(m) for m in missing)))
    result = lint_mod.lint_roots(roots)
    for problem in result.problems:
        location = _display(problem.path) + (":{}".format(problem.line) if problem.line else "")
        print("{}: {} {} {}".format(location, problem.severity, problem.code, problem.message))
    print(
        "lint: {} plugin(s), {} skill(s), {} eval case(s), {} result summary(ies): {} error(s), {} warning(s)".format(
            result.plugins, result.skills, result.cases, result.summaries, result.errors, result.warnings
        )
    )
    failed = result.errors > 0 or (args.strict and result.warnings > 0)
    return EXIT_PROBLEMS if failed else EXIT_OK


def cmd_registry(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out = Path(args.out).resolve() if args.out else None
    try:
        path, text = registry_mod.generate(root, out)
    except registry_mod.RegistryError as exc:
        sys.stderr.write("registry: {}\n".format(exc))
        return EXIT_PROBLEMS
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if args.check:
        if current == text:
            print("registry: {} is up to date".format(_display(path)))
            return EXIT_OK
        reason = "is missing" if current is None else "is stale"
        sys.stderr.write("registry: {} {}; run `{}` and commit the result\n".format(_display(path), reason, registry_mod.GENERATOR))
        sys.stderr.write(registry_mod.diff(current, text, path))
        return EXIT_PROBLEMS
    if current == text:
        print("registry: {} is already up to date".format(_display(path)))
        return EXIT_OK
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("registry: wrote {}".format(_display(path)))
    return EXIT_OK


def cmd_new(args: argparse.Namespace) -> int:
    try:
        created = scaffold(
            Path(args.root).resolve(), args.pack, args.skill, labs=args.labs, roles=args.roles, min_tier=args.min_tier
        )
    except ScaffoldError as exc:
        sys.stderr.write("new: {}\n".format(exc))
        return EXIT_PROBLEMS
    for path in created:
        print("created {}".format(_display(path)))
    print("next: replace the TODO placeholders, add input files under the case's files/ directory, then run lint")
    return EXIT_OK


def cmd_redact(args: argparse.Namespace) -> int:
    try:
        summary, _ = redact_file(
            Path(args.aggregate),
            Path(args.out),
            date=args.date,
            plugin_dir=Path(args.plugin_dir) if args.plugin_dir else None,
        )
    except RedactError as exc:
        sys.stderr.write("redact-results: {}\n".format(exc))
        return EXIT_PROBLEMS
    sys.stdout.write(markdown_table(summary))
    print("redact-results: wrote {}".format(_display(Path(args.out))))
    return EXIT_OK


def _plugin_dirs(root: Path) -> List[Path]:
    """The engine plugin first (its skills have eval cases too), then packs and labs."""
    dirs = [root / "engine"] if (root / "engine" / MANIFEST).is_file() else []
    for root_dir in registry_mod.plugin_roots(root):
        dirs.extend(d for d in discover_plugin_dirs(root_dir) if (d / MANIFEST).is_file())
    return dirs


def cmd_plugins(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    dirs = _plugin_dirs(root)
    if args.changed_files is not None:
        if args.changed_files == "-":
            lines = sys.stdin.read().splitlines()
        else:
            lines = Path(args.changed_files).read_text(encoding="utf-8").splitlines()
        changed = [Path(line.strip()).as_posix() for line in lines if line.strip()]
        selected = []
        for plugin_dir in dirs:
            prefix = plugin_dir.relative_to(root).as_posix() + "/"
            if any(path.startswith(prefix) for path in changed):
                selected.append(plugin_dir)
        dirs = selected
    for plugin_dir in dirs:
        print(plugin_dir.relative_to(root).as_posix())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skills.py",
        description="Lint skills, generate REGISTRY.md, scaffold skills and redact eval results.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    lint = sub.add_parser("lint", help="check every skill, plugin, eval case and summary under the given roots")
    lint.add_argument("roots", nargs="*", help="plugin roots such as packs labs (default: both, from the repo root)")
    lint.add_argument("--strict", action="store_true", help="treat warnings as failures")
    lint.add_argument("--list-rules", action="store_true", help="print every rule code and exit")
    lint.set_defaults(func=cmd_lint)

    registry = sub.add_parser("registry", help="write REGISTRY.md, or check that it is up to date")
    registry.add_argument("--check", action="store_true", help="exit 1 when REGISTRY.md is stale, without writing")
    registry.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this checkout)")
    registry.add_argument("--out", help="registry path (default: <root>/REGISTRY.md)")
    registry.set_defaults(func=cmd_registry)

    new = sub.add_parser("new", help="scaffold a skill and one eval case in the claude plugin eval layout")
    new.add_argument("pack", help="pack folder under packs/ (or labs/ with --labs)")
    new.add_argument("skill", help="skill name: lowercase letters, digits and hyphens")
    new.add_argument("--labs", action="store_true", help="scaffold into labs/<pack> instead of packs/<pack>")
    new.add_argument("--roles", default="builder", help="comma-separated role ids (default: builder)")
    new.add_argument("--min-tier", default="mid", choices=lint_mod.TIERS, help="lowest model tier the skill supports")
    new.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this checkout)")
    new.set_defaults(func=cmd_new)

    redact = sub.add_parser("redact-results", help="turn aggregate-result.json into a committable summary.json")
    redact.add_argument("aggregate", help="aggregate-result.json written by claude plugin eval --json")
    redact.add_argument("--out", required=True, help="where to write summary.json")
    redact.add_argument("--date", help="run date, YYYY-MM-DD (default: from the result's startedAt)")
    redact.add_argument("--plugin-dir", help="plugin directory, to attribute cases to skills by name when needed")
    redact.set_defaults(func=cmd_redact)

    plugins = sub.add_parser("plugins", help="list plugin directories under packs/ and labs/")
    plugins.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this checkout)")
    plugins.add_argument(
        "--changed-files",
        metavar="FILE",
        help="only plugins containing a path listed in FILE (one per line, relative to the root; - reads stdin)",
    )
    plugins.set_defaults(func=cmd_plugins)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
