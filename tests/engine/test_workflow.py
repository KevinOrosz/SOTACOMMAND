"""engine/workflows/convergence.js: independent passes, then one synthesizer."""

import json
import os
import re
import shutil
import unittest

from . import support

SCRIPT = os.path.join(support.ENGINE_DIR, "workflows", "convergence.js")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_harness.js")
NODE = shutil.which("node")


class StaticShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = support.read(SCRIPT)
        end = cls.source.index("\n}\n")
        cls.meta = cls.source[: end + 2]

    def test_meta_is_the_first_statement_and_a_pure_literal(self):
        self.assertTrue(self.source.startswith("export const meta = {\n"))
        # Outside string literals: no template strings, spreads or calls.
        code = re.sub(r"'[^']*'", "''", self.meta)
        for forbidden in ("`", "${", "...", "("):
            self.assertNotIn(forbidden, code)
        self.assertIn("name: 'convergence'", self.meta)
        self.assertIn("description:", self.meta)

    def test_phase_titles_match_meta(self):
        meta_titles = re.findall(r"title: '([^']+)'", self.meta)
        called = re.findall(r"phase\('([^']+)'\)", self.source)
        self.assertEqual(meta_titles, ["Passes", "Synthesis"])
        self.assertEqual(called, meta_titles)

    def test_runtime_restrictions(self):
        for forbidden in ("Date.now", "Math.random", "new Date()", "import(", "require("):
            self.assertNotIn(forbidden, self.source)

    def test_uses_pipeline_and_agent(self):
        self.assertIn("await pipeline(lenses,", self.source)
        self.assertIn("schema: PASS_SCHEMA", self.source)
        self.assertIn("schema: SYNTHESIS_SCHEMA", self.source)

    def test_schemas_keep_required_inside_properties(self):
        for name in ("PASS_SCHEMA", "SYNTHESIS_SCHEMA"):
            block = self.source[self.source.index("const %s" % name):]
            required = re.search(r"required: \[([^\]]*)\]", block).group(1)
            properties = block[: block.index("\n}\n")]
            for key in re.findall(r"'([a-z_]+)'", required):
                self.assertIn("%s:" % key, properties, name)


@unittest.skipIf(NODE is None, "node is not installed")
class RuntimeTests(support.TempDirTestCase):
    def run_script(self, **scenario):
        path = support.write(self.path("scenario.json"), json.dumps(scenario))
        result = support.run([NODE, HARNESS, SCRIPT, path])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def passes(self, output):
        return [c for c in output["calls"] if c["opts"].get("label", "").startswith("pass-")]

    def synthesizer(self, output):
        return [c for c in output["calls"] if c["opts"].get("label") == "synthesizer"]

    def test_default_run(self):
        material = "Ship the weekly report on Fridays instead of Mondays."
        output = self.run_script(args=material)
        self.assertTrue(output["ok"], output.get("error"))
        self.assertEqual(output["meta"]["name"], "convergence")
        passes = self.passes(output)
        self.assertEqual([c["opts"]["label"] for c in passes], ["pass-1", "pass-2", "pass-3"])
        lenses = ["evidence:", "risk:", "alternatives:"]
        for call, lens in zip(passes, lenses):
            self.assertIn(material, call["prompt"])
            self.assertIn("Your lens: " + lens, call["prompt"])
            for other in lenses:
                if other != lens:
                    self.assertNotIn("Your lens: " + other, call["prompt"])
            self.assertNotIn("summary from pass-", call["prompt"], "passes must not see each other")
            self.assertEqual(call["opts"]["phase"], "Passes")
            self.assertIn("schema", call["opts"])
        (synthesis,) = self.synthesizer(output)
        for label in ("pass-1", "pass-2", "pass-3"):
            self.assertIn("summary from %s" % label, synthesis["prompt"])
        self.assertIn("Never manufacture agreement", synthesis["prompt"])
        self.assertEqual(output["phases"], ["Passes", "Synthesis"])
        self.assertEqual(output["result"]["status"], "complete")
        self.assertEqual(output["result"]["dropped"], 0)
        self.assertEqual(output["result"]["synthesis"]["summary"], "stub synthesis")

    def test_object_args_with_lenses_and_agent_type(self):
        output = self.run_script(args={
            "input": "Draft plan",
            "lenses": ["cost", "risk", " ", "users"],
            "agentType": "sotacommand-engine:researcher",
        })
        self.assertTrue(output["ok"], output.get("error"))
        passes = self.passes(output)
        self.assertEqual(len(passes), 3)
        self.assertTrue(all(c["opts"]["agentType"] == "sotacommand-engine:researcher" for c in passes))
        self.assertNotIn("agentType", self.synthesizer(output)[0]["opts"])

    def test_identical_passes_by_count(self):
        output = self.run_script(args={"input": "x", "passes": 4})
        self.assertEqual(len(self.passes(output)), 4)

    def test_too_many_lenses_are_capped_and_logged(self):
        output = self.run_script(args={"input": "x", "lenses": ["lens %d" % i for i in range(10)]})
        self.assertEqual(len(self.passes(output)), 8)
        self.assertTrue(any("dropping 2" in line for line in output["logs"]), output["logs"])

    def test_missing_input_is_an_error(self):
        for args in (None, "", {"lenses": ["a", "b"]}, ["not", "an", "object"]):
            with self.subTest(args=args):
                output = self.run_script(args=args)
                self.assertFalse(output["ok"])
                self.assertIn("pass the material", output["error"])
                self.assertEqual(output["calls"], [])

    def test_one_lens_is_not_convergence(self):
        output = self.run_script(args={"input": "x", "lenses": ["only"]})
        self.assertFalse(output["ok"])
        self.assertIn("at least 2", output["error"])

    def test_failed_pass_is_dropped_and_logged(self):
        output = self.run_script(args="x", failPasses=[1])
        self.assertTrue(output["ok"])
        self.assertEqual(output["result"]["dropped"], 1)
        self.assertEqual([p["pass"] for p in output["result"]["passes"]], ["pass-1", "pass-3"])
        self.assertTrue(any("1 of 3 passes" in line for line in output["logs"]))
        self.assertNotIn("summary from pass-2", self.synthesizer(output)[0]["prompt"])

    def test_too_few_passes_skip_synthesis(self):
        output = self.run_script(args="x", failPasses=[0, 1])
        self.assertTrue(output["ok"])
        self.assertEqual(output["result"]["status"], "insufficient")
        self.assertEqual(self.synthesizer(output), [])

    def test_missing_synthesis_is_reported(self):
        output = self.run_script(args="x", synthesisNull=True)
        self.assertEqual(output["result"]["status"], "synthesis-missing")


if __name__ == "__main__":
    unittest.main()
