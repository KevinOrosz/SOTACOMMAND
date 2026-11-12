"""The command-line contract: exit 0 clean, 1 findings, 2 usage or config error."""

import json
import os
import unittest

from tests.leakgate import support


class ExitCodeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        support.write_files(self.tmp, {"clean.md": "Nothing private here.\n", "KB/notes.md": "x\n"})
        self.clean = os.path.join(self.tmp, "clean.md")
        self.leaky = os.path.join(self.tmp, "KB", "notes.md")

    def code(self, *args, **kwargs):
        return support.run_cli(*args, **kwargs).returncode

    def test_clean_findings_and_errors(self):
        self.assertEqual(0, self.code("check", self.clean))
        self.assertEqual(1, self.code("check", "--root", self.tmp, self.leaky))
        self.assertEqual(1, self.code("check", self.tmp))

    def test_usage_errors_exit_2(self):
        self.assertEqual(2, self.code())
        self.assertEqual(2, self.code("check"))
        self.assertEqual(2, self.code("frobnicate"))
        self.assertEqual(2, self.code("check", self.clean, "--no-such-flag"))
        self.assertEqual(2, self.code("check", os.path.join(self.tmp, "missing.md")))
        self.assertEqual(2, self.code("check", self.clean, "--root", os.path.join(self.tmp, "nope")))
        self.assertEqual(2, self.code("check", self.clean, "--context", "-1"))
        self.assertEqual(2, self.code("check", self.clean, "--mode", "ci"))
        self.assertEqual(2, self.code("hash-denylist", self.clean))  # --salt-file is required

    def test_config_errors_exit_2(self):
        cases = {
            "not-json.json": "{ nope",
            "wrong-version.json": json.dumps({"version": 2}),
            "unknown-key.json": json.dumps({"version": 1, "regexes": []}),
            "bad-regex.json": json.dumps({"version": 1, "regex": [{"id": "x", "description": "d", "patterns": ["("]}]}),
            "empty-match.json": json.dumps({"version": 1, "regex": [{"id": "x", "description": "d", "patterns": ["a*"]}]}),
            "bad-id.json": json.dumps({"version": 1, "paths": [{"id": "Bad Id", "globs": ["x"]}]}),
            "dup-id.json": json.dumps({"version": 1, "paths": [{"id": "x", "globs": ["a"]}, {"id": "x", "globs": ["b"]}]}),
            "reserved-id.json": json.dumps({"version": 1, "terms": [{"id": "hashed-term", "terms": ["q"]}]}),
            "missing-extends.json": json.dumps({"version": 1, "extends": "nowhere.json"}),
        }
        for name, content in cases.items():
            path = os.path.join(self.tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            with self.subTest(config=name):
                completed = support.run_cli("check", self.clean, "--config", path)
                self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("leakgate: error:", completed.stderr)
        self.assertEqual(2, self.code("check", self.clean, "--config", os.path.join(self.tmp, "absent.json")))
        self.assertEqual(2, self.code("check", self.clean, "--denylist", os.path.join(self.tmp, "absent.txt")))

    def test_json_errors_are_reported_as_json(self):
        document = support.run_json("check", os.path.join(self.tmp, "missing.md"), "--json")
        self.assertEqual(2, document["_returncode"])
        self.assertEqual(2, document["exit_code"])
        self.assertTrue(document["errors"])

    def test_internal_errors_never_look_like_findings(self):
        bench = support.run_cli("bench", "--corpus", os.path.join(self.tmp, "no-corpus"))
        self.assertEqual(2, bench.returncode)


class JsonContractTest(unittest.TestCase):
    """M3 (extraction) consumes this document; keep it stable."""

    def test_document_shape(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"KB/a.md": "x\n", "docs/b.md": "fine\n"})
            document = support.run_json("check", tmp, "--json")
        self.assertEqual(1, document["_returncode"])
        self.assertEqual("leakgate", document["tool"])
        self.assertEqual(1, document["schema_version"])
        self.assertEqual("check", document["command"])
        self.assertEqual("check", document["mode"])
        self.assertEqual(1, document["exit_code"])
        self.assertEqual({"files_scanned", "files_binary", "findings", "files_with_findings", "allowed"}, set(document["summary"]))
        self.assertEqual(2, document["summary"]["files_scanned"])
        (finding,) = document["findings"]
        self.assertEqual({"path", "line", "col", "rule_id", "rule_type", "excerpt"}, set(finding))
        self.assertEqual(("KB/a.md", 0, 0, "private-dir", "path"),
                         (finding["path"], finding["line"], finding["col"], finding["rule_id"], finding["rule_type"]))
        self.assertEqual([], document["errors"])
        self.assertIsInstance(document["warnings"], list)
        self.assertNotIn("allowed_findings", document)

    def test_show_allowed_adds_allowed_findings(self):
        document = support.run_json("check", "fixtures/leak-corpus/clean", "--root", "fixtures/leak-corpus/clean",
                                    "--all-files", "--show-allowed", "--json")
        self.assertEqual(0, document["_returncode"])
        self.assertTrue(document["allowed_findings"])
        self.assertTrue(all(item["allowed_by"] for item in document["allowed_findings"]))

    def test_clean_output_is_quiet_on_stdout(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"a.md": "fine\n"})
            completed = support.run_cli("check", tmp)
        self.assertEqual(0, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("no findings", completed.stderr)


class DoneWhenTest(unittest.TestCase):
    """The brief's acceptance commands, verbatim."""

    def test_check_agents_md_exits_0(self):
        self.assertEqual(0, support.run_cli("check", "AGENTS.md").returncode)

    @unittest.skipUnless(os.path.isdir(os.path.join(support.REPO_ROOT, "internal")), "internal/ is only in the private staging repository")
    def test_check_internal_plan_exits_1(self):
        self.assertEqual(1, support.run_cli("check", "internal/PLAN.md").returncode)

    def test_bench_prints_recall_and_false_blocks(self):
        completed = support.run_cli("bench")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        lines = completed.stdout.strip().splitlines()
        recall = [line for line in lines if line.startswith("recall=")]
        blocks = [line for line in lines if line.startswith("false_blocks=")]
        self.assertEqual(1, len(recall))
        self.assertEqual(["false_blocks=0"], blocks)
        caught, planted = (int(part) for part in recall[0].split("=", 1)[1].split("/"))
        self.assertEqual(caught, planted)
        self.assertGreaterEqual(planted, 80)
        self.assertEqual(["recall=%d/%d" % (caught, planted), "false_blocks=0"], lines[-2:])

    def test_version(self):
        completed = support.run_cli("--version")
        self.assertEqual(0, completed.returncode)
        self.assertRegex(completed.stdout, r"\Aleakgate \d+\.\d+\.\d+\n\Z")


if __name__ == "__main__":
    unittest.main()
