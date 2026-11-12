"""Shared helpers for the extraction tool tests.

The leak gate runs over ``tests/`` in CI, so these tests keep leak-shaped text
out of their own source:

* Persona-style names in unit tests are invented tokens (``ZEPHYR``, ``QUILL``,
  ``ONYX``). The real persona names live only in ``fixtures/`` and the example
  manifests, and the end-to-end tests read them from there.
* Home-directory paths are assembled at runtime by :func:`mac_home` and
  :func:`linux_home`.
* House rule codes, ruling dates and model names are tested end to end
  through ``fixtures/fake-command``, whose planted markers are listed in
  ``fixtures/fake-command/planted-markers.json``.

The owner name "Avery Example" is the invented fixture owner.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_PY = REPO_ROOT / "tools" / "repo" / "extract" / "extract.py"
FIXTURE = REPO_ROOT / "fixtures" / "fake-command"
EXAMPLE_MANIFEST = REPO_ROOT / "export" / "manifest.example.json"
HANDLED_MANIFEST = REPO_ROOT / "export" / "manifest.handled.example.json"
SCHEMA = REPO_ROOT / "export" / "manifest.schema.json"
STUB_GATE = Path(__file__).resolve().parent / "stub_leakgate.py"

_MODULE_NAME = "sotacommand_extract_under_test"


def load_extract():
    """Import tools/repo/extract/extract.py once, under a private module name."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, str(EXTRACT_PY))
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


ex = load_extract()


def mac_home(user, *rest):
    """A macOS home path such as the one the tool must catch, built from parts."""
    return "/" + "/".join(["Users", user] + list(rest))


def linux_home(user, *rest):
    return "/" + "/".join(["home", user] + list(rest))


def windows_home(user, *rest):
    return "\\".join(["C:", "Users", user] + list(rest))


def tilde_home(user, *rest):
    """A home path written as a tilde followed by the user name."""
    return "~" + "/".join([user] + list(rest))


def stub(*args):
    """The stub leak gate as an argv prefix, with its own options."""
    return [sys.executable, str(STUB_GATE)] + list(args)


def base_manifest(**overrides):
    """A small valid manifest with invented persona tokens and the fixture owner."""
    manifest = {
        "schema_version": "1",
        "persona_map": {"ZEPHYR": "orchestrator", "QUILL": "researcher", "ONYX": "owner-auditor"},
        "owner_terms": {"Avery Example": "the owner", "Avery": "the owner"},
        "files": [],
    }
    manifest.update(overrides)
    return manifest


class TempDirTestCase(unittest.TestCase):
    """A test case with a fresh temporary directory in ``self.tmp``."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="extract-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def make_source(self, files, name="src"):
        """Write ``{relative path: text or bytes}`` under a new source folder."""
        root = self.tmp / name
        root.mkdir()
        for rel, content in files.items():
            path = root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_bytes(content.encode("utf-8"))
        return root

    def write_manifest(self, manifest, name="manifest.json"):
        path = self.tmp / name
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def load(self, manifest, source):
        """Load a manifest dict against ``source``; returns (manifest, errors)."""
        return ex.load_manifest(self.write_manifest(manifest), source)

    def assert_valid(self, manifest, source):
        loaded, errors = self.load(manifest, source)
        self.assertEqual(errors, [])
        return loaded

    def assert_error(self, manifest, source, *fragments):
        """Assert loading fails with an error message containing every fragment."""
        loaded, errors = self.load(manifest, source)
        self.assertIsNone(loaded)
        joined = "\n".join(errors)
        for fragment in fragments:
            self.assertIn(fragment, joined)
        return errors

    def transform(self, text, entry=None, target="docs/page.md", source="page.md", mode=0o644, **manifest_overrides):
        """Run one file through a manifest built from :func:`base_manifest`."""
        root = self.make_source({source: text}, name="src-%d" % len(list(self.tmp.iterdir())))
        item = {"from": source, "to": target}
        item.update(entry or {})
        manifest = base_manifest(files=[item], **manifest_overrides)
        loaded = self.assert_valid(manifest, root)
        compiled = ex.Compiled.build(loaded)
        raw = root.joinpath(*source.split("/")).read_bytes()
        return ex.transform_entry(compiled, loaded.entries[0], raw, mode)

    def text(self, result):
        return result.data.decode("utf-8")

    def flags(self, result, kind=None):
        return [f for f in result.flags if kind is None or f.flag == kind]

    def run_tool(self, source, manifest_path, gate=None, dry_run=False, report=True, timeout=60.0, out_name="stage"):
        """Run an extraction in-process with temp directories under ``self.tmp``."""
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        opts = ex.Options(
            source=str(source),
            manifest=str(manifest_path),
            out=str(self.tmp / out_name),
            dry_run=dry_run,
            report=str(self.tmp / "report.md") if report else None,
            leakgate=gate if gate is not None else stub("--terms-from", str(manifest_path)),
            gate_timeout=timeout,
            work_dir=str(work),
        )
        outcome = ex.run_extraction(opts)
        return outcome, ex.render_report(outcome)

    def assert_no_output(self, out_name="stage"):
        """Refusals leave nothing behind: no --out, no partial copy, no temp dirs."""
        self.assertFalse((self.tmp / out_name).exists(), "--out must not exist after a refusal")
        leftovers = [p.name for p in self.tmp.iterdir() if ".partial-" in p.name]
        self.assertEqual(leftovers, [])
        work = self.tmp / "work"
        if work.exists():
            self.assertEqual(sorted(p.name for p in work.iterdir()), [])


def run_cli(*args, env=None, cwd=None):
    """Run extract.py as a subprocess; returns CompletedProcess with text output."""
    return subprocess.run(
        [sys.executable, str(EXTRACT_PY)] + [str(a) for a in args],
        cwd=str(cwd or REPO_ROOT),
        env=env if env is not None else dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=300,
    )


def tree_snapshot(root):
    """``{relative path: (bytes, mode bits)}`` for every file under ``root``."""
    snapshot = {}
    for folder, _, files in os.walk(str(root)):
        for name in files:
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            snapshot[rel] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return snapshot


def planted_markers():
    with open(str(FIXTURE / "planted-markers.json"), encoding="utf-8") as handle:
        return json.load(handle)["markers"]
