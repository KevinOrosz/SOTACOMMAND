"""leakgate: a gate that stops private material from leaving a repository.

Standard library only, Python 3.9+. No network calls. The command-line entry
point is ``tools/repo/leakgate/leakgate.py``; this package is the library
behind it. Stable API::

    from leakgate import scan, scan_report, scan_text, load_config, run_bench

    findings = scan(["docs/"])            # blocking findings only
    for f in findings:
        print(f.path, f.line, f.col, f.rule_id, f.excerpt)

See ``tools/repo/leakgate/README.md`` for rules, allowlists and the bench.
"""

__version__ = "1.0.0"

from .bench import BenchResult, run_bench  # noqa: E402
from .config import DEFAULT_CONFIG_PATH, Config, load_config  # noqa: E402
from .errors import ConfigError, LeakgateError, ScanError, UsageError  # noqa: E402
from .scanner import Finding, ScanReport, scan, scan_report, scan_text  # noqa: E402
from .terms import hash_specs, parse_term_lines  # noqa: E402

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
    "parse_term_lines",
    "run_bench",
    "scan",
    "scan_report",
    "scan_text",
]
