#!/usr/bin/env python3
"""PreToolUse hook: apply the write rules to shell commands.

Source: adapters/templates/engine/tools/protect_shell.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

``protect_paths.py`` guards the Write and Edit tools. Without this hook a role
that has Bash could change a protected file with ``echo x > os.config.json``.
Claude Code runs this before every Bash or PowerShell call and sends the
documented PreToolUse JSON on stdin. It works out which files the command
would write and applies the same three rules as ``protect_paths.py``:

1. ``protected_paths`` in ``os.config.json``: nobody writes them.
2. Role write scopes: a role that has a scope writes only inside it.
3. The second brain's ``KB/``: no direct writes; ``bin/kb write put`` proposes.

It then applies ``shell_guard`` from ``os.config.json``::

    "shell_guard": {
      "deny": ["\\\\bsudo\\\\b"],
      "ask":  ["\\\\bgit\\\\s+push\\\\b"]
    }

Each entry is a regular expression matched against the whole command. A
``deny`` match blocks the command. An ``ask`` match hands the decision to the
owner: Claude Code shows them the command and waits for a yes.

What counts as a write target:

* redirections: ``>``, ``>>``, ``&>``, ``2>`` and the like;
* the arguments of commands that change files: ``rm``, ``mv``, ``cp`` (its
  destination, or each source's name inside it when it is a folder), ``tee``,
  ``touch``, ``mkdir``, ``chmod``, ``chown``, ``ln``, ``truncate``,
  ``install``, ``rsync`` (its destination), ``dd of=``,
  ``sed -i``, ``perl -i``, and ``git rm|mv|checkout|restore|clean``;
* writing into, removing, moving or reverting a folder that holds a protected
  file (``cp x profile/``, ``rm -rf profile``, ``git checkout -- .``);
* wildcards, expanded in the command's folder (``rm -f *.json``);
* ``curl -o``, ``wget -O``, ``find -delete`` and ``-exec``, ``tar x``,
  ``unzip``, ``patch``, ``git apply``, ``git reset --hard`` and ``git stash``;
* the same inside ``sh -c``, ``bash -c``, ``eval``, ``$(...)`` and backticks.

Inline programs (``python -c``, ``node -e``, ``perl -e``, ``ruby -e``) can't
be read reliably, so they are denied when they name a protected path, and
always denied for a role that has a write scope. A command that can't be
parsed is treated the same way. Reading a protected file (``cat``, ``grep``)
is always allowed.

THIS IS A GUARD, NOT A SANDBOX. It stops the direct and the careless routes.
A command that builds a path from variables, or a script written to an allowed
place and then run, can get past it. Roles that must never write outside
their scope should not have Bash at all, which is how the generated roles are
set up; a container or the harness's own sandbox is the hard boundary.

Output follows the hooks reference: silence allows; a ``permissionDecision``
of ``deny`` or ``ask`` with a reason otherwise. Anything it cannot evaluate
(bad input, unreadable config) is denied, so it fails closed.
"""

import argparse
import os
import re
import shlex
import sys
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)
import protect_paths  # noqa: E402
import ledger  # noqa: E402

SHELL_TOOLS = ("Bash", "PowerShell")
SEPARATORS = (";", "&&", "||", "|", "|&", "&", "(", ")", "\n")
WRAPPERS = ("sudo", "command", "env", "nohup", "time", "nice", "exec", "builtin", "xargs", "doas", "stdbuf", "timeout")
# Options of a wrapper that take a value, so the value isn't read as the program.
WRAPPER_VALUES = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t", "-T"}, "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}, "nice": {"-n", "--adjustment"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--arg-file"},
    "time": {"-o", "-f", "--output", "--format"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
}
# Every non-option argument is a file the command changes.
MUTATORS = ("rm", "rmdir", "unlink", "touch", "mkdir", "chmod", "chown", "chgrp", "truncate", "tee", "shred", "mkfifo")
# The last argument (or -t DIR) is written; into it, when it is a folder.
DEST_LAST = ("cp", "install", "rsync", "scp", "ln", "mv")
# git subcommands that change files in the working tree, and what they touch.
GIT_PATHS = ("rm", "mv", "checkout", "restore", "clean")
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SHELLS = ("sh", "bash", "zsh", "dash", "ksh")
INLINE = {"python": "-c", "python3": "-c", "node": "-e", "perl": "-e", "ruby": "-e", "php": "-r", "osascript": "-e"}
MAX_EXPAND = 5000
POWERSHELL_WRITERS = re.compile(
    r"(?i)\b(set-content|add-content|out-file|remove-item|move-item|copy-item|new-item|clear-content|rename-item|"
    r"del|erase|rd|ri|mi|ci|ni|sc|ac)\b|>"
)
_REDIRECT = re.compile(r"^(?:\d*|&)>>?\|?(.*)$")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_UNRESOLVED = re.compile(r"[$*?`\[\]{}]")


class Unparsed(Exception):
    """The command can't be split into words."""


def split_commands(command: str) -> List[List[str]]:
    """Simple commands as word lists. Raises Unparsed."""
    try:
        # A newline ends a command, like ";". Inside quotes this only over-splits, which is the safe side.
        flat = command.replace("\\\n", " ").replace("\n", " ; ")
        lexer = shlex.shlex(flat, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise Unparsed(str(exc))
    commands: List[List[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _program(words: List[str]) -> Tuple[str, List[str]]:
    """The program name (wrappers, their options and VAR=value prefixes skipped) and its arguments."""
    index = 0
    while index < len(words):
        word = words[index]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            index += 1
            continue
        name = os.path.basename(word)
        if name not in WRAPPERS:
            break
        index += 1
        values = WRAPPER_VALUES.get(name, set())
        while index < len(words) and words[index].startswith("-") and words[index] != "-":
            index += 2 if words[index] in values else 1
        if name == "timeout" and index < len(words) and re.fullmatch(r"[0-9.]+[smhd]?", words[index]):
            index += 1
    if index >= len(words):
        return "", []
    return os.path.basename(words[index]), words[index + 1:]


def _plain(args: Sequence[str], values: Sequence[str] = ()) -> Tuple[List[str], Dict[str, str]]:
    """Non-option arguments, and the values of the options in ``values`` (``-t DIR`` or ``--x=DIR``)."""
    plain, found = [], {}  # type: List[str], Dict[str, str]
    index, ended = 0, False
    while index < len(args):
        arg = args[index]
        if ended or not arg.startswith("-") or arg == "-":
            plain.append(arg)
        elif arg == "--":
            ended = True
        elif "=" in arg and arg.split("=", 1)[0] in values:
            found[arg.split("=", 1)[0]] = arg.split("=", 1)[1]
        elif arg in values:
            if index + 1 < len(args):
                found[arg] = args[index + 1]
            index += 1
        index += 1
    return plain, found


def _join(cwd: str, raw: str) -> str:
    path = os.path.expanduser(raw)
    return path if protect_paths.is_absolute(path) else os.path.join(cwd, path)


def _into(program: str, args: Sequence[str], cwd: str) -> List[str]:
    """What cp, mv, install, ln, rsync or scp write: the destination, or each source's name inside it."""
    plain, opts = _plain(args, ("-t", "--target-directory", "-S", "--suffix", "-m", "--mode", "-o", "--owner",
                                "-g", "--group", "-e", "--rsh", "--exclude", "--include", "--backup-dir"))
    target_dir = opts.get("-t") or opts.get("--target-directory")
    if target_dir is not None:
        dest, sources = target_dir, plain
    elif plain:
        dest, sources = plain[-1], plain[:-1]
    else:
        return []
    out = list(sources) if program == "mv" else []  # mv also removes its sources
    no_target_dir = "-T" in args or "--no-target-directory" in args
    if no_target_dir or not (target_dir is not None or dest.endswith("/") or os.path.isdir(_join(cwd, dest))):
        return out + [dest]
    for source in sources or [""]:
        name = os.path.basename(source.rstrip("/"))
        if not name or name in (".", "..") or (program == "rsync" and source.endswith("/")):
            out.append(dest)  # the contents of the source land in dest itself
        else:
            out.append(os.path.join(dest, name))
    return out


def _patch_paths(text: str, strip: int) -> List[str]:
    paths = []
    for line in text.splitlines():
        if line.startswith(("+++ ", "--- ", "rename to ", "copy to ")):
            path = line.split(" ", 2)[-1] if line.startswith(("rename", "copy")) else line[4:]
            path = path.split("\t", 1)[0].strip()
            if path and path != "/dev/null":
                parts = path.split("/")
                paths.append("/".join(parts[strip:]) if len(parts) > strip else parts[-1])
    return paths


def _read_small(path: str) -> Optional[str]:
    try:
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _archive_members(program: str, args: Sequence[str], cwd: str) -> Optional[List[str]]:
    """Paths an extraction would write, or None when the archive can't be listed."""
    import tarfile
    import zipfile
    try:
        if program == "unzip":
            plain, opts = _plain(args, ("-d", "-x", "-P"))
            if not plain:
                return None
            with zipfile.ZipFile(_join(cwd, plain[0])) as archive:
                names = archive.namelist()
            dest = opts.get("-d", "")
        else:
            plain, opts = _plain(args, ("-f", "--file", "-C", "--directory"))
            archive_path = opts.get("-f") or opts.get("--file")
            if archive_path is None and plain and re.fullmatch(r"-?[A-Za-z]*f[A-Za-z]*", args[0].lstrip("-") or "x"):
                archive_path = plain[0] if not args[0].startswith("-") or "f" in args[0] else None
                if archive_path == args[0]:
                    archive_path = plain[1] if len(plain) > 1 else None
            if archive_path is None:
                return None
            with tarfile.open(_join(cwd, archive_path)) as archive:
                names = archive.getnames()
            dest = opts.get("-C") or opts.get("--directory") or ""
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, RuntimeError):
        return None
    return [os.path.join(dest, name) if dest else name for name in names[:MAX_EXPAND]]


def _find_matches(args: Sequence[str], cwd: str) -> List[str]:
    """What a ``find ... -delete`` or ``-exec`` would touch: the matching paths, or the start folders."""
    starts = []
    for arg in args:
        if arg.startswith(("-", "(", "!")):
            break
        starts.append(arg)
    names = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-name", "-iname", "-path", "-ipath", "-regex")]
    if not names:
        return starts or ["."]
    import fnmatch
    found, seen = [], 0
    for start in starts or ["."]:
        base = _join(cwd, start)
        for folder, dirs, files in os.walk(base):
            for name in dirs + files:
                seen += 1
                if seen > MAX_EXPAND:
                    return starts or ["."]  # too big to check one by one: judge the start folders
                if any(fnmatch.fnmatch(name, pattern) for pattern in names):
                    found.append(os.path.relpath(os.path.join(folder, name), cwd))
    return found


def analyse(command: str, cwd: str = ".", depth: int = 0) -> Tuple[List[str], List[str]]:
    """``(write targets, inline programs)`` of a shell command. Raises Unparsed."""
    targets: List[str] = []
    inline: List[str] = []
    if depth > 4:
        raise Unparsed("commands nested too deeply")
    for match in _SUBSTITUTION.finditer(command):
        inner_targets, inner_inline = analyse(match.group(1) or match.group(2) or "", cwd, depth + 1)
        targets += inner_targets
        inline += inner_inline
    for words in split_commands(command):
        kept: List[str] = []
        stdin_file = None
        index = 0
        while index < len(words):
            word = words[index]
            if word in (">", ">>", ">|", "&>", "&>>") or re.fullmatch(r"\d+", word) and index + 1 < len(words) and words[index + 1] in (">", ">>"):
                if re.fullmatch(r"\d+", word):
                    index += 1
                if index + 1 < len(words) and not words[index + 1].startswith("&"):
                    targets.append(words[index + 1])
                index += 2
                continue
            if word in ("<", "<<", "<<<"):
                if word == "<" and index + 1 < len(words):
                    stdin_file = words[index + 1]
                index += 2
                continue
            redirect = _REDIRECT.match(word) if ">" in word and not word.startswith(("-", "=")) else None
            if redirect is not None and redirect.group(1) and not redirect.group(1).startswith("&"):
                targets.append(redirect.group(1))
                index += 1
                continue
            kept.append(word)
            index += 1
        program, args = _program(kept)
        plain = [arg for arg in args if not arg.startswith("-")]
        if program in MUTATORS:
            targets += plain
        elif program in DEST_LAST:
            targets += _into(program, args, cwd)
        elif program == "dd":
            targets += [arg[3:] for arg in args if arg.startswith("of=")]
        elif program in ("sed", "perl") and any(re.match(r"^-[A-Za-z]*i", arg) or arg.startswith("--in-place") for arg in args):
            targets += plain[1:] if program == "sed" and not any(a in ("-e", "-f") or a.startswith("--expression") for a in args) else plain
        elif program == "git":
            targets += _git_targets(args, cwd)
        elif program == "curl":
            plain_c, opts = _plain(args, ("-o", "--output", "-K", "--config", "-d", "--data", "-H", "--header", "-X", "-u", "-A", "-e", "-T", "-F"))
            out = opts.get("-o") or opts.get("--output")
            if out:
                targets.append(out)
            elif any(a in ("-O", "--remote-name") or re.fullmatch(r"-[A-Za-z]*O[A-Za-z]*", a) for a in args):
                targets += [os.path.basename(u.split("?", 1)[0].rstrip("/")) or "index.html" for u in plain_c if "://" in u]
        elif program == "wget":
            plain_w, opts = _plain(args, ("-O", "--output-document", "-P", "--directory-prefix", "-o", "-a", "-i"))
            out = opts.get("-O") or opts.get("--output-document")
            if out and out != "-":
                targets.append(out)
            elif not out:
                prefix = opts.get("-P") or opts.get("--directory-prefix") or ""
                targets += [os.path.join(prefix, os.path.basename(u.split("?", 1)[0].rstrip("/")) or "index.html") for u in plain_w if "://" in u]
        elif program == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint") for a in args):
            targets += _find_matches(args, cwd)
        elif program in ("tar", "bsdtar", "gtar") and args and (re.fullmatch(r"-?[A-Za-z]*x[A-Za-z]*", args[0]) or "--extract" in args or "-x" in args):
            members = _archive_members("tar", args, cwd)
            targets += members if members is not None else ["."]
        elif program == "unzip":
            members = _archive_members("unzip", args, cwd)
            targets += members if members is not None else ["."]
        elif program == "patch":
            plain_p, opts = _plain(args, ("-i", "--input", "-p", "--strip", "-d", "--directory", "-o", "--output"))
            strip = opts.get("-p") or opts.get("--strip") or next((a[2:] for a in args if re.fullmatch(r"-p\d+", a)), "0")
            source = opts.get("-i") or opts.get("--input") or stdin_file
            text = _read_small(_join(cwd, source)) if source else None
            if opts.get("-o") or opts.get("--output"):
                targets.append(opts.get("-o") or opts.get("--output"))
            elif text is None:
                targets.append(plain_p[0] if plain_p else ".")
            else:
                prefix = opts.get("-d") or opts.get("--directory") or ""
                targets += [os.path.join(prefix, path) for path in _patch_paths(text, int(strip) if str(strip).isdigit() else 0)]
        elif program in SHELLS and "-c" in args:
            script = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            inner_targets, inner_inline = analyse(script, cwd, depth + 1)
            targets += inner_targets
            inline += inner_inline
        elif program == "eval":
            inner_targets, inner_inline = analyse(" ".join(args), cwd, depth + 1)
            targets += inner_targets
            inline += inner_inline
        if program in INLINE or re.fullmatch(r"python3?\.\d+", program):
            flag = INLINE.get(program, "-c")
            if flag in args and args.index(flag) + 1 < len(args):
                inline.append(args[args.index(flag) + 1])
    return targets, inline


def _git_targets(args: Sequence[str], cwd: str) -> List[str]:
    """Paths a git command would change in the working tree. ``.`` means all of it."""
    index, work = 0, ""
    while index < len(args) and args[index].startswith("-"):
        option = args[index].split("=", 1)[0]
        if option in GIT_VALUE_OPTIONS and "=" not in args[index]:
            if option == "-C" and index + 1 < len(args):
                work = os.path.join(work, args[index + 1])
            index += 2
        else:
            index += 1
    if index >= len(args):
        return []
    sub, rest = args[index], list(args[index + 1:])
    plain, opts = _plain(rest, ("-m", "--message", "-b", "-B", "--source", "-s", "--conflict", "--pathspec-from-file", "-e", "--exclude"))
    here = lambda paths: [os.path.join(work, p) if work else p for p in paths]  # noqa: E731
    if sub in GIT_PATHS:
        if sub == "clean":
            return here(plain or ["."])
        if sub == "checkout" and "--" not in rest:
            plain = plain[1:] if plain and not os.path.exists(_join(os.path.join(cwd, work), plain[0])) else plain
        if sub == "rm" or sub == "mv":
            return here(plain)
        return here(plain or (["."] if sub == "restore" else []))
    if sub == "reset" and any(a in ("--hard", "--merge", "--keep") for a in rest):
        return here(["."])
    if sub == "stash" and (not plain or plain[0] in ("push", "save", "pop", "apply", "branch")):
        return here(["."])
    if sub in ("apply", "am"):
        files = [p for p in plain if os.path.isfile(_join(cwd, p))]
        texts = [_read_small(_join(cwd, p)) for p in files]
        if "--check" in rest or "--stat" in rest or "--numstat" in rest:
            return []
        if not files or any(text is None for text in texts):
            return here(["."])
        return here([path for text in texts for path in _patch_paths(text or "", 1)])
    return []


def literal_prefixes(patterns: Sequence[str]) -> List[str]:
    """The fixed leading text of each glob, for a plain substring check."""
    found = []
    for pattern in patterns:
        text = re.split(r"[*?\[]", pattern.replace("\\", "/"), 1)[0].strip("/")
        if len(text) >= 3:
            found.append(text)
    return found


def mentions(text: str, literals: Sequence[str]) -> Optional[str]:
    lowered = text.lower().replace("\\", "/")
    for literal in literals:
        if literal.lower() in lowered:
            return literal
    return None


def read_guard(config: Optional[Dict[str, Any]]) -> Tuple[List["re.Pattern[str]"], List["re.Pattern[str]"]]:
    """``(deny, ask)`` patterns from shell_guard. Raises PolicyError."""
    block = (config or {}).get("shell_guard")
    if block is None:
        return [], []
    if not isinstance(block, dict):
        raise protect_paths.PolicyError("os.config.json shell_guard must be an object")
    compiled = []
    for key in ("deny", "ask"):
        entries = block.get(key, [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) and entry for entry in entries):
            raise protect_paths.PolicyError("os.config.json shell_guard.%s must be a list of patterns" % key)
        try:
            compiled.append([re.compile(entry) for entry in entries])
        except re.error as exc:
            raise protect_paths.PolicyError("os.config.json shell_guard.%s has an invalid pattern (%s)" % (key, exc))
    return compiled[0], compiled[1]


def decide(decision: str, reason: str, stdout: TextIO) -> None:
    os_config.emit_json(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                "permissionDecisionReason": reason}},
        stdout,
    )


def check_command(
    command: str, tool: str, cwd: str, project: str, role: Optional[str], protected: Sequence[str],
    scopes: Dict[str, List[str]], brain_kb: Optional[str],
) -> Optional[str]:
    """A deny reason, or None."""
    literals = literal_prefixes(protected)
    scoped = bool(role) and role in scopes
    if tool == "PowerShell":
        hit = mentions(command, literals)
        if hit and POWERSHELL_WRITERS.search(command):
            return "protect_shell: this PowerShell command names the protected path %r and can write. Protected files are changed by the owner, not by agents." % hit
        if scoped and POWERSHELL_WRITERS.search(command):
            return "protect_shell: the %s role has a write scope, and a PowerShell command that writes can't be checked against it. Use the Write or Edit tool." % role
        return None
    try:
        targets, inline = analyse(command, cwd)
    except Unparsed as exc:
        hit = mentions(command, literals)
        if hit or scoped:
            return "protect_shell: this command can't be read reliably (%s)%s. Rewrite it as plain commands." % (
                exc, " and it names the protected path %r" % hit if hit else " and the %s role has a write scope" % role)
        return None
    for program in inline:
        hit = mentions(program, literals)
        if hit:
            return "protect_shell: an inline program names the protected path %r, and inline programs can't be checked. Use the Read tool to read it; protected files are changed by the owner." % hit
        if scoped:
            return "protect_shell: the %s role has a write scope, and an inline program can't be checked against it. Use the Write or Edit tool." % role
    for raw in targets:
        if raw in ("/dev/null", "/dev/stdout", "/dev/stderr", "-"):
            continue
        if re.search(r"[$`{}]", raw):
            hit = mentions(raw, literals)
            if hit:
                return "protect_shell: the command writes to %r, which names the protected path %r." % (raw, hit)
            if scoped:
                return "protect_shell: the %s role has a write scope, and the write target %r can't be resolved. Name the file plainly." % (role, raw)
            continue
        paths = [_join(cwd, raw)]
        if re.search(r"[*?\[]", raw):
            hit = mentions(raw, literals)
            if hit:
                return "protect_shell: the command writes to %r, which names the protected path %r." % (raw, hit)
            import glob
            paths = sorted(set(glob.glob(paths[0]) + glob.glob(os.path.join(os.path.dirname(paths[0]), "." + os.path.basename(paths[0])))))[:MAX_EXPAND]
            if scoped and not paths:
                continue
        for target in paths:
            reason = _check_target(target, raw, project, role, protected, scopes, literals, brain_kb)
            if reason:
                return reason
    return None


def _check_target(target: str, raw: str, project: str, role: Optional[str], protected: Sequence[str],
                  scopes: Dict[str, List[str]], literals: Sequence[str], brain_kb: Optional[str]) -> Optional[str]:
    if brain_kb:
        for path, _ in protect_paths.views(target, project):
            for root in (brain_kb, os.path.realpath(brain_kb)):
                if protect_paths.relative(path, root) is not None:
                    return ("protect_shell: the command writes to %s, inside the second brain's KB/. Agents propose "
                            "changes with bin/kb write put, and only the owner lands them." % raw)
    # A folder that holds a protected path counts as that path: writing into it, removing it or reverting it.
    for path, roots in protect_paths.views(target, project):
        for root in roots:
            rel = protect_paths.relative(path, root)
            if rel is None:
                continue
            for literal in literals:
                if rel == "" or literal.lower() == rel.lower() or literal.lower().startswith(rel.lower() + "/"):
                    return ("protect_shell: the command changes %r, which holds the protected path %r. "
                            "Protected files are changed by the owner, not by agents." % (raw, literal))
    reason = protect_paths.evaluate(target, project, role, protected, scopes)
    if reason:
        return reason.replace("protect_paths:", "protect_shell: the command writes a file it may not.", 1)
    return None


def main(
    argv: Optional[Sequence[str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description="PreToolUse hook that applies the write rules to shell commands.")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    parser.add_argument("--policy", help="role policy file (default: role_policy.json beside this script)")
    args = parser.parse_args(argv)

    try:
        hook_input = os_config.read_hook_input(stdin)
    except os_config.InputError as exc:
        stderr.write("protect_shell: %s; blocking the call.\n" % exc)
        return 2
    tool = hook_input.get("tool_name")
    if tool not in SHELL_TOOLS:
        return 0
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        stderr.write("protect_shell: no command in tool_input; blocking the call.\n")
        return 2
    if not command.strip():
        return 0

    project = os_config.project_root(hook_input, args.root)
    cwd = hook_input.get("cwd") if isinstance(hook_input.get("cwd"), str) else project
    try:
        config, _ = os_config.load_config(project)
        protected, additions = protect_paths.rules_from_config(config)
        scopes = protect_paths.load_policy(args.policy)
        brain = os_config.second_brain(config, project)
        deny_patterns, ask_patterns = read_guard(config)
    except (os_config.ConfigError, protect_paths.PolicyError) as exc:
        decide("deny", "protect_shell: %s; shell commands are blocked until it is fixed." % exc, stdout)
        return 0
    for role, globs in additions.items():
        scopes[role] = list(scopes.get(role, [])) + [g for g in globs if g not in scopes.get(role, [])]

    brain_kb = os.path.join(brain["root"], "KB") if brain is not None else None
    reason = check_command(command, tool, cwd, project, protect_paths.role_of(hook_input.get("agent_type")),
                           protected, scopes, brain_kb)
    if reason:
        decide("deny", reason, stdout)
        ledger.record(project, "denied", hook_input, guard="shell", detail=reason)
        return 0
    for pattern in deny_patterns:
        if pattern.search(command):
            decide("deny", "protect_shell: this command matches %r in os.config.json (shell_guard.deny). "
                           "The owner has ruled it out." % pattern.pattern, stdout)
            ledger.record(project, "denied", hook_input, guard="shell", detail="shell_guard.deny %s" % pattern.pattern)
            return 0
    for pattern in ask_patterns:
        if pattern.search(command):
            decide("ask", "This command matches %r in os.config.json (shell_guard.ask), so it needs the owner's OK." % pattern.pattern, stdout)
            ledger.record(project, "asked", hook_input, detail="shell_guard.ask %s" % pattern.pattern)
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed: a crash must not let the command through
        sys.stderr.write("protect_shell: internal error (%s: %s); blocking the call.\n" % (type(exc).__name__, exc))
        sys.exit(2)
