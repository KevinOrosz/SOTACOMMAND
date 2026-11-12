"""engine/tools/session_close_gate.py: the Stop hook close gate and its exit codes."""

import json
import os
import sys
import unittest

from . import support


def stop_input(root, active=False, background=None):
    payload = {
        "session_id": "abc123",
        "transcript_path": os.path.join(root, ".transcript.jsonl"),
        "cwd": root,
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "stop_hook_active": active,
        "last_assistant_message": "Done.",
        "background_tasks": background or [],
        "session_crons": [],
    }
    return payload


def python(code):
    """A verify command in list form, portable across platforms."""
    return [sys.executable, "-c", code]


class CloseGateTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(self.root)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def gate(self, **gate):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"close_gate": gate}))

    def run_hook(self, payload=None, raw=None):
        payload = stop_input(self.root) if payload is None and raw is None else payload
        return support.run_tool("session_close_gate.py", payload, raw=raw, env=self.env)

    def assertBlocked(self, result, *fragments):
        self.assertEqual(result.returncode, 2, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "block")
        for fragment in fragments:
            self.assertIn(fragment, decision["reason"])
            self.assertIn(fragment, result.stderr)
        return decision["reason"]

    def test_no_config_passes(self):
        result = self.run_hook()
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_empty_verify_passes(self):
        self.gate(verify="")
        self.assertEqual(self.run_hook().returncode, 0)
        self.gate(verify=[])
        self.assertEqual(self.run_hook().returncode, 0)

    def test_passing_verify_exits_0_silently(self):
        self.gate(verify=python("print('all good')"))
        result = self.run_hook()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_failing_verify_exits_2_with_reason(self):
        self.gate(verify=python("import sys; print('3 tests failed'); sys.exit(3)"))
        reason = self.assertBlocked(self.run_hook(), "verify exited 3", "3 tests failed")
        self.assertIn(sys.executable, reason)

    def test_failure_after_one_retry_exits_1_and_lets_claude_stop(self):
        self.gate(verify=python("import sys; sys.exit(1)"))
        result = self.run_hook(stop_input(self.root, active=True))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("still failing after one retry", result.stderr.splitlines()[0])

    def test_block_repeats_keeps_blocking(self):
        self.gate(verify=python("import sys; sys.exit(1)"), block_repeats=True)
        self.assertBlocked(self.run_hook(stop_input(self.root, active=True)), "verify exited 1")

    def test_shell_string_command(self):
        if os.name == "nt":
            self.skipTest("POSIX shell syntax")
        self.gate(verify="echo checking && exit 4")
        self.assertBlocked(self.run_hook(), "verify exited 4", "checking")
        self.gate(verify="true")
        self.assertEqual(self.run_hook().returncode, 0)

    def test_verify_runs_in_the_project_root(self):
        support.write(os.path.join(self.root, "marker.txt"), "x")
        self.gate(verify=python("import os, sys; sys.exit(0 if os.path.exists('marker.txt') else 9)"))
        self.assertEqual(self.run_hook().returncode, 0)

    def test_timeout_blocks(self):
        self.gate(verify=python("import time; time.sleep(30)"), timeout_seconds=1)
        self.assertBlocked(self.run_hook(), "timed out after 1 seconds")

    def test_command_that_cannot_start_blocks(self):
        self.gate(verify=["definitely-not-a-real-command-7f3a"])
        self.assertBlocked(self.run_hook(), "could not start")

    def test_missing_file_output_still_blocks_through_stdout_json(self):
        # The hooks reference treats exit 2 with empty stdout and a "No such file"
        # stderr as non-blocking for Stop; the JSON on stdout keeps the block.
        self.gate(verify=python("import sys; print('cat: x: No such file or directory'); sys.exit(1)"))
        self.assertBlocked(self.run_hook(), "No such file or directory")

    def test_background_tasks_skip_the_gate(self):
        self.gate(verify=python("import sys; sys.exit(1)"))
        task = {"id": "t1", "type": "subagent", "status": "running", "description": "build"}
        result = self.run_hook(stop_input(self.root, background=[task]))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_long_output_is_trimmed_to_its_tail(self):
        self.gate(verify=python("import sys\nfor i in range(200): print('line', i)\nsys.exit(1)"))
        reason = self.assertBlocked(self.run_hook(), "line 199")
        self.assertNotIn("line 0\n", reason)
        self.assertLess(len(reason), 4000)

    def test_invalid_gate_config_blocks_once(self):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"close_gate": "npm test"}))
        self.assertBlocked(self.run_hook(), "close_gate must be an object")
        self.gate(verify=python("pass"), timeout_seconds=0)
        self.assertBlocked(self.run_hook(), "timeout_seconds")
        self.gate(verify=[1, 2])
        self.assertBlocked(self.run_hook(), "close_gate.verify")
        self.gate(verify=python("pass"), block_repeats="yes")
        self.assertBlocked(self.run_hook(), "block_repeats")

    def test_invalid_os_config_json(self):
        support.write(os.path.join(self.root, "os.config.json"), "{ nope")
        self.assertBlocked(self.run_hook(), "not valid JSON")
        self.assertEqual(self.run_hook(stop_input(self.root, active=True)).returncode, 1)

    def test_input_that_is_not_json_is_a_non_blocking_error(self):
        result = self.run_hook(raw="not json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not run", result.stderr)


if __name__ == "__main__":
    unittest.main()
