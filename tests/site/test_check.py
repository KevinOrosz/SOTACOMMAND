"""build.py --check catches drift, writes nothing, and the CLI keeps its exit-code contract."""

import tempfile
import unittest
from pathlib import Path

from tests.site import support

OUTPUTS = ("README.md", "site/evals/index.html", "site/llms-full.txt", "site/llms.txt")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = support.make_root(Path(self._tmp.name))
        built = support.run_cli("--root", str(self.root))
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(sorted(line.split(" ", 1)[1] for line in built.stdout.splitlines()), sorted(OUTPUTS))

    def tearDown(self):
        self._tmp.cleanup()

    def check(self):
        return support.run_cli("--root", str(self.root), "--check")

    def snapshot(self):
        return {rel: (self.root / rel).read_bytes() if (self.root / rel).exists() else None for rel in OUTPUTS}

    def test_fresh_outputs_pass(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "up to date: 4 generated files")

    def test_an_edited_page_is_drift(self):
        page = self.root / "site" / "faq.md"
        page.write_text(page.read_text(encoding="utf-8") + "\n## A new question\n\nA new answer.\n", encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale: site/llms-full.txt", result.stdout)
        self.assertNotIn("stale: site/llms.txt", result.stdout, "the index only changes with titles or summaries")
        self.assertIn("Run: python3 tools/repo/site/build.py", result.stdout)

    def test_a_changed_page_summary_is_drift_in_llms_txt(self):
        page = self.root / "site" / "faq.md"
        text = page.read_text(encoding="utf-8").replace("description: \"Answers on", "description: \"Short answers on")
        page.write_text(text, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale: site/llms.txt", result.stdout)

    def test_a_new_summary_is_drift(self):
        support.write_json(self.root / support.SAMPLE_REL, support.load_sample())
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale: site/evals/index.html", result.stdout)
        self.assertIn("stale: README.md", result.stdout)

    def test_a_hand_edited_output_is_drift(self):
        dashboard = self.root / "site" / "evals" / "index.html"
        dashboard.write_text(dashboard.read_text(encoding="utf-8").replace("No eval results yet", "All good"), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines()[0], "stale: site/evals/index.html")

    def test_a_hand_edited_proof_block_is_drift(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("makes no claims", "beats everything"), encoding="utf-8")
        self.assertEqual(self.check().returncode, 1)

    def test_edits_outside_the_proof_block_are_not_drift(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nA new closing line.\n", encoding="utf-8")
        self.assertEqual(self.check().returncode, 0)

    def test_a_missing_output_is_drift(self):
        (self.root / "site" / "llms.txt").unlink()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale: site/llms.txt (missing)", result.stdout)

    def test_check_writes_nothing(self):
        (self.root / "site" / "llms.txt").unlink()
        support.write_json(self.root / support.SAMPLE_REL, support.load_sample())
        before = self.snapshot()
        self.assertEqual(self.check().returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_rebuilding_clears_drift(self):
        support.write_json(self.root / support.SAMPLE_REL, support.load_sample())
        self.assertEqual(self.check().returncode, 1)
        rebuilt = support.run_cli("--root", str(self.root))
        self.assertEqual(rebuilt.returncode, 0)
        self.assertIn("wrote site/evals/index.html", rebuilt.stdout)
        self.assertIn("unchanged site/llms.txt", rebuilt.stdout)
        self.assertEqual(self.check().returncode, 0)

    def test_crlf_checkouts_are_not_drift(self):
        for rel in OUTPUTS:
            path = self.root / rel
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.check().returncode, 0)

    def test_missing_proof_markers_are_an_input_error(self):
        readme = self.root / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text.replace("<!-- proof:end -->", ""), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("README.md needs exactly one block", result.stderr)


class CliTests(unittest.TestCase):
    def test_check_refuses_preview_options(self):
        result = support.run_cli("--check", "--summary", str(support.SAMPLE_PATH))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--check compares the committed outputs", result.stderr)

    def test_summary_needs_out(self):
        result = support.run_cli("--summary", str(support.SAMPLE_PATH))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--summary needs --out", result.stderr)

    def test_preview_writes_only_under_out(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            root = support.make_root(Path(src))
            self.assertEqual(support.run_cli("--root", src).returncode, 0)
            before = {rel: (root / rel).read_bytes() for rel in OUTPUTS}
            result = support.run_cli("--root", src, "--summary", str(support.SAMPLE_PATH), "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({rel: (root / rel).read_bytes() for rel in OUTPUTS}, before)
            preview = (Path(out) / "site" / "evals" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Sample data.", preview)
            self.assertIn("sample-pack", preview)
            self.assertNotIn(str(support.FIXTURES), preview, "absolute paths never reach the output")

    def test_help(self):
        result = support.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--check", result.stdout)
        self.assertIn("Exit codes", result.stdout)


if __name__ == "__main__":
    unittest.main()
