#!/usr/bin/env python3
"""Generate harness outputs from harness-neutral role sources.

Usage::

    python3 adapters/generate.py [--roles DIR] [--out DIR] [--check]
    python3 adapters/generate.py --harness codex --target PROJECT_DIR
        [--roles DIR] [--root DIR] [--include-labs] [--force]

The default mode writes, relative to ``--out`` (default: the repository root):

* ``engine/``: the Claude Code plugin (manifest, one agent per role,
  settings, hooks, hook tools, convergence workflow, os-boot skill, templates);
* ``.github/agents/<role>.agent.md``: GitHub Copilot custom agents;
* ``.claude-plugin/marketplace.json``: the engine plus every plugin found
  under ``packs/`` and ``labs/`` in ``--out``. Only the engine and packs whose
  skills are all ``stable`` start enabled.

``--check`` regenerates into a temporary directory and exits 1 when any
committed output differs, is missing, or is no longer generated.

``--harness codex`` installs the pack skills into ``PROJECT_DIR/.agents/skills/``
(as copies) and writes ``PROJECT_DIR/AGENTS.md`` when it is absent.

``--roles`` defaults to ``engine/roles/`` when it exists (the real roles, once
extracted) and to ``fixtures/roles-sample/`` otherwise. The role source
contract is in adapters/ROLES.md.

Exit codes: 0 success; 1 drift (``--check``) or skills skipped (codex);
2 usage, role-source or configuration error.

Standard library only, Python 3.9+. No network or model calls.
"""

import argparse
import os
import sys
from typing import List, Optional, Sequence, Tuple

ADAPTERS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(ADAPTERS_DIR)
if ADAPTERS_DIR not in sys.path:
    sys.path.insert(0, ADAPTERS_DIR)

from osgen import claude, codex, copilot, marketplace, outputs, roles  # noqa: E402

ERRORS = (
    roles.RoleError,
    marketplace.MarketplaceError,
    copilot.CopilotError,
    codex.CodexError,
)


def default_roles_dir(repo_root: str) -> str:
    extracted = os.path.join(repo_root, "engine", "roles")
    if os.path.isdir(extracted):
        return extracted
    return os.path.join(repo_root, "fixtures", "roles-sample")


def build(
    role_list: Sequence[roles.Role], out_dir: str
) -> Tuple[outputs.OutputSet, List[str], List[str]]:
    """All generated files for ``out_dir``.

    Returns (outputs, skipped plugin directories, marketplace notes).
    """
    result = outputs.OutputSet()
    claude.add_engine(result, role_list)
    copilot.add_agents(result, role_list)
    skipped, notes = marketplace.add_marketplace(result, claude.manifest_data(), out_dir)
    return result, skipped, notes


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Generate the engine plugin, Copilot agents and marketplace from role sources.",
    )
    parser.add_argument("--roles", help="role sources directory (roles/<role-id>/soul.md)")
    parser.add_argument("--out", help="output root (default: the repository root)")
    parser.add_argument("--check", action="store_true",
                        help="compare committed outputs with a fresh generation; exit 1 on drift")
    parser.add_argument("--harness", choices=("codex",), help="install for another harness")
    parser.add_argument("--target", help="project directory for --harness codex")
    parser.add_argument("--root", help="repository to read packs from for --harness codex "
                                       "(default: this repository)")
    parser.add_argument("--include-labs", action="store_true",
                        help="--harness codex: also install skills from labs/")
    parser.add_argument("--force", action="store_true",
                        help="--harness codex: replace skill folders this tool did not install")
    args = parser.parse_args(argv)
    if args.harness:
        if not args.target:
            parser.error("--harness codex needs --target PROJECT_DIR")
        if args.check or args.out:
            parser.error("--check and --out apply to generation, not to --harness installs")
    else:
        for flag in ("target", "root"):
            if getattr(args, flag):
                parser.error("--%s needs --harness codex" % flag)
        if args.include_labs or args.force:
            parser.error("--include-labs and --force need --harness codex")
    return args


def run_codex(args: argparse.Namespace) -> int:
    repo_root = os.path.abspath(args.root or REPO_ROOT)
    role_list = roles.load_roles(os.path.abspath(args.roles or default_roles_dir(repo_root)))
    report, skipped = codex.install(
        repo_root, role_list, os.path.abspath(args.target),
        include_labs=args.include_labs, force=args.force,
    )
    for line in report:
        print("codex: %s" % line)
    return 1 if skipped else 0


def run_generate(args: argparse.Namespace) -> int:
    out_dir = os.path.abspath(args.out or REPO_ROOT)
    roles_dir = os.path.abspath(args.roles or default_roles_dir(REPO_ROOT))
    role_list = roles.load_roles(roles_dir)
    result, skipped, notes = build(role_list, out_dir)
    for relative in skipped:
        sys.stderr.write(
            "marketplace: skipped %s (no .claude-plugin/plugin.json yet)\n" % relative
        )
    for note in notes:
        sys.stderr.write("marketplace: %s\n" % note)
    if args.check:
        problems = outputs.check(result, out_dir)
        if problems:
            for line in problems:
                print(line)
            print("check: drift in generated outputs; run python3 adapters/generate.py "
                  "with the same --roles and --out to update them")
            return 1
        print("check: %d generated files match (%d roles)" % (len(result.files), len(role_list)))
        return 0
    written, unchanged, removed = outputs.write(result, out_dir)
    for path in written:
        print("wrote %s" % path)
    for path in removed:
        print("removed %s" % path)
    print("generate: %d written, %d unchanged, %d removed (%d roles)"
          % (len(written), len(unchanged), len(removed), len(role_list)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.harness == "codex":
            return run_codex(args)
        return run_generate(args)
    except ERRORS as exc:
        sys.stderr.write("generate: error: %s\n" % exc)
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write("generate: error: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
