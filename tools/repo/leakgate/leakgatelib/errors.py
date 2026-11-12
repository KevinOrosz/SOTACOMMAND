"""Exceptions raised by leakgate.

Every exception here maps to exit code 2 ("usage or config error") at the
command line. Findings are not exceptions: they are data, returned by
:func:`leakgatelib.scanner.scan`.
"""


class LeakgateError(Exception):
    """Base class for errors that stop a run before it can give a verdict."""


class ConfigError(LeakgateError):
    """A rules file, denylist, hashed denylist or salt is missing or invalid."""


class UsageError(LeakgateError):
    """The caller asked for something leakgate can't do.

    Examples: a path that doesn't exist, a ``--root`` that doesn't contain the
    path, or a directory whose every file is ignored by git.
    """


class ScanError(LeakgateError):
    """A file that had to be scanned couldn't be read.

    The gate fails closed: an unreadable file is never treated as clean.
    """
