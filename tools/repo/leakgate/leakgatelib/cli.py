"""Command-line interface. Exit codes: 0 clean, 1 findings, 2 usage or config
error (including a file that couldn't be read, and any internal error:
leakgate never exits 1 unless it found something)."""

import argparse
import json
import os
import sys
import tempfile
import traceback
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .bench import DEFAULT_CORPUS, run_bench
from .config import load_config
from .errors import ConfigError, LeakgateError, UsageError
from .scanner import MODES, Finding, ScanReport, scan_report
from .terms import create_salt_file, hash_specs, read_salt_file, read_term_file

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
JSON_SCHEMA_VERSION = 1
README_HINT = "tools/repo/leakgate/README.md"


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors already; keep that, add a hint."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, "leakgate: error: %s\n" % message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="leakgate.py",
        description="Stop private material from leaving this repository.",
        epilog="Exit codes: 0 clean, 1 findings, 2 usage or config error. Docs: " + README_HINT,
    )
    parser.add_argument("--version", action="version", version="leakgate " + __version__)
    commands = parser.add_subparsers(dest="command", metavar="{check,hash-denylist,bench}")
    commands.required = True

    check = commands.add_parser(
        "check",
        help="scan files and directories for leaks",
        description="Scan files and directories. Directories inside a git work tree contribute the "
        "files git would publish (tracked, plus untracked files that aren't ignored).",
    )
    check.add_argument("paths", nargs="+", metavar="PATH", help="files or directories to scan")
    check.add_argument("--config", metavar="FILE", help="rules file (default: rules.json next to leakgate.py)")
    check.add_argument("--denylist", metavar="FILE", action="append", default=[], help="plaintext term list; repeatable")
    check.add_argument("--hashed-denylist", metavar="FILE", action="append", default=[], help="published list of salted term hashes; repeatable")
    check.add_argument("--salt-file", metavar="FILE", help="salt for hashed lists (else $LEAKGATE_SALT)")
    check.add_argument("--json", action="store_true", help="print one JSON document on stdout")
    check.add_argument("--all-files", action="store_true", help="scan every file under directories, ignoring git")
    check.add_argument("--exclude", metavar="GLOB", action="append", default=[], help="skip paths matching GLOB (relative to the root); repeatable")
    check.add_argument("--root", metavar="DIR", help="root that relative paths and globs are measured from")
    check.add_argument("--mode", choices=MODES, default="check", help="allowlist mode (default: check)")
    check.add_argument("--show-allowed", action="store_true", help="also list findings an allow entry covers")
    check.add_argument("--context", metavar="N", type=int, default=0, help="show N characters of masked context (default 0)")
    check.add_argument("--require-env-denylist", action="store_true", help="fail (exit 2) when $LEAKGATE_DENYLIST is empty")

    hashing = commands.add_parser(
        "hash-denylist",
        help="hash a plaintext term list for publication",
        description="Write a JSON list of salted SHA-256 hashes of normalized terms. "
        "The output is safe to commit; keep the salt and the plaintext private.",
    )
    hashing.add_argument("plain", metavar="PLAIN.txt", help="plaintext term list")
    hashing.add_argument("--salt-file", metavar="FILE", required=True, help="file holding the salt")
    hashing.add_argument("--create-salt", action="store_true", help="create the salt file (owner-only permissions) if it doesn't exist")
    hashing.add_argument("--out", metavar="FILE", help="write here instead of stdout")

    bench = commands.add_parser(
        "bench",
        help="measure recall on the planted corpus and false blocks on the clean corpus",
        description="Prints recall=<caught>/<planted> and false_blocks=<n>. Exit 0 only when every "
        "planted leak is caught, nothing unexpected fires, and the clean corpus is clean.",
    )
    bench.add_argument("--corpus", metavar="DIR", help="corpus directory (default: fixtures/leak-corpus)")
    bench.add_argument("--json", action="store_true", help="print one JSON document on stdout")
    return parser


# --------------------------------------------------------------------------
# Output helpers


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write("leakgate: " + text + "\n")


def _plural(count: int, word: str) -> str:
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def _finding_line(finding: Finding) -> str:
    line = "%s:%d:%d: %s" % (finding.path, finding.line, finding.col, finding.excerpt)
    if finding.allowed_by:
        line += " (allowed by %s)" % ", ".join(finding.allowed_by)
    return line


def _json_dump(document: Dict[str, Any]) -> None:
    _out(json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False))


def _base_document(command: str) -> Dict[str, Any]:
    return {"tool": "leakgate", "version": __version__, "schema_version": JSON_SCHEMA_VERSION, "command": command}


def _report_document(report: ScanReport, show_allowed: bool) -> Dict[str, Any]:
    document = _base_document("check")
    document.update(
        {
            "mode": report.mode,
            "exit_code": report.exit_code,
            "summary": {
                "files_scanned": report.files_scanned,
                "files_binary": report.files_binary,
                "findings": len(report.findings),
                "files_with_findings": report.files_with_findings,
                "allowed": len(report.allowed),
            },
            "findings": [finding.to_dict() for finding in report.findings],
        }
    )
    if show_allowed:
        document["allowed_findings"] = [finding.to_dict(with_allowed_by=True) for finding in report.allowed]
    document["warnings"] = list(report.warnings)
    document["errors"] = list(report.errors)
    return document


def _error_document(command: str, message: str) -> Dict[str, Any]:
    document = _base_document(command)
    document.update({"exit_code": EXIT_ERROR, "errors": [message]})
    return document


# --------------------------------------------------------------------------
# Commands


def _cmd_check(args: argparse.Namespace) -> int:
    config = load_config(
        args.config,
        denylist=tuple(args.denylist),
        hashed_denylist=tuple(args.hashed_denylist),
        salt_file=args.salt_file,
    )
    if args.require_env_denylist and config.env_term_count == 0:
        raise ConfigError("--require-env-denylist: $%s is unset or holds no terms" % config.env_variable)
    if args.context < 0:
        raise UsageError("--context must be 0 or more")
    report = scan_report(
        args.paths,
        config,
        root=args.root,
        mode=args.mode,
        all_files=args.all_files,
        exclude=args.exclude,
        context=args.context,
    )
    if args.json:
        _json_dump(_report_document(report, args.show_allowed))
        return report.exit_code

    listed = sorted(report.findings + (report.allowed if args.show_allowed else []), key=Finding.key)
    for finding in listed:
        _out(_finding_line(finding))
    for warning in report.warnings:
        _err("warning: " + warning)
    for error in report.errors:
        _err("error: " + error)
    scanned = "%s scanned" % _plural(report.files_scanned, "file")
    allowed = ", %d allowed" % len(report.allowed) if report.allowed else ""
    if report.findings:
        _err(
            "%s in %s (%s%s)"
            % (_plural(len(report.findings), "finding"), _plural(report.files_with_findings, "file"), scanned, allowed)
        )
        _err("remove the material, or add a scoped allow entry with a reason (see %s)" % README_HINT)
    elif not report.errors:
        _err("no findings (%s%s)" % (scanned, allowed))
    if report.errors:
        _err("failing closed: files that can't be read are never treated as clean")
    return report.exit_code


def _atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temp = tempfile.mkstemp(prefix=".leakgate-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def _cmd_hash(args: argparse.Namespace) -> int:
    if args.create_salt and not os.path.exists(args.salt_file):
        create_salt_file(args.salt_file)
        _err("created salt file %s (keep it private; store the same value in the LEAKGATE_SALT secret)" % args.salt_file)
    salt = read_salt_file(args.salt_file)
    specs = read_term_file(args.plain, args.plain)
    if not specs:
        raise ConfigError("%s: no terms to hash" % args.plain)
    digests = hash_specs(specs, salt)
    text = json.dumps(digests, indent=2) + "\n"
    if args.out:
        _atomic_write(args.out, text)
        destination = args.out
    else:
        sys.stdout.write(text)
        destination = "stdout"
    sensitive = sum(1 for spec in specs if spec.case_sensitive)
    _err(
        "hashed %s (%d case-sensitive) into %s -> %s"
        % (_plural(len(specs), "term"), sensitive, _plural(len(digests), "digest"), destination)
    )
    return EXIT_CLEAN


def _cmd_bench(args: argparse.Namespace) -> int:
    corpus = args.corpus or DEFAULT_CORPUS
    result = run_bench(corpus)
    code = EXIT_CLEAN if result.ok else EXIT_FINDINGS
    if args.json:
        document = _base_document("bench")
        document.update(result.to_dict())
        document["exit_code"] = code
        _json_dump(document)
        return code

    _out("planted: %s, %d planted findings: %d caught, %d missed, %d unexpected" % (
        _plural(result.planted_files, "file"), result.planted, result.caught, len(result.missed), len(result.unexpected)))
    _out("coverage: " + ", ".join("%s %d" % item for item in sorted(result.coverage.items())))
    _out("clean: %s, %s" % (_plural(result.clean_files, "file"), _plural(len(result.false_blocks), "false block")))
    for path, line, rule_id in result.missed:
        _out("  missed: %s:%d %s" % (path, line, rule_id))
    for finding in result.unexpected:
        _out("  unexpected: planted/" + _finding_line(finding))
    for finding in result.false_blocks:
        _out("  false block: clean/" + _finding_line(finding))
    for warning in result.warnings:
        _err("warning: " + warning)
    _out("recall=%d/%d" % (result.caught, result.planted))
    _out("false_blocks=%d" % len(result.false_blocks))
    return code


_COMMANDS = {"check": _cmd_check, "hash-denylist": _cmd_hash, "bench": _cmd_bench}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    wants_json = bool(getattr(args, "json", False))
    try:
        return _COMMANDS[args.command](args)
    except LeakgateError as exc:
        if wants_json:
            _json_dump(_error_document(args.command, str(exc)))
        _err("error: %s" % exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err("interrupted")
        return EXIT_ERROR
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return EXIT_ERROR
    except Exception as exc:  # never let a crash look like "findings" (exit 1)
        if os.environ.get("LEAKGATE_DEBUG"):
            traceback.print_exc()
        message = "internal error: %s: %s (set LEAKGATE_DEBUG=1 for a traceback)" % (type(exc).__name__, exc)
        if wants_json:
            _json_dump(_error_document(args.command, message))
        _err(message)
        return EXIT_ERROR
