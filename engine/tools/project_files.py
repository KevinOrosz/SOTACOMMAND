"""Confine project state and installer files to regular entries under a chosen root."""
import contextlib
import os
import stat


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def parts(relative):
    values = relative.replace(os.sep, "/").split("/")
    if any(x in ("", ".", "..") or "\\" in x or ":" in x or "\x00" in x for x in values):
        raise ValueError("invalid project-relative path")
    return values


def checked(root, relative, directory=False):
    path = os.path.abspath(root)
    if _link(os.lstat(path)):
        raise ValueError("linked project folder")
    names = parts(relative)
    for number, name in enumerate(names):
        path = os.path.join(path, name)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        want_dir = number < len(names) - 1 or directory
        if _link(info) or (want_dir and not stat.S_ISDIR(info.st_mode)) or (
                not want_dir and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
            raise ValueError("linked or unexpected project entry: %s" % relative)
    return path


@contextlib.contextmanager
def location(root, relative, create=False):
    names = parts(relative)
    if os.name != "posix":
        path = checked(root, relative)
        if create:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            path = checked(root, relative)
        yield path, {}
        return
    flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
    with contextlib.ExitStack() as stack:
        fd = os.open(root, flags)
        stack.callback(os.close, fd)
        for part in names[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(part, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        yield names[-1], {"dir_fd": fd}


@contextlib.contextmanager
def open_regular(root, relative, mode, **kwargs):
    # Open without truncating. Validate the inode before any write, including for hard links.
    flags = os.O_RDONLY if mode.startswith("r") else os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if mode.startswith("a") else 0
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    with location(root, relative, create=not mode.startswith("r")) as (name, opts):
        fd = os.open(name, flags, 0o600, **opts)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("expected a regular single-link file: %s" % relative)
            if mode.startswith("w"):
                os.ftruncate(fd, 0)
            with os.fdopen(fd, mode, closefd=False, **kwargs) as stream:
                yield stream
        finally:
            os.close(fd)
