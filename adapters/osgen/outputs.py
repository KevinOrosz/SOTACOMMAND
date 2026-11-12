"""Generated output sets: write them, or check them against committed files.

An ``OutputSet`` maps POSIX-style relative paths to file contents. Some
directories are *owned*: every file in them must be generated, so a file the
generator no longer produces (for example the agent of a deleted role) counts
as drift in ``--check`` and is removed on write.
"""

import difflib
import os
import shutil
import stat
import tempfile
from typing import Dict, Iterable, List, Sequence, Set, Tuple

# Files that tooling drops next to sources; never treated as outputs or drift.
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = (".pyc", ".pyo")
EXECUTABLE_MODE = 0o755
DEFAULT_MODE = 0o644


class OutputSet(object):
    """Files to generate, keyed by relative POSIX path."""

    def __init__(self) -> None:
        self.files: Dict[str, bytes] = {}
        self.executables: Set[str] = set()
        self.owned_dirs: List[str] = []

    def add(self, path: str, content: str, executable: bool = False) -> None:
        if path in self.files:
            raise ValueError("generated twice: %s" % path)
        if path.startswith("/") or ".." in path.split("/") or "\\" in path:
            raise ValueError("output paths must be relative POSIX paths: %s" % path)
        if not content.endswith("\n"):
            content += "\n"
        self.files[path] = content.encode("utf-8")
        if executable:
            self.executables.add(path)

    def own(self, directory: str) -> None:
        """Declare that every file under ``directory`` is generated."""
        self.owned_dirs.append(directory.rstrip("/"))

    def paths(self) -> List[str]:
        return sorted(self.files)


def _ignored(name: str) -> bool:
    return name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES)


def _existing_files(root: str, directory: str) -> List[str]:
    """Relative POSIX paths of files under ``root/directory``, minus tool litter."""
    base = os.path.join(root, *directory.split("/"))
    found: List[str] = []
    if not os.path.isdir(base):
        return found
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not _ignored(d))
        for name in sorted(files):
            if _ignored(name):
                continue
            full = os.path.join(current, name)
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return found


def stale_files(outputs: OutputSet, root: str) -> List[str]:
    """Files in owned directories that the generator does not produce."""
    stale: List[str] = []
    for directory in outputs.owned_dirs:
        for path in _existing_files(root, directory):
            if path not in outputs.files:
                stale.append(path)
    return sorted(set(stale))


def write(outputs: OutputSet, root: str) -> Tuple[List[str], List[str], List[str]]:
    """Write ``outputs`` under ``root``. Returns (written, unchanged, removed)."""
    written: List[str] = []
    unchanged: List[str] = []
    for path in outputs.paths():
        target = os.path.join(root, *path.split("/"))
        content = outputs.files[path]
        if os.path.isfile(target) and _read_bytes(target) == content:
            unchanged.append(path)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
            written.append(path)
        _set_mode(target, path in outputs.executables)
    removed = stale_files(outputs, root)
    for path in removed:
        os.remove(os.path.join(root, *path.split("/")))
    for directory in outputs.owned_dirs:
        _remove_empty_dirs(os.path.join(root, *directory.split("/")))
    return written, unchanged, removed


def _set_mode(target: str, executable: bool) -> None:
    if os.name == "nt":
        return
    wanted = EXECUTABLE_MODE if executable else DEFAULT_MODE
    current = stat.S_IMODE(os.stat(target).st_mode)
    if current != wanted:
        os.chmod(target, wanted)


def _remove_empty_dirs(base: str) -> None:
    if not os.path.isdir(base):
        return
    for current, dirs, files in os.walk(base, topdown=False):
        if current == base:
            continue
        if not os.listdir(current):
            os.rmdir(current)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _normalized(content: bytes) -> bytes:
    # Windows checkouts may carry CRLF line endings; they are not drift.
    return content.replace(b"\r\n", b"\n")


def check(outputs: OutputSet, root: str, diff_lines: int = 40) -> List[str]:
    """Regenerate into a temporary directory and compare with ``root``.

    Returns human-readable drift lines; an empty list means no drift.
    """
    problems: List[str] = []
    with tempfile.TemporaryDirectory(prefix="osgen-check-") as scratch:
        write(outputs, scratch)
        for path in outputs.paths():
            generated = _read_bytes(os.path.join(scratch, *path.split("/")))
            committed_path = os.path.join(root, *path.split("/"))
            if not os.path.isfile(committed_path):
                problems.append("missing: %s" % path)
                continue
            committed = _read_bytes(committed_path)
            if _normalized(committed) != _normalized(generated):
                problems.append("changed: %s" % path)
                problems.extend(_diff(path, committed, generated, diff_lines))
        for path in stale_files(outputs, root):
            problems.append("not generated (stale): %s" % path)
    return problems


def _diff(path: str, committed: bytes, generated: bytes, limit: int) -> List[str]:
    try:
        old = _normalized(committed).decode("utf-8").splitlines()
        new = _normalized(generated).decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ["  (binary difference)"]
    lines = list(
        difflib.unified_diff(old, new, "committed/" + path, "generated/" + path, lineterm="", n=1)
    )
    if len(lines) > limit:
        lines = lines[:limit] + ["... (%d more diff lines)" % (len(lines) - limit)]
    return ["  " + line for line in lines]


def copy_tree(source: str, destination: str, skip_dirs: Iterable[str] = ()) -> None:
    """Copy a directory as real files (symlinks are followed, never recreated).

    Copies instead of symlinks keep installs working on Windows.
    """
    skip = set(skip_dirs)

    def ignore(directory: str, names: Sequence[str]) -> Set[str]:
        ignored = {name for name in names if _ignored(name)}
        if os.path.normpath(directory) == os.path.normpath(source):
            ignored.update(name for name in names if name in skip)
        return ignored

    shutil.copytree(source, destination, symlinks=False, ignore=ignore)
