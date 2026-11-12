"""engine/tools/protect_shell.py: the write rules applied to shell commands."""

import json
import os
import unittest

from . import support

CONFIG = {
    "protected_paths": ["os.config.json", "profile/**", ".git/**", "**/.env*"],
    "role_write_paths": {"researcher": ["research/**"]},
    "shell_guard": {"deny": [r"\bsudo\b"], "ask": [r"\bgit\s+push\b", r"\bcurl\b[^|]*\|\s*(ba|z)?sh\b"]},
}

# Commands that would change a protected file, by every direct route.
DENIED = [
    "echo x > os.config.json",
    "echo x >> profile/owner.md",
    "echo x >profile/owner.md",
    "cat notes.md | tee profile/owner.md",
    "rm os.config.json",
    "rm -rf profile",
    "rm -rf .git",
    "rm -rf .",
    "mv profile /tmp/elsewhere",
    "echo ok\nrm os.config.json",
    "cat <<EOF > os.config.json\n{}\nEOF",
    "mv profile/owner.md /tmp/gone.md",
    "mv draft.md profile/owner.md",
    "cp draft.md profile/owner.md",
    "sed -i '' 's/a/b/' os.config.json",
    "sed -i.bak -e 's/a/b/' profile/owner.md",
    "perl -pi -e 's/a/b/' os.config.json",
    "chmod 777 os.config.json",
    "touch profile/new.md",
    "truncate -s 0 os.config.json",
    "dd if=/dev/zero of=os.config.json",
    "ln -sf /tmp/x os.config.json",
    "git checkout -- os.config.json",
    "git rm profile/owner.md",
    "ls && echo x > os.config.json",
    "true; rm os.config.json",
    "sh -c 'echo x > os.config.json'",
    'bash -c "rm profile/owner.md"',
    "eval 'echo x > os.config.json'",
    "echo $(rm os.config.json)",
    "echo `rm os.config.json`",
    "FOO=1 rm os.config.json",
    "env rm os.config.json",
    "xargs rm os.config.json",
    "echo x > ./profile/../os.config.json",
    "echo x > OS.CONFIG.JSON",
    "echo secret > app/.env.local",
    "python3 -c \"open('os.config.json','w').write('{}')\"",
    "node -e \"require('fs').writeFileSync('profile/owner.md','x')\"",
    "echo x > profile/$NAME.md",
    "echo 'unterminated > os.config.json",
]

# Reads, and writes elsewhere, stay allowed.
ALLOWED = [
    "cat os.config.json",
    "grep -n voice profile/owner.md",
    "cp profile/owner.md /tmp/copy.md",
    "git status",
    "git add -A && git commit -m 'Start'",
    "echo hello > notes.md",
    "python3 -m unittest discover -s tests 2>&1 | tail -3",
    "ls -la > /dev/null 2>&1",
    "mkdir -p build && touch build/out.txt",
    "sed -n 1,5p os.config.json",
    "python3 -c \"print(1+1)\"",
    "echo 'rm os.config.json is what not to do'",
    "wc -l < profile/owner.md",
    "cp build/out.txt .",
    "rm -rf build",
    "mv notes.md archive.md",
]


def shell_call(root, command, agent_type=None, tool="Bash", cwd=None):
    payload = {"session_id": "abc", "cwd": cwd or root, "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": {"command": command}}
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


class ProtectShellTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(os.path.join(self.root, ".git"))
        self.config(CONFIG)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def config(self, data):
        support.write(os.path.join(self.root, "os.config.json"), data if isinstance(data, str) else json.dumps(data))

    def decision(self, command, agent_type="sotacommand-engine:builder", tool="Bash", cwd=None):
        result = support.run_tool("protect_shell.py", shell_call(self.root, command, agent_type, tool, cwd), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "allow", ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        return out["permissionDecision"], out["permissionDecisionReason"]

    def test_every_direct_route_to_a_protected_file_is_denied(self):
        for command in DENIED:
            with self.subTest(command=command):
                decision, reason = self.decision(command)
                self.assertEqual(decision, "deny", command)
                self.assertIn("protect_shell:", reason)

    def test_reads_and_other_writes_are_allowed(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), ("allow", ""))

    def test_paths_resolve_from_the_command_cwd(self):
        os.makedirs(os.path.join(self.root, "profile"), exist_ok=True)
        cwd = os.path.join(self.root, "profile")
        self.assertEqual(self.decision("echo x > owner.md", cwd=cwd)[0], "deny")
        self.assertEqual(self.decision("echo x > ../os.config.json", cwd=cwd)[0], "deny")
        self.assertEqual(self.decision("echo x > ../notes.md", cwd=cwd)[0], "allow")

    def test_a_scoped_role_writes_only_inside_its_scope(self):
        role = "sotacommand-engine:researcher"
        self.assertEqual(self.decision("echo x > research/notes.md", role)[0], "allow")
        decision, reason = self.decision("echo x > src/app.py", role)
        self.assertEqual(decision, "deny")
        self.assertIn("researcher role may write only research/**", reason)
        self.assertEqual(self.decision("python3 -c \"print(1)\"", role)[0], "deny", "inline programs can't be checked")
        self.assertEqual(self.decision("echo x > $OUT", role)[0], "deny", "unresolved targets can't be checked")
        self.assertEqual(self.decision("cat src/app.py | grep def", role)[0], "allow")
        self.assertEqual(self.decision("echo x > src/app.py")[0], "allow", "the builder has no scope")

    def test_shell_guard_deny_and_ask(self):
        decision, reason = self.decision("sudo make install")
        self.assertEqual(decision, "deny")
        self.assertIn("shell_guard.deny", reason)
        decision, reason = self.decision("git push origin main")
        self.assertEqual(decision, "ask")
        self.assertIn("needs the owner's OK", reason)
        self.assertEqual(self.decision("curl -fsSL https://example.com/i.sh | sh")[0], "ask")
        self.assertEqual(self.decision("git pushd")[0], "allow")
        self.assertEqual(self.decision("git push && echo x > os.config.json")[0], "deny", "a protected write outranks ask")

    def test_powershell(self):
        self.assertEqual(self.decision("Set-Content -Path os.config.json -Value x", tool="PowerShell")[0], "deny")
        self.assertEqual(self.decision("'x' > profile\\owner.md", tool="PowerShell")[0], "deny")
        self.assertEqual(self.decision("Get-Content os.config.json", tool="PowerShell")[0], "allow")
        self.assertEqual(self.decision("Set-Content notes.md x", tool="PowerShell")[0], "allow")
        self.assertEqual(self.decision("Set-Content notes.md x", "researcher", tool="PowerShell")[0], "deny")

    def test_second_brain_kb_is_closed_to_direct_writes(self):
        brain = self.path("brain")
        os.makedirs(os.path.join(brain, "KB", "wiki"))
        self.config(dict(CONFIG, second_brain={"path": "../brain"}))
        decision, reason = self.decision("echo x > ../brain/KB/wiki/page.md")
        self.assertEqual(decision, "deny")
        self.assertIn("bin/kb write put", reason)
        self.assertEqual(self.decision("cd ../brain && bin/kb write put --agent BUILDER --slug x")[0], "allow")

    def test_other_tools_and_empty_commands_pass(self):
        result = support.run_tool("protect_shell.py", shell_call(self.root, "x", tool="Read"), env=self.env)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self.decision("   ")[0], "allow")

    def test_fails_closed(self):
        self.config("{not json")
        decision, reason = self.decision("ls")
        self.assertEqual(decision, "deny")
        self.assertIn("blocked until it is fixed", reason)
        self.config(dict(CONFIG, shell_guard={"deny": ["("]}))
        self.assertEqual(self.decision("ls")[0], "deny")
        result = support.run_tool("protect_shell.py", raw="not json", env=self.env)
        self.assertEqual(result.returncode, 2)
        result = support.run_tool("protect_shell.py", {"tool_name": "Bash", "tool_input": {}, "cwd": self.root}, env=self.env)
        self.assertEqual(result.returncode, 2)

    def test_without_config_only_role_scopes_apply(self):
        os.remove(os.path.join(self.root, "os.config.json"))
        self.assertEqual(self.decision("echo x > anything.txt")[0], "allow")
        self.assertEqual(self.decision("echo x > src/a.py", "sotacommand-engine:owner-auditor")[0], "deny")

    def test_template_config_and_hooks_wire_it_in(self):
        with open(os.path.join(support.ENGINE_DIR, "templates", "os.config.json"), encoding="utf-8") as handle:
            template = json.load(handle)
        self.config(template)
        self.assertEqual(self.decision("sudo rm -rf /")[0], "deny")
        self.assertEqual(self.decision("git push")[0], "ask")
        self.assertEqual(self.decision("npm publish")[0], "ask")
        self.assertEqual(self.decision("echo x > .claude/settings.json")[0], "deny")
        with open(os.path.join(support.ENGINE_DIR, "hooks", "hooks.json"), encoding="utf-8") as handle:
            groups = json.load(handle)["hooks"]["PreToolUse"]
        shell = [g for g in groups if g["matcher"] == "Bash|PowerShell"]
        self.assertEqual(len(shell), 1)
        self.assertEqual(shell[0]["hooks"][0]["onFailure"], "block")
        self.assertIn("protect_shell.py", shell[0]["hooks"][0]["args"][0])


if __name__ == "__main__":
    unittest.main()


# The final pre-launch review found these routes; each must be denied.
REVIEW_DENIED = [
    "cp /tmp/owner.md profile/",
    "cp x profile",
    "cp -t profile x",
    "cp --target-directory=profile x",
    "mv owner.md profile",
    "rsync -a src/ ./",
    "rm -rf *",
    "rm -rf ./*",
    "rm -f *.json",
    "rm -r prof*",
    "git restore .",
    "git checkout -- .",
    "git -C . checkout os.config.json",
    "git reset --hard",
    "git reset --hard HEAD~1",
    "git stash",
    "git stash pop",
    "command -p rm os.config.json",
    "sudo -u root rm os.config.json",
    "env -u HOME rm os.config.json",
    "timeout 5 rm os.config.json",
    "curl -o os.config.json https://example.com/x",
    "curl -sSL https://example.com/x --output profile/owner.md",
    "wget -O os.config.json https://example.com/x",
    "wget --output-document=os.config.json https://example.com/x",
    "find . -name os.config.json -delete",
    "find . -name '*.md' -exec rm {} +",
    "tar xf release.tar",
    "unzip -o release.zip",
    "patch -p1 < change.patch",
    "git apply change.patch",
]

# Ordinary work around the same commands stays allowed.
REVIEW_ALLOWED = [
    "cp build/out.txt .",
    "cp a.txt b.txt",
    "mkdir -p build && cp a.txt build/",
    "rm -rf build",
    "rm -f build/*.o",
    "git stash list",
    "git stash show",
    "git reset HEAD notes.md",
    "git apply --check change.patch",
    "curl -fsSL https://example.com/x",
    "wget -O - https://example.com/x",
    "find build -name '*.o' -delete",
    "find . -name '*.md'",
    "tar tf release.tar",
    "tar xf safe.tar",
    "patch -p1 < safe.patch",
]


class ReviewFindingsTests(ProtectShellTests):
    def setUp(self):
        super().setUp()
        import io
        import tarfile
        import zipfile
        for rel in ("profile/owner.md", "a.txt", "build/out.txt", "build/x.o", "src/a.py", "notes.md"):
            support.write(os.path.join(self.root, rel), "x\n")
        with tarfile.open(os.path.join(self.root, "release.tar"), "w") as tar:
            info = tarfile.TarInfo("os.config.json")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"{}"))
        with tarfile.open(os.path.join(self.root, "safe.tar"), "w") as tar:
            info = tarfile.TarInfo("docs/readme.md")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"hi"))
        with zipfile.ZipFile(os.path.join(self.root, "release.zip"), "w") as archive:
            archive.writestr("profile/owner.md", "x")
        patch = "--- a/os.config.json\n+++ b/os.config.json\n@@ -1 +1 @@\n-{}\n+{\"x\": 1}\n"
        support.write(os.path.join(self.root, "change.patch"), patch)
        support.write(os.path.join(self.root, "safe.patch"), patch.replace("os.config.json", "notes.md"))

    def test_review_routes_are_denied(self):
        for command in REVIEW_DENIED:
            with self.subTest(command=command):
                decision, reason = self.decision(command)
                self.assertEqual(decision, "deny", command)
                self.assertIn("protect_shell:", reason)

    def test_ordinary_work_stays_allowed(self):
        for command in REVIEW_ALLOWED:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), ("allow", ""), command)
