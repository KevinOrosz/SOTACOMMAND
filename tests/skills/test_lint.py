"""skills.py lint: one failing fixture per rule, plus the clean baselines."""

import json
import subprocess
import sys

from .helpers import (
    REPO,
    SKILLS_PY,
    TreeTestCase,
    base_files,
    case_files,
    case_yaml,
    prompt_md,
    skill_md,
    stable_files,
    summary_doc,
)

from skillslib.lint import RULES, lint_roots  # noqa: E402

CASE = "packs/demo/evals/alpha/alpha-basic/"
SKILL = "packs/demo/skills/alpha/SKILL.md"
MANIFEST = "packs/demo/.claude-plugin/plugin.json"


def manifest(**changes):
    data = json.loads(base_files()[MANIFEST])
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data)


def frontmatter_skill(frontmatter: str) -> str:
    return "---\n" + frontmatter + "---\n\n# Alpha\n"


VALID_META = (
    "metadata:\n"
    '  version: "1.0.0"\n'
    "  status: experimental\n"
    "  pack: demo\n"
    "  min-tier: low\n"
    "  roles: builder\n"
    "  gate: none\n"
)


def other_plugin(skill: str = "alpha") -> dict:
    files = {
        "packs/other/.claude-plugin/plugin.json": json.dumps(dict(json.loads(manifest()), name="other-pack")),
        "packs/other/README.md": "# other\n",
        "packs/other/skills/{}/SKILL.md".format(skill): skill_md(name=skill, pack="other"),
    }
    return files


def second_skill(name: str, with_case: str = "") -> dict:
    files = {"packs/demo/skills/{}/SKILL.md".format(name): skill_md(name=name)}
    if with_case:
        files.update(case_files(skill=name, case=with_case))
    return files


SUMMARY_PATH = "packs/demo/evals/results/2026-11-01/summary.json"

# code -> (description, overrides, base). base None means the default tree.
FIXTURES = {
    "PLG001": ("plugin directory without a manifest", {"packs/stray/README.md": "# stray\n"}, None),
    "PLG002": ("manifest that isn't JSON", {MANIFEST: "{not json"}, None),
    "PLG003": ("plugin name that isn't kebab-case", {MANIFEST: manifest(name="Demo_Pack")}, None),
    "PLG004": ("plugin name with a reserved word", {MANIFEST: manifest(name="claude-helpers")}, None),
    "PLG005": ("plugin without a README", {"packs/demo/README.md": None}, None),
    "PLG006": ("version that isn't semver", {MANIFEST: manifest(version="1.0")}, None),
    "PLG007": ("missing description", {MANIFEST: manifest(description=None)}, None),
    "PLG008": ("missing author", {MANIFEST: manifest(author=None)}, None),
    "PLG009": ("top-level bin/", {"packs/demo/bin/tool": "#!/bin/sh\n"}, None),
    "PLG010": ("CLAUDE.md at the plugin root", {"packs/demo/CLAUDE.md": "# notes\n"}, None),
    "PLG011": ("manifest path without ./", {MANIFEST: manifest(skills="extra-skills/")}, None),
    "PLG012": (
        "stdio MCP server",
        {"packs/demo/.mcp.json": json.dumps({"mcpServers": {"local": {"command": "node", "args": ["server.js"]}}})},
        None,
    ),
    "PLG013": ("settings.json with an ignored key", {"packs/demo/settings.json": '{"agent": "x", "theme": "dark"}'}, None),
    "PLG014": ("SKILL.md outside a skill folder", {"packs/demo/docs/SKILL.md": skill_md()}, None),
    "SKL001": ("SKILL.md without frontmatter", {SKILL: "# Alpha\n\nNo frontmatter here.\n"}, None),
    "SKL002": (
        "block scalar in the frontmatter",
        {SKILL: frontmatter_skill("name: alpha\ndescription: |\n  Multi-line text.\n" + VALID_META)},
        None,
    ),
    "SKL003": (
        "non-portable top-level key",
        {SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\nversion: 1.0.0\n" + VALID_META)},
        None,
    ),
    "SKL004": ("name with consecutive hyphens", {"packs/demo/skills/al--pha/SKILL.md": skill_md(name="al--pha")}, None),
    "SKL005": ("name that doesn't match its folder", {SKILL: skill_md(name="beta")}, None),
    "SKL006": ("name with a reserved word", second_skill("claude-tools"), None),
    "SKL007": ("description with angle brackets", {SKILL: skill_md(description="Writes <b>bold</b> greetings.")}, None),
    "SKL008": (
        "compatibility over 500 characters",
        {SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\ncompatibility: " + "x" * 501 + "\n" + VALID_META)},
        None,
    ),
    "SKL009": (
        "allowed-tools written as a list",
        {SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\nallowed-tools: [Read, Grep]\n" + VALID_META)},
        None,
    ),
    "SKL010": (
        "metadata value that YAML reads as a number",
        {SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\n" + VALID_META.replace('"1.0.0"', "1.0"))},
        None,
    ),
    "SKL011": (
        "missing required metadata key",
        {SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\n" + VALID_META.replace("  gate: none\n", ""))},
        None,
    ),
    "SKL012": ("unknown status", {SKILL: skill_md(status="beta")}, None),
    "SKL013": ("retired skill without replaces", {SKILL: skill_md(status="retired")}, None),
    "SKL014": ("stable skill with one eval case", {}, stable_files(cases=1)),
    "SKL015": ("stable skill whose delta is too small", {}, stable_files(delta=0.1)),
    "SKL016": ("scaffold placeholder in the description", {SKILL: skill_md(description="TODO say what this does.")}, None),
    "SKL017": ("SKILL.md over 500 lines", {SKILL: skill_md(body="line\n" * 600)}, None),
    "SKL018": (
        "replaces names a missing skill",
        {SKILL: skill_md(status="retired", extra_meta="  replaces: gamma\n")},
        None,
    ),
    "SKL019": ("skill name reused by another pack", other_plugin("alpha"), None),
    "SKL020": ("stable skill whose qualifying run didn't pin the model", {}, stable_files(model_pinned=False)),
    "EVL001": (
        "case outside evals/<skill>/<case>/",
        dict(
            (path.replace("packs/demo/evals/alpha/loose-case/", "packs/demo/evals/loose-case/"), content)
            for path, content in case_files(case="loose-case").items()
        ),
        None,
    ),
    "EVL002": ("case named after a Windows device", case_files(case="con"), None),
    "EVL003": ("two cases with one name", second_skill("beta", with_case="alpha-basic"), None),
    "EVL004": ("empty prompt", {CASE + "prompt.md": prompt_md(body="")}, None),
    "EVL005": ("unknown prompt.md key", {CASE + "prompt.md": prompt_md(extra="difficulty: hard\n")}, None),
    "EVL006": ("case that pins a model", {CASE + "prompt.md": prompt_md(extra="model: some-model\n")}, None),
    "EVL007": ("tags without the skill", {CASE + "prompt.md": prompt_md().replace("tags: [alpha]", "tags: [smoke]")}, None),
    "EVL008": ("unquoted schema_version", {CASE + "case.yaml": case_yaml(schema="1.1")}, None),
    "EVL009": ("scaffold script that doesn't exist", {CASE + "case.yaml": case_yaml(script="missing.sh")}, None),
    "EVL010": ("case without graders", {CASE + "graders": None}, None),
    "EVL011": ("unknown grader type", {CASE + "graders/20-output.md": "---\ntype: contains\npath: hello.txt\n---\n"}, None),
    "EVL012": (
        "llm grader without PASS/FAIL",
        {CASE + "graders/30-judge.md": "---\ntype: llm\n---\n\nThe greeting should be friendly.\n"},
        None,
    ),
    "EVL013": ("no Skill indicator grader", {CASE + "graders/10-skill-fired.md": None}, None),
    "EVL014": ("scaffold placeholder in the prompt", {CASE + "prompt.md": prompt_md(body="TODO write the prompt.")}, None),
    "RES001": (
        "summary with a field outside the format",
        {SUMMARY_PATH: json.dumps(dict(summary_doc([("alpha-basic", "alpha", 1.0, 0.5, 3, 3)]), model="x"))},
        None,
    ),
    "RES002": (
        "raw eval output in results/",
        {"packs/demo/evals/results/2026-11-01T00-00-00-000Z/aggregate-result.json": "{}"},
        None,
    ),
    "RES003": (
        "summary attributes a case to an unknown skill",
        {SUMMARY_PATH: json.dumps(summary_doc([("alpha-basic", "ghost", 1.0, 0.5, 3, 3)]))},
        None,
    ),
    "RES004": (
        "summary names a case that was renamed since",
        {SUMMARY_PATH: json.dumps(summary_doc([("alpha-old-name", "alpha", 1.0, 0.5, 3, 3)]))},
        None,
    ),
}


# A stable skill with one case folder can't have a qualifying run that only
# names current cases (that run would cover one case and trip SKL015), so
# the SKL014 fixture's summary also names two removed cases.
COMPANIONS = {"SKL014": {"RES004"}}


class LintRuleTests(TreeTestCase):
    def codes(self) -> set:
        result = lint_roots(self.roots())
        return {p.code for p in result.problems}

    def test_every_rule_has_a_failing_fixture(self) -> None:
        self.assertEqual(sorted(FIXTURES), sorted(RULES))

    def test_base_tree_is_clean(self) -> None:
        self.tree()
        result = lint_roots(self.roots())
        self.assertEqual(result.problems, [], [p.message for p in result.problems])
        self.assertEqual((result.plugins, result.skills, result.cases), (1, 1, 1))

    def test_stable_tree_is_clean(self) -> None:
        self.tree(base=stable_files())
        result = lint_roots(self.roots())
        self.assertEqual(result.problems, [], [p.message for p in result.problems])

    def test_each_fixture_trips_exactly_its_rule(self) -> None:
        for code, (description, overrides, base) in sorted(FIXTURES.items()):
            with self.subTest(code=code, fixture=description):
                self.tearDown()
                self.setUp()
                self.tree(overrides, base=base)
                result = lint_roots(self.roots())
                codes = {p.code for p in result.problems}
                expected = {code} | COMPANIONS.get(code, set())
                self.assertEqual(codes, expected, "\n".join("{} {}".format(p.code, p.message) for p in result.problems))
                severities = {p.severity for p in result.problems if p.code == code}
                self.assertEqual(severities, {RULES[code][0]})


class LintDetailTests(TreeTestCase):
    def messages(self, code: str) -> list:
        return [p.message for p in lint_roots(self.roots()).problems if p.code == code]

    def test_crlf_and_bom_are_reported(self) -> None:
        self.tree({SKILL: skill_md().replace("\n", "\r\n")})
        self.assertTrue(any("CRLF" in m for m in self.messages("SKL001")))
        self.tree({SKILL: "﻿" + skill_md()})
        self.assertTrue(any("byte-order mark" in m for m in self.messages("SKL001")))

    def test_yaml_11_booleans_and_dates_are_not_strings(self) -> None:
        meta = VALID_META.replace("  gate: none\n", "  gate: none\n  reviewed: 2026-11-01\n  public: yes\n")
        self.tree({SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\n" + meta)})
        messages = self.messages("SKL010")
        self.assertEqual(len(messages), 2, messages)
        self.assertTrue(any("timestamp" in m for m in messages))
        self.assertTrue(any("bool" in m for m in messages))

    def test_flow_style_metadata_is_rejected(self) -> None:
        flow = '{version: "1.0.0", status: experimental, pack: demo, min-tier: low, roles: builder, gate: none}'
        self.tree({SKILL: frontmatter_skill("name: alpha\ndescription: Greets.\nmetadata: " + flow + "\n")})
        self.assertTrue(any("indented block" in m for m in self.messages("SKL010")))

    def test_unquoted_colon_in_description_is_a_subset_error(self) -> None:
        self.tree({SKILL: frontmatter_skill("name: alpha\ndescription: Use when: anything\n" + VALID_META)})
        problems = [p for p in lint_roots(self.roots()).problems if p.code == "SKL002"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].line, 3)

    def test_stable_rule_reports_every_shortfall(self) -> None:
        self.tree(base=stable_files(delta=0.1, score=0.7, runs=2))
        messages = self.messages("SKL015")
        self.assertEqual(len(messages), 1)
        for fragment in ("below 0.80", "below +0.20", "at least 3 of each"):
            self.assertIn(fragment, messages[0])

    def test_newest_complete_run_decides_the_stable_rule(self) -> None:
        files = stable_files()
        newer = summary_doc(
            [(n, "alpha", 0.9, 0.0, 3, 3) for n in ("alpha-basic", "alpha-two", "alpha-three")],
            started_at="2026-11-03T10:00:00.000Z",
        )
        files["packs/demo/evals/results/2026-11-03/summary.json"] = json.dumps(newer)
        self.tree(base=files)
        self.assertEqual(len(self.messages("SKL015")), 1)

    def test_the_newest_qualifying_run_must_pin_the_model(self) -> None:
        files = stable_files()
        cases = [(n, "alpha", 0.9, 0.3, 3, 3) for n in ("alpha-basic", "alpha-two", "alpha-three")]
        unpinned = summary_doc(cases, started_at="2026-11-03T10:00:00.000Z", model_pinned=False)
        files["packs/demo/evals/results/2026-11-03/summary.json"] = json.dumps(unpinned)
        self.tree(base=files)
        messages = self.messages("SKL020")
        self.assertEqual(len(messages), 1)
        self.assertIn("evals/results/2026-11-03/summary.json", messages[0])
        self.assertEqual(self.messages("SKL015"), [])
        pinned_later = summary_doc(cases, started_at="2026-11-04T10:00:00.000Z")
        self.tree({"packs/demo/evals/results/2026-11-04/summary.json": json.dumps(pinned_later)})
        self.assertEqual(self.messages("SKL020"), [])

    def test_no_qualifying_run_is_reported_once(self) -> None:
        files = stable_files()
        del files["packs/demo/evals/results/2026-11-01/summary.json"]
        self.tree(base=files)
        self.assertEqual(len(self.messages("SKL015")), 1)
        self.assertEqual(self.messages("SKL020"), [])

    def test_partial_and_one_arm_runs_never_qualify(self) -> None:
        files = stable_files()
        del files["packs/demo/evals/results/2026-11-01/summary.json"]
        cases = [(n, "alpha", 0.9, 0.3, 3, 3) for n in ("alpha-basic", "alpha-two", "alpha-three")]
        files["packs/demo/evals/results/a/summary.json"] = json.dumps(summary_doc(cases, partial=True))
        files["packs/demo/evals/results/b/summary.json"] = json.dumps(summary_doc(cases, ablation="none"))
        self.tree(base=files)
        self.assertEqual(len(self.messages("SKL015")), 1)

    def test_grader_option_errors(self) -> None:
        cases = {
            "regex without pattern": "---\ntype: regex\n---\n",
            "inline regex flags": "---\ntype: regex\npattern: '(?i)hello'\n---\n",
            "bad regex flags": "---\ntype: regex\npattern: hi\nflags: q\n---\n",
            "bad match mode": "---\ntype: regex\npattern: hi\nmatch: some\n---\n",
            "bad target": "---\ntype: regex\npattern: hi\ntarget: everything\n---\n",
            "negative weight": "---\ntype: file_exists\npath: a\nweight: -1\n---\n",
            "unknown arm": "---\ntype: file_exists\npath: a\narm: never\n---\n",
            "option of another type": "---\ntype: file_exists\npath: a\npattern: x\n---\n",
            "min above max": "---\ntype: tool_used\ntool: Read\nmin: 3\nmax: 1\n---\n",
            "exists not boolean": "---\ntype: file_exists\npath: a\nexists: maybe\n---\n",
            "tool_order without after": "---\ntype: tool_order\nbefore: Read\n---\n",
            "baseline file missing": "---\ntype: baseline\nbaseline_file: ref.jsonl\n---\n\nPASS if same. FAIL if not.\n",
            "no frontmatter": "PASS if fine.\n",
        }
        for label, content in cases.items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.tree({CASE + "graders/40-extra.md": content})
                self.assertTrue(self.messages("EVL011"), label)

    def test_valid_grader_shapes_pass(self) -> None:
        graders = {
            "40-regex.md": "---\ntype: regex\ntarget:\n  source: file\n  path: hello.txt\npattern: '^hello$'\nflags: mi\nmatch: 'count:1'\nweight: 2\n---\n",
            "41-flow-target.md": "---\ntype: regex\ntarget: {source: file, path: hello.txt}\npattern: hello\nmatch: not_contains\n---\n",
            "42-order.md": "---\ntype: tool_order\nbefore: Read\nafter: {tool: Write, input_match: hello}\n---\n",
            "43-never.md": "---\ntype: tool_used\ntool: Bash\nmin: 0\nmax: 0\narm: both\n---\n",
            "44-absent.md": "---\ntype: file_exists\npath: '*.log'\nexists: false\n---\n",
        }
        self.tree({CASE + "graders/" + name: content for name, content in graders.items()})
        problems = lint_roots(self.roots()).problems
        self.assertEqual(problems, [], [p.message for p in problems])

    def test_stdio_servers_declared_in_the_manifest_are_reported_once_each(self) -> None:
        servers = {"local": {"command": "node"}, "remote": {"type": "http", "url": "https://example.com/mcp"}}
        self.tree({MANIFEST: manifest(mcpServers=servers)})
        self.assertEqual(len(self.messages("PLG012")), 1)
        self.tree(
            {
                MANIFEST: manifest(mcpServers=["./servers.json", {"other": {"command": "python3"}}]),
                "packs/demo/servers.json": json.dumps({"mcpServers": {"filed": {"command": "node"}}}),
            }
        )
        self.assertEqual(len(self.messages("PLG012")), 2)

    def test_case_yaml_context_must_stay_inside_the_case(self) -> None:
        self.tree({CASE + "case.yaml": case_yaml(script="../../../../README.md")})
        self.assertTrue(self.messages("EVL009"))

    def test_a_skill_can_live_in_a_manifest_declared_directory(self) -> None:
        self.tree(
            {
                MANIFEST: manifest(skills=["./extra/"]),
                "packs/demo/extra/beta/SKILL.md": skill_md(name="beta"),
            }
        )
        result = lint_roots(self.roots())
        self.assertEqual(result.problems, [], [p.message for p in result.problems])
        self.assertEqual(result.skills, 2)


class LintCliTests(TreeTestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SKILLS_PY)] + list(args), cwd=str(self.root), capture_output=True, text=True
        )

    def test_exit_codes(self) -> None:
        self.tree()
        clean = self.run_cli("lint", "packs", "labs")
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn("0 error(s), 0 warning(s)", clean.stdout)

        self.tree({SKILL: skill_md(status="beta")})
        broken = self.run_cli("lint", "packs", "labs")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("packs/demo/skills/alpha/SKILL.md:", broken.stdout)
        self.assertIn("error SKL012", broken.stdout)

        missing = self.run_cli("lint", "nowhere")
        self.assertEqual(missing.returncode, 2)

    def test_warnings_fail_only_under_strict(self) -> None:
        self.tree({SKILL: skill_md(description="TODO say what this does.")})
        self.assertEqual(self.run_cli("lint", "packs").returncode, 0)
        self.assertEqual(self.run_cli("lint", "--strict", "packs").returncode, 1)

    def test_list_rules_prints_every_code(self) -> None:
        listed = self.run_cli("lint", "--list-rules")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual([line.split()[0] for line in listed.stdout.splitlines()], list(RULES))


class RepositoryLintTests(TreeTestCase):
    def test_committed_packs_and_labs_lint_clean_under_strict(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SKILLS_PY), "lint", "--strict", "engine", "packs", "labs"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

