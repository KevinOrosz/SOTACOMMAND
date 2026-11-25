"""engine/tools/session_close_gate.py: the Stop hook close gate and its exit codes."""

import json
import os
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

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

    def test_unapproved_commands_never_execute(self):
        self.gate(verify=python("print('would run')"))
        self.assertBlocked(self.run_hook(), "owner review required")
        self.gate(verify="echo checking", block_repeats=True)
        self.assertBlocked(self.run_hook(stop_input(self.root, active=True)), "owner review required")

    def test_failure_after_one_retry_exits_1_and_lets_claude_stop(self):
        self.gate(verify=python("pass"))
        result = self.run_hook(stop_input(self.root, active=True))
        self.assertEqual(result.returncode, 1)
        self.assertIn("still failing after one retry", result.stderr)

    @unittest.skipUnless(os.name == "posix", "automatic verification is supported on POSIX Docker hosts")
    def test_reviewed_snapshot_and_constrained_execution(self):
        sys.path.insert(0, os.path.join(support.REPO_ROOT, "engine", "tools"))
        import close_review
        with tempfile.TemporaryDirectory() as owner:
            root = Path(self.root)
            (root / "code.txt").write_text("reviewed")
            (root / "extra.txt").write_text("outside snapshot")
            command = ["cat", "code.txt"]
            with mock.patch.object(close_review, "_home", return_value=Path(owner)):
                path = close_review._policy_path(self.root, command)
                close_review._review_store(path, self.root, create=True)
                record = {"image": "sha256:" + "a" * 64, "inputs": ["code.txt"],
                          "digest": close_review.snapshot(self.root, ["code.txt"])}
                path.write_text(json.dumps(record))
                path.chmod(0o600)
                def check_runner(args, **options):
                    self.assertIn("--network=none", args)
                    self.assertIn("--read-only", args)
                    self.assertIn("--cap-drop=ALL", args)
                    self.assertIn("--user=65534:65534", args)
                    self.assertEqual(set(options["env"]), {"HOME", "PATH", "DOCKER_CONFIG"})
                    files = Path(options["cwd"]) / "reviewed"
                    self.assertEqual(sorted(f.name for f in files.iterdir()), ["code.txt"])
                    return subprocess.CompletedProcess(args, 0)
                with mock.patch.object(close_review.shutil, "which", return_value="/trusted/docker"), mock.patch.object(
                        close_review.subprocess, "run", side_effect=check_runner) as runner:
                    self.assertEqual(close_review.execute(command, self.root, 5), (0, ""))
                    (root / "code.txt").write_text("new revision")
                    with self.assertRaisesRegex(close_review.ReviewError, "inputs changed"):
                        close_review.execute(command, self.root, 5)
                    self.assertEqual(runner.call_count, 1)

    def test_background_tasks_skip_the_gate(self):
        self.gate(verify=python("import sys; sys.exit(1)"))
        task = {"id": "t1", "type": "subagent", "status": "running", "description": "build"}
        result = self.run_hook(stop_input(self.root, background=[task]))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

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
