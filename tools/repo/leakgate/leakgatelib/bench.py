"""The benchmark: proof in both directions, re-runnable by anyone.

``fixtures/leak-corpus/`` holds two miniature repositories and a test-only
rules file:

``planted/``
    Files full of deliberate leaks. ``planted/expected.json`` declares every
    finding the gate must produce there, as ``(path, line, rule_id)``.
``clean/``
    Realistic files that must produce no finding at all: harness names,
    plugin paths, prose words that collide with persona names, example.com
    addresses, version strings.
``bench.json``
    Extends the production ``rules.json`` with invented private terms, a
    hashed list and its salt, and the ``LEAKGATE_DENYLIST`` value to use. The
    bench never reads the real environment, so a CI secret can't change it.

Sealed tokens. A few planted values have the exact shape of a real provider
token (a Stripe test key, a Slack token, a Hugging Face token). Stored as is,
they would trip GitHub's secret push protection for everyone who pushes this
repository. So they are stored with ``{{seal}}`` inside their prefix
(``s{{seal}}k_test_...``), and the bench copies the corpus to a temporary
folder and removes the seals there before it scans. Only the bench does this:
the scanner never removes a seal, so a seal is no way past the gate. Line and
column numbers don't change, because each seal sits after the token's first
character.

Each corpus is scanned as its own root, in ``bench`` mode, with every file
included. Recall is ``caught / planted``, where a planted finding counts as
caught when a blocking finding has the same path, line and rule (compared
as a multiset). Any other blocking finding in ``planted/`` is *unexpected*,
which means ``expected.json`` is stale or a rule over-fires. Every blocking
finding in ``clean/`` is a *false block*. The bench passes only when nothing
is missed, nothing is unexpected and nothing is falsely blocked.
"""

import collections
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .config import TOOL_DIR, Config, display_path, load_config
from .errors import ConfigError
from .scanner import Finding, scan_report

#: ``tools/repo/leakgate`` -> the repository root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(TOOL_DIR)))
DEFAULT_CORPUS = os.path.join(REPO_ROOT, "fixtures", "leak-corpus")
EXPECTED_NAME = "expected.json"
EXPECTED_SCHEMA_VERSION = 1
#: Marks a planted token stored in a shape GitHub's push protection won't match.
SEAL = "{{seal}}"


def unseal(text: str) -> str:
    """The planted value as the bench scans it."""
    return text.replace(SEAL, "")


def materialize(source: str, destination: str) -> None:
    """Copy one corpus folder to ``destination``, with every seal removed."""
    shutil.copytree(source, destination, symlinks=True)
    marker = SEAL.encode("utf-8")
    for folder, _dirs, files in os.walk(destination):
        for name in files:
            path = os.path.join(folder, name)
            if os.path.islink(path):
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            if marker in data:
                with open(path, "wb") as handle:
                    handle.write(data.replace(marker, b""))

Expected = Tuple[str, int, str]


class BenchResult(object):
    """Outcome of :func:`run_bench`."""

    def __init__(self) -> None:
        self.planted_files = 0
        self.planted = 0
        self.caught = 0
        self.missed = []  # type: List[Expected]
        self.unexpected = []  # type: List[Finding]
        self.clean_files = 0
        self.false_blocks = []  # type: List[Finding]
        self.coverage = {}  # type: Dict[str, int]
        self.warnings = []  # type: List[str]

    @property
    def ok(self) -> bool:
        return self.planted > 0 and not self.missed and not self.unexpected and not self.false_blocks

    @property
    def recall(self) -> float:
        return self.caught / self.planted if self.planted else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "recall": {"caught": self.caught, "planted": self.planted},
            "false_blocks": len(self.false_blocks),
            "unexpected": len(self.unexpected),
            "planted_files": self.planted_files,
            "clean_files": self.clean_files,
            "coverage": dict(sorted(self.coverage.items())),
            "missed": [{"path": p, "line": l, "rule_id": r} for p, l, r in self.missed],
            "unexpected_findings": [f.to_dict() for f in self.unexpected],
            "false_block_findings": [f.to_dict() for f in self.false_blocks],
            "warnings": list(self.warnings),
        }


def load_expected(path: str, planted_dir: str, config: Config) -> List[Expected]:
    """Read and validate ``expected.json``."""
    shown = display_path(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ConfigError("%s: not found" % shown)
    except ValueError as exc:
        raise ConfigError("%s: not valid JSON: %s" % (shown, exc))
    if not isinstance(data, dict) or data.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise ConfigError("%s: must be an object with \"schema_version\": %d" % (shown, EXPECTED_SCHEMA_VERSION))
    items = data.get("findings")
    if not isinstance(items, list) or not items:
        raise ConfigError("%s: \"findings\" must be a non-empty list" % shown)
    expected = []
    for index, item in enumerate(items):
        where = "%s: findings[%d]" % (shown, index)
        if not isinstance(item, dict) or set(item) != {"path", "line", "rule_id"}:
            raise ConfigError("%s must have exactly \"path\", \"line\" and \"rule_id\"" % where)
        rel, line, rule_id = item["path"], item["line"], item["rule_id"]
        if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
            raise ConfigError("%s: \"path\" must be a relative POSIX path inside planted/" % where)
        if not isinstance(line, int) or isinstance(line, bool) or line < 0:
            raise ConfigError("%s: \"line\" must be an integer >= 0 (0 = the file path)" % where)
        if rule_id not in config.rule_types:
            raise ConfigError("%s: unknown rule_id %r" % (where, rule_id))
        if not os.path.lexists(os.path.join(planted_dir, *rel.split("/"))):
            raise ConfigError("%s: %s does not exist" % (where, rel))
        expected.append((rel, line, rule_id))
    return expected


def run_bench(corpus_dir: Optional[str] = None) -> BenchResult:
    """Run the benchmark on ``corpus_dir`` (default ``fixtures/leak-corpus``)."""
    corpus = os.path.abspath(corpus_dir or DEFAULT_CORPUS)
    planted_dir = os.path.join(corpus, "planted")
    clean_dir = os.path.join(corpus, "clean")
    config_path = os.path.join(corpus, "bench.json")
    for required in (planted_dir, clean_dir):
        if not os.path.isdir(required):
            raise ConfigError("%s: corpus directory not found" % display_path(required))
    config = load_config(config_path, bench=True)
    expected = load_expected(os.path.join(planted_dir, EXPECTED_NAME), planted_dir, config)

    result = BenchResult()
    result.warnings.extend(config.warnings)
    work = tempfile.mkdtemp(prefix="leakgate-bench-")
    try:
        planted_copy, clean_copy = os.path.join(work, "planted"), os.path.join(work, "clean")
        materialize(planted_dir, planted_copy)
        materialize(clean_dir, clean_copy)
        planted = scan_report([planted_copy], config, root=planted_copy, mode="bench", all_files=True, exclude=["/" + EXPECTED_NAME])
        clean = scan_report([clean_copy], config, root=clean_copy, mode="bench", all_files=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for report in (planted, clean):
        if report.errors:
            raise ConfigError("bench couldn't read the corpus: " + "; ".join(report.errors))
        result.warnings.extend(report.warnings)

    result.planted_files = planted.files_scanned
    result.clean_files = clean.files_scanned
    result.planted = len(expected)
    wanted = collections.Counter(expected)
    detected = collections.Counter((f.path, f.line, f.rule_id) for f in planted.findings)
    result.caught = sum(min(count, detected[key]) for key, count in wanted.items())
    for key in sorted(wanted):
        result.missed.extend([key] * max(0, wanted[key] - detected[key]))
    surplus = detected - wanted
    for finding in planted.findings:
        key = (finding.path, finding.line, finding.rule_id)
        if surplus[key] > 0:
            surplus[key] -= 1
            result.unexpected.append(finding)
    result.false_blocks = list(clean.findings)
    for _path, _line, rule_id in expected:
        rule_type = config.rule_types[rule_id]
        result.coverage[rule_type] = result.coverage.get(rule_type, 0) + 1
    return result
