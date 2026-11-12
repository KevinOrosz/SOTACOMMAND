#!/usr/bin/env python3
"""leakgate: stop private material from leaving this repository.

Usage (exit 0 clean, 1 findings, 2 usage or config error)::

    python3 tools/repo/leakgate/leakgate.py check PATH... [--json]
        [--config FILE] [--denylist FILE] [--hashed-denylist FILE]
        [--salt-file FILE] [--all-files] [--exclude GLOB] [--root DIR]
        [--mode check|bench] [--show-allowed] [--context N]
        [--require-env-denylist]
    python3 tools/repo/leakgate/leakgate.py hash-denylist PLAIN.txt --salt-file FILE [--out FILE]
    python3 tools/repo/leakgate/leakgate.py bench [--json]

As a library (with this directory on ``sys.path``)::

    import leakgate
    findings = leakgate.scan(["docs/"], leakgate.load_config())

Standard library only, Python 3.9+, no network access. Rules live in
``rules.json`` beside this file; ``README.md`` documents everything.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from leakgatelib import (  # noqa: E402
    BenchResult,
    Config,
    ConfigError,
    DEFAULT_CONFIG_PATH,
    Finding,
    LeakgateError,
    ScanError,
    ScanReport,
    UsageError,
    __version__,
    hash_specs,
    load_config,
    parse_term_lines,
    run_bench,
    scan,
    scan_report,
    scan_text,
)
from leakgatelib.cli import main  # noqa: E402

__all__ = [
    "BenchResult",
    "Config",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "Finding",
    "LeakgateError",
    "ScanError",
    "ScanReport",
    "UsageError",
    "__version__",
    "hash_specs",
    "load_config",
    "main",
    "parse_term_lines",
    "run_bench",
    "scan",
    "scan_report",
    "scan_text",
]

if __name__ == "__main__":
    sys.exit(main())
