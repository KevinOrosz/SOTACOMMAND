"""Decide which files a check covers, and the paths rules see for them.

Every scanned file gets a *relative path* (POSIX, from a *root*). Path rules,
allowlist scopes and ``--exclude`` all match against it, so the root matters:

1. ``--root DIR`` when given.
2. Otherwise the git work tree's top level, when the target is inside one.
   ``check internal`` and ``check internal/PLAN.md`` both see
   ``internal/...`` that way.
3. Otherwise the current directory, when the target is inside it.
4. Otherwise the target directory itself (or a file's parent directory).

Which files a directory contributes:

* Inside a git work tree (and without ``--all-files``): the files git would
  publish, meaning tracked files plus untracked files that aren't ignored.
  Untracked files count because they're one ``git add`` away from leaving.
  If every file under the directory is ignored, that's a usage error (exit 2)
  rather than a silent pass, since an ignored temp directory must not look
  clean.
* Outside git, or with ``--all-files``: every file, skipping ``.git``.

A file named explicitly is always scanned, ignored or not. Symlinks are
never followed: git stores a link as its target path, so the target path is
what gets scanned.
"""

import os
import shutil
import stat
import subprocess
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import UsageError
from .globs import GlobSet


class FileTarget(object):
    """One file to scan. ``relpath`` uses ``/`` and is relative to the root."""

    __slots__ = ("abspath", "relpath", "is_symlink")

    def __init__(self, abspath: str, relpath: str, is_symlink: bool) -> None:
        self.abspath = abspath
        self.relpath = relpath
        self.is_symlink = is_symlink

    def __repr__(self) -> str:
        return "FileTarget(%r)" % self.relpath


class Discovery(object):
    """The result of :func:`discover`.

    ``errors`` lists directories that couldn't be read. They make the whole
    check fail (exit 2): a file the gate couldn't see is never clean.
    """

    __slots__ = ("files", "warnings", "errors")

    def __init__(self, files: List[FileTarget], warnings: List[str], errors: List[str]) -> None:
        self.files = files
        self.warnings = warnings
        self.errors = errors


def _git(args: Sequence[str], cwd: str) -> Optional[bytes]:
    """Run a read-only git command; ``None`` when git is missing or fails."""
    if shutil.which("git") is None:
        return None
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"  # never take the index lock while reading
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    try:
        completed = subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def git_toplevel(directory: str) -> Optional[str]:
    out = _git(["rev-parse", "--show-toplevel"], directory)
    if not out:
        return None
    top = out.decode("utf-8", errors="surrogateescape").strip()
    return os.path.realpath(top) if top else None


def _git_publishable(directory: str) -> Optional[List[str]]:
    """Tracked plus untracked-not-ignored files under ``directory``, as paths
    relative to the work tree's top level."""
    out = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--full-name", "--", "."], directory)
    if out is None:
        return None
    names = []
    seen = set()
    for raw in out.split(b"\x00"):
        if not raw:
            continue
        name = raw.decode("utf-8", errors="surrogateescape")
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _git_ignored_count(directory: str) -> int:
    out = _git(["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory", "--", "."], directory)
    if not out:
        return 0
    return sum(1 for raw in out.split(b"\x00") if raw)


def _has_any_file(directory: str) -> bool:
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if any(name != ".git" for name in filenames):
            return True
    return False


def _real_location(path: str) -> str:
    """Resolve symlinks in the parent directories but not in the final name."""
    parent, name = os.path.split(os.path.abspath(path))
    return os.path.join(os.path.realpath(parent), name)


def _is_within(path: str, root: str) -> bool:
    try:
        relative = os.path.relpath(path, root)
    except ValueError:  # different drives on Windows
        return False
    return relative != ".." and not relative.startswith(".." + os.sep) and not os.path.isabs(relative)


def _relpath(abspath: str, root: str) -> str:
    return os.path.relpath(_real_location(abspath), root).replace(os.sep, "/")


def _walk_all(directory: str, errors: List[str]) -> List[str]:
    """Every file under ``directory`` (symlinks included, never followed).
    Unreadable directories are appended to ``errors``."""
    found = []

    def on_error(exc: OSError) -> None:
        errors.append("can't read directory %s: %s" % (exc.filename, exc.strerror or exc))

    for dirpath, dirnames, filenames in os.walk(directory, onerror=on_error, followlinks=False):
        keep = []
        for name in dirnames:
            if name == ".git":
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                found.append(full)
            else:
                keep.append(name)
        dirnames[:] = sorted(keep)
        for name in filenames:
            if name != ".git":
                found.append(os.path.join(dirpath, name))
    return found


def _choose_root(target: str, is_dir: bool, explicit_root: Optional[str], cwd: str) -> Tuple[str, Optional[str]]:
    """Return ``(root, git_toplevel_or_None)`` for one target."""
    probe = target if is_dir else os.path.dirname(target)
    top = git_toplevel(probe)
    if explicit_root is not None:
        return explicit_root, top
    if top is not None:
        return top, top
    real_cwd = os.path.realpath(cwd)
    if _is_within(_real_location(target), real_cwd):
        return real_cwd, None
    return os.path.realpath(target if is_dir else os.path.dirname(target)), None


def discover(
    paths: Iterable[str],
    root: Optional[str] = None,
    all_files: bool = False,
    exclude: Iterable[str] = (),
    cwd: Optional[str] = None,
) -> Discovery:
    """Expand ``paths`` into the files to scan. See the module docstring."""
    cwd = os.path.abspath(cwd or os.getcwd())
    try:
        excluded = GlobSet(list(exclude))
    except ValueError as exc:
        raise UsageError("--exclude: %s" % exc)
    explicit_root = None
    if root is not None:
        explicit_root = os.path.realpath(os.path.join(cwd, root))
        if not os.path.isdir(explicit_root):
            raise UsageError("--root %s is not a directory" % root)

    warnings = []  # type: List[str]
    errors = []  # type: List[str]
    files = {}  # type: Dict[str, FileTarget]
    for given in paths:
        target = os.path.abspath(os.path.join(cwd, given))
        if not os.path.lexists(target):
            raise UsageError("no such file or directory: %s" % given)
        is_dir = os.path.isdir(target) and not os.path.islink(target)
        chosen_root, top = _choose_root(target, is_dir, explicit_root, cwd)
        if not _is_within(_real_location(target), chosen_root) and _real_location(target) != chosen_root:
            raise UsageError("%s is outside --root %s" % (given, root))

        if not is_dir:
            candidates = [target]
        elif top is not None and not all_files:
            names = _git_publishable(os.path.realpath(target))
            if names is None:
                warnings.append("git couldn't list %s; scanning every file under it" % given)
                candidates = _walk_all(target, errors)
            else:
                candidates = [os.path.join(top, *name.split("/")) for name in names]
                ignored = _git_ignored_count(os.path.realpath(target))
                if not candidates and _has_any_file(target):
                    raise UsageError(
                        "every file under %s is ignored by git, so there's nothing to scan; "
                        "pass --all-files to scan them anyway" % given
                    )
                if ignored:
                    warnings.append(
                        "%d git-ignored path(s) under %s were not scanned (--all-files includes them)"
                        % (ignored, given)
                    )
        else:
            candidates = _walk_all(target, errors)

        missing = 0
        for candidate in candidates:
            if not os.path.lexists(candidate):
                missing += 1  # tracked but deleted in the work tree
                continue
            is_link = os.path.islink(candidate)
            if not is_link:
                try:
                    mode = os.stat(candidate).st_mode
                except OSError:
                    mode = 0
                if stat.S_ISDIR(mode):
                    if candidate != target:
                        warnings.append("skipped %s: a directory git lists as one entry (submodule?)" % _relpath(candidate, chosen_root))
                    continue
                if mode and not stat.S_ISREG(mode):
                    warnings.append("skipped %s: not a regular file" % _relpath(candidate, chosen_root))
                    continue
            relpath = _relpath(candidate, chosen_root)
            if excluded and excluded.first_match(relpath) is not None:
                continue
            key = _real_location(candidate)
            if key not in files:
                files[key] = FileTarget(candidate, relpath, is_link)
        if missing:
            warnings.append("%d tracked file(s) under %s are missing from the work tree" % (missing, given))

    ordered = sorted(files.values(), key=lambda item: (item.relpath, item.abspath))
    return Discovery(ordered, warnings, errors)
