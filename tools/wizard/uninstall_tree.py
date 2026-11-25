"""Filesystem operations for uninstall. Callers supply only catalog-validated names."""

import errno
import hashlib
import os
import secrets
import stat
from contextlib import ExitStack


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class UninstallTree:
    """Hold POSIX directory descriptors so renamed path components cannot redirect an operation.

    Windows lacks dir_fd operations. Reject links and junctions in every component on
    every lookup there. As with other Windows installers, use an exclusively owned folder.
    """

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.stack = ExitStack()
        self.dirs = {}
        self.versions = {}

    def __enter__(self):
        info = os.lstat(self.root)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("uninstall requires a regular project directory")
        if os.name == "posix":
            self.flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(self.root, self.flags)
            self.stack.callback(os.close, fd)
            self.dirs[""] = fd
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)

    def location(self, relative):
        parts = relative.split("/")
        if any(part in ("", ".", "..") or "\\" in part or ":" in part or "\x00" in part for part in parts):
            raise ValueError("invalid uninstall path")
        folder = ""
        if os.name == "posix":
            fd = self.dirs[""]
            for part in parts[:-1]:
                folder = os.path.join(folder, part)
                if folder not in self.dirs:
                    child = os.open(part, self.flags, dir_fd=fd)
                    self.stack.callback(os.close, child)
                    self.dirs[folder] = child
                fd = self.dirs[folder]
            return parts[-1], {"dir_fd": fd}
        path = self.root
        for part in parts[:-1]:
            path = os.path.join(path, part)
            info = os.lstat(path)
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("uninstall refuses linked or non-directory parent of %s" % relative)
        return os.path.join(path, parts[-1]), {}

    @staticmethod
    def version(name, opts):
        try:
            info = os.stat(name, follow_symlinks=False, **opts)
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def checked_location(self, relative):
        name, opts = self.location(relative)
        if self.version(name, opts) != self.versions[relative]:
            raise ValueError("file changed during uninstall: %s" % relative)
        return name, opts

    def read(self, relative, digest=False):
        """Read a regular file without following links. Missing entries are already removed."""
        try:
            name, opts = self.location(relative)
            self.versions[relative] = self.version(name, opts)
            info = os.stat(name, follow_symlinks=False, **opts)
            if _is_link(info) or not stat.S_ISREG(info.st_mode):
                raise ValueError("uninstall refuses linked or non-regular file: %s" % relative)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(name, flags, **opts), "rb") as handle:
                if not os.path.samestat(info, os.fstat(handle.fileno())):
                    raise ValueError("file changed during uninstall: %s" % relative)
                if not digest:
                    return handle.read()
                sha = hashlib.sha256()
                for block in iter(lambda: handle.read(65536), b""):
                    sha.update(block)
                return sha.hexdigest()
        except FileNotFoundError:
            self.versions[relative] = None
            return None

    def remove(self, relative):
        name, opts = self.checked_location(relative)
        os.unlink(name, **opts)

    def replace(self, relative, data):
        """Replace the name atomically; never write through an existing hard link."""
        name, opts = self.checked_location(relative)
        temp = os.path.join(os.path.dirname(name), ".wizard-" + secrets.token_hex(16))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(temp, flags, 0o600, **opts)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            renames = {"src_dir_fd": opts["dir_fd"], "dst_dir_fd": opts["dir_fd"]} if opts else {}
            self.checked_location(relative)
            os.replace(temp, name, **renames)
        finally:
            try:
                os.unlink(temp, **opts)
            except FileNotFoundError:
                pass

    def prune(self, relatives):
        folders = set()
        for relative in relatives:
            parts = relative.split("/")[:-1]
            folders.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))
        for folder in sorted(folders, key=lambda p: p.count("/"), reverse=True):
            try:
                name, opts = self.location(folder)
                os.rmdir(name, **opts)
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                    raise
