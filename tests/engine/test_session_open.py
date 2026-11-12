"""engine/tools/session_open.py: the SessionStart preflight report."""

import json
import os
import unittest

from . import support


def session_start(root, source="startup", agent_type=None):
    payload = {
        "session_id": "abc123",
        "transcript_path": os.path.join(root, ".transcript.jsonl"),
        "cwd": root,
        "hook_event_name": "SessionStart",
        "source": source,
    }
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


class SessionOpenTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(self.root)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def config(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        support.write(os.path.join(self.root, "os.config.json"), text)

    def memory(self, name, lines):
        support.write(os.path.join(self.root, "memory", name), "".join("line %d\n" % i for i in range(lines)))

    def run_hook(self, payload=None, args=(), env=None):
        payload = session_start(self.root) if payload is None else payload
        return support.run_tool("session_open.py", payload, env=env or self.env, args=args)

    def hook_output(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return output, output["hookSpecificOutput"]["additionalContext"]

    def test_reports_each_file_against_its_caps(self):
        self.config({"memory_caps": [
            {"path": "memory/HANDOFF.md", "max_bytes": 900, "max_lines": 15},
            {"path": "memory/lessons.md", "max_lines": 3},
            {"path": "memory/backlog.md", "max_bytes": 100},
            {"path": "memory/bad.md"},
        ]})
        self.memory("HANDOFF.md", 4)
        self.memory("lessons.md", 5)
        output, context = self.hook_output(self.run_hook())
        lines = context.splitlines()
        self.assertIn("session-open: memory caps: 1 OK, 1 OVER, 1 MISSING, 1 INVALID.", lines)
        self.assertIn("OK memory/HANDOFF.md: 28 bytes, 4 lines (cap 900 bytes, cap 15 lines)", lines)
        self.assertIn("OVER memory/lessons.md: 35 bytes, 5 lines (cap 3 lines)", lines)
        self.assertIn("MISSING memory/backlog.md (cap 100 bytes)", lines)
        self.assertIn("INVALID memory/bad.md: needs max_bytes or max_lines", lines)
        self.assertIn("memory/lessons.md", output["systemMessage"])
        self.assertIn("memory/backlog.md", output["systemMessage"])

    def test_all_ok_has_no_user_message(self):
        self.config({"memory_caps": [{"path": "memory/HANDOFF.md", "max_lines": 15}]})
        self.memory("HANDOFF.md", 2)
        output, context = self.hook_output(self.run_hook())
        self.assertNotIn("systemMessage", output)
        self.assertIn("1 OK, 0 OVER", context)

    def test_byte_cap_alone_can_flag_a_file(self):
        self.config({"memory_caps": [{"path": "memory/HANDOFF.md", "max_bytes": 10}]})
        self.memory("HANDOFF.md", 2)
        _, context = self.hook_output(self.run_hook())
        self.assertIn("OVER memory/HANDOFF.md: 14 bytes", context)

    def test_session_source_and_main_agent_are_reported(self):
        self.config({"memory_caps": []})
        payload = session_start(self.root, source="resume", agent_type="sotacommand-engine:orchestrator")
        _, context = self.hook_output(self.run_hook(payload))
        self.assertIn("session source resume; main agent sotacommand-engine:orchestrator.", context)
        self.assertIn("sets no memory_caps", context)

    def test_without_os_config(self):
        output, context = self.hook_output(self.run_hook())
        self.assertIn("no os.config.json in %s" % self.root, context)
        self.assertIn(os.path.join("templates", "os.config.json"), context)
        self.assertNotIn("systemMessage", output)

    def test_invalid_os_config_is_reported_not_raised(self):
        self.config("{ broken")
        output, context = self.hook_output(self.run_hook())
        self.assertIn("not valid JSON", context)
        self.assertIn("systemMessage", output)

    def test_invalid_entries_and_unsafe_paths(self):
        self.config({"memory_caps": [
            "memory/HANDOFF.md",
            {"path": "../outside.md", "max_bytes": 10},
            {"path": "memory/x.md", "max_bytes": -1},
            {"path": "memory/y.md", "max_lines": True},
        ]})
        _, context = self.hook_output(self.run_hook())
        self.assertIn("0 OK, 0 OVER, 0 MISSING, 4 INVALID", context)
        self.assertIn("path must stay inside the project", context)
        self.assertIn("max_bytes must be a positive integer", context)
        self.assertIn("max_lines must be a positive integer", context)

    def test_caps_must_be_a_list(self):
        self.config({"memory_caps": {"memory/HANDOFF.md": 10}})
        output, context = self.hook_output(self.run_hook())
        self.assertIn("must be a list", context)
        self.assertIn("systemMessage", output)

    def test_empty_or_invalid_stdin_still_reports(self):
        self.config({"memory_caps": [{"path": "memory/HANDOFF.md", "max_lines": 15}]})
        for raw in ("", "not json"):
            with self.subTest(stdin=raw):
                result = support.run_tool("session_open.py", raw=raw, env=self.env)
                self.hook_output(result)

    def test_text_mode_exit_codes(self):
        self.config({"memory_caps": [{"path": "memory/HANDOFF.md", "max_lines": 3}]})
        self.memory("HANDOFF.md", 2)
        ok = support.run_tool("session_open.py", raw="", env=self.env, args=["--text"])
        self.assertEqual(ok.returncode, 0)
        self.assertIn("OK memory/HANDOFF.md", ok.stdout)
        self.memory("HANDOFF.md", 9)
        over = support.run_tool("session_open.py", raw="", env=self.env, args=["--text"])
        self.assertEqual(over.returncode, 1)
        self.assertIn("OVER memory/HANDOFF.md", over.stdout)

    def test_root_is_found_from_cwd_without_the_environment(self):
        self.config({"memory_caps": [{"path": "memory/HANDOFF.md", "max_lines": 3}]})
        nested = os.path.join(self.root, "src", "pkg")
        os.makedirs(nested)
        payload = session_start(nested)
        _, context = self.hook_output(self.run_hook(payload, env=support.clean_env()))
        self.assertIn("MISSING memory/HANDOFF.md", context)

    def test_template_config_reports_cleanly(self):
        template = support.read(os.path.join(support.ENGINE_DIR, "templates", "os.config.json"))
        self.config(template)
        for name in ("HANDOFF", "lessons"):
            template = os.path.join(support.ENGINE_DIR, "templates", "memory", name + ".template.md")
            support.write(os.path.join(self.root, "memory", name + ".md"), support.read(template))
        output, context = self.hook_output(self.run_hook())
        self.assertIn("2 OK, 0 OVER, 0 MISSING, 0 INVALID", context)
        self.assertNotIn("systemMessage", output)


if __name__ == "__main__":
    unittest.main()


class BootParityTests(support.TempDirTestCase):
    """Folder, registry and router parity of the installed engine (BOOT_INVALID)."""

    def setUp(self):
        super().setUp()
        import shutil
        self.plugin = self.path("plugin")
        shutil.copytree(support.ENGINE_DIR, self.plugin)
        self.root = self.path("project")
        os.makedirs(self.root)

    def run_copy(self, text=True):
        import subprocess
        import sys
        args = [sys.executable, os.path.join(self.plugin, "tools", "session_open.py"), "--root", self.root]
        if text:
            args.append("--text")
        return subprocess.run(args, input="{}", capture_output=True, text=True,
                              env=support.clean_env(CLAUDE_PROJECT_DIR=self.root))

    def policy(self, **changes):
        path = os.path.join(self.plugin, "tools", "role_policy.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        data.update(changes)
        support.write(path, json.dumps(data))
        return data

    def test_generated_engine_passes(self):
        result = self.run_copy()
        self.assertIn("boot parity OK", result.stdout)
        self.assertNotIn("BOOT_INVALID", result.stdout)

    def test_missing_agent_file_is_boot_invalid(self):
        os.remove(os.path.join(self.plugin, "agents", "sensor.md"))
        result = self.run_copy()
        self.assertEqual(result.returncode, 1)
        self.assertIn("BOOT_INVALID role sensor is registered but agents/sensor.md is missing", result.stdout)
        self.assertIn("memory caps were not checked", result.stdout)

    def test_unregistered_agent_file_is_boot_invalid(self):
        support.write(os.path.join(self.plugin, "agents", "stowaway.md"), "---\nname: stowaway\n---\nbody\n")
        self.assertIn("BOOT_INVALID agents/stowaway.md is not in the registry", self.run_copy().stdout)

    def test_router_drift_is_boot_invalid(self):
        data = self.policy()
        self.policy(routes=[r for r in data["routes"] if r != "builder"])
        out = self.run_copy().stdout
        self.assertIn("BOOT_INVALID the router in agents/orchestrator.md names", out)

    def test_renamed_agent_is_boot_invalid(self):
        path = os.path.join(self.plugin, "agents", "builder.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        support.write(path, text.replace("\nname: builder\n", "\nname: coder\n", 1))
        self.assertIn("BOOT_INVALID agents/builder.md names agent 'coder', not 'builder'", self.run_copy().stdout)

    def test_hook_mode_tells_the_user_and_still_exits_zero(self):
        os.remove(os.path.join(self.plugin, "agents", "sensor.md"))
        result = self.run_copy(text=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn("BOOT_INVALID", output["systemMessage"])
        self.assertIn("BOOT_INVALID", output["hookSpecificOutput"]["additionalContext"])

    def test_plugin_without_registry_skips_the_check(self):
        os.remove(os.path.join(self.plugin, "tools", "role_policy.json"))
        out = self.run_copy().stdout
        self.assertIn("boot parity was not checked", out)
        self.assertNotIn("BOOT_INVALID", out)

    def test_a_project_set_up_with_another_version_is_reported(self):
        with open(os.path.join(self.plugin, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            version = json.load(handle)["version"]
        support.write(os.path.join(self.root, ".sotacommand", "install.json"), json.dumps({"version": version, "files": {}}))
        self.assertNotIn("was set up with version", self.run_copy().stdout)
        support.write(os.path.join(self.root, ".sotacommand", "install.json"), json.dumps({"version": "0.0.9", "files": {}}))
        self.assertIn("this project was set up with version 0.0.9 and the engine is %s" % version, self.run_copy().stdout)

    def test_boot_skill_stops_on_boot_invalid(self):
        with open(os.path.join(support.ENGINE_DIR, "skills", "os-boot", "SKILL.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("says `BOOT_INVALID`, stop", text)
