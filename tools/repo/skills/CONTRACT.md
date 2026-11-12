# Skills contract

This is the contract every skill in `packs/` and `labs/` follows, and the contract the tools in this folder enforce. `skills.py lint` checks it, `skills.py registry` publishes it, and CI runs both on every pull request.

- [Layout](#layout)
- [SKILL.md frontmatter](#skillmd-frontmatter)
- [The YAML subset](#the-yaml-subset)
- [Status and the stable rule](#status-and-the-stable-rule)
- [Eval cases](#eval-cases)
- [Running evals](#running-evals)
- [The redacted summary](#the-redacted-summary-summaryjson)
- [The registry](#the-registry)
- [Commands](#commands)
- [Lint rules](#lint-rules)

## Layout

Every directory under `packs/` and `labs/` is one Claude Code plugin:

```text
packs/<pack>/
├── .claude-plugin/plugin.json      name, version, description, author, license
├── README.md
├── skills/<skill>/
│   ├── SKILL.md                    frontmatter contract below, then the instructions
│   ├── references/                 optional, loaded on demand
│   └── scripts/                    optional
└── evals/
    ├── <skill>/<case>/             one directory per eval case, grouped by skill
    │   ├── prompt.md
    │   ├── case.yaml
    │   ├── place-files.sh
    │   ├── files/                  input files copied into the run's workspace
    │   └── graders/NN-name.md
    └── results/<date>/summary.json redacted results only
```

- `packs/` is for packs whose skills are stable. `labs/` holds experimental packs, which the marketplace lists with `defaultEnabled: false`. The one exception is `packs/example`, the reference layout for the toolchain, whose two skills are experimental.
- A pack has no top-level `bin/` (claude.ai and Cowork refuse to install a plugin that has one), no local stdio MCP server, and no `CLAUDE.md` (plugins never load it; instructions belong in a skill).
- Manifest component paths start with `./`.
- The plugin name is kebab-case and contains neither `claude` nor `anthropic`.
- Skill names are unique across every pack and lab, because the Codex adapter copies all of them into one `.agents/skills/` directory.

## SKILL.md frontmatter

The frontmatter uses only the six keys of the open Agent Skills format. claude.ai uploads and the Skills API reject any other top-level key with a hard error, so everything custom goes under `metadata` as flat strings.

```yaml
---
name: csv-summary
description: Summarize a CSV file into summary.md with its row count, a total for every numeric column and a table of negative values by row. Use when the user asks to summarize, total or sanity-check a CSV file.
license: Apache-2.0
metadata:
  version: "1.2.0"
  status: stable
  pack: example
  min-tier: low
  roles: builder, researcher
  gate: none
---
```

| Key | Rule |
|---|---|
| `name` | Required. 1 to 64 characters of `a-z`, `0-9` and single hyphens, with no leading or trailing hyphen. Matches the skill folder name. Contains neither `claude` nor `anthropic`. |
| `description` | Required. 1 to 1024 characters, no `<` or `>`. Says what the skill does and when to use it: it is the trigger. Put the key use case first. |
| `license` | Optional. A non-empty string, such as an SPDX identifier. |
| `compatibility` | Optional. 1 to 500 characters of environment requirements. |
| `allowed-tools` | Optional. A space-separated string (the portable form), never a list. |
| `metadata` | Required here. An indented block mapping (not flow style `{...}`) whose values are all strings. |

Required `metadata` keys:

| Key | Values |
|---|---|
| `version` | Semver, such as `1.0.0`. Quote it when YAML could read it as a number. |
| `status` | `stable`, `experimental` or `retired`. |
| `pack` | The pack folder name, such as `example` for `packs/example/`. |
| `min-tier` | `low`, `mid` or `high`: the lowest model capability tier the skill is written for. Never a model name. |
| `roles` | Comma-separated functional role ids, such as `builder, orchestrator`. Never persona names. |
| `gate` | `none`, or `mandatory` for a skill that must fire on every matching situation. |
| `replaces` | Retired skills only: the name of the skill that supersedes this one. |

`metadata` values must be strings to every YAML reader. A value that YAML 1.2 or YAML 1.1 (PyYAML, used by some packaging tools) would read as a number, boolean, null or date fails lint: write `"1.0"`, not `1.0`, and `"yes"`, not `yes`.

## The YAML subset

YAML isn't in the Python standard library, so the tools read every frontmatter block and `case.yaml` with a strict subset parser (`skillslib/yamlsubset.py`). Anything outside the subset is a lint error with a line number, never a guess.

Supported:

- `key: value` lines starting in column 1. Keys use letters, digits, `_`, `.` and `-`. A space (or the end of the line) follows the colon.
- Scalars on one line: plain, `'single-quoted'` (`''` is a literal quote) and `"double-quoted"` with YAML escapes.
- One-line flow collections of scalars: `[a, "b", 3]` and `{source: file, path: out.md}`.
- Block lists of scalars (`- item`) under a top-level key, indented or not.
- One level of nested block mapping under a top-level key, whose values are scalars or one-line flow collections.
- Blank lines, full-line comments and trailing ` # comments`.

Rejected: anchors, aliases and tags; block scalars (`|`, `>`); multi-line scalars and flow collections; nesting deeper than one level; lists of mappings; nested flow collections; duplicate keys; quoted or complex keys; tab indentation; document markers and directives inside the block. A plain value can't contain `: ` (quote it), which is the most common way a hand-written description breaks YAML.

`SKILL.md` also has to start with `---` at byte 0 (no byte-order mark), use LF line endings, and keep `---` out of the frontmatter body, because the reference validator splits the file on it.

## Status and the stable rule

| Status | Meaning |
|---|---|
| `experimental` | Loads, but is unproven. Offered, never auto-fired. |
| `stable` | Passed a qualifying eval run, and its eval cases and redacted results ship with it. |
| `retired` | A redirect stub that names its successor in `metadata.replaces`. |

**The stable rule.** A skill may be `stable` only when its newest complete two-arm run (`--ablation with-without`, not partial) covers at least 3 of its eval cases and shows:

- a with-skill score of at least **0.80**, the mean of its cases' scores;
- a Δ of at least **+0.20**, the mean of its cases' with-skill minus no-skill scores, with a Δ for every case;
- at least **3 runs** per case in each arm;
- on the **pinned model**: the run passed `--model`, which the summary records as `suite.modelPinned: true`. The evals workflow always passes `--model` and `--judge-model` from repository variables.

Lint enforces all of it: SKL014 (at least 3 eval cases), SKL015 (the thresholds) and SKL020 (the pinned model). A skill needs at least 3 eval cases to be stable at all. When a newer qualifying run misses the rule, lint fails until the skill is reworked or demoted. A Δ at or below zero means the skill doesn't beat no skill: rework it or retire it. That result is worth publishing.

## Eval cases

Cases use the `claude plugin eval` format exactly, grouped by skill: `evals/<skill>/<case>/`. The runner finds every case below `evals/`. The grouping directory is how a result is attributed to a skill.

**Names.** A case name is its directory name: it starts with a letter or digit, uses only letters, digits, `.`, `_` and `-`, and is unique within the plugin. Prefix it with the skill or its subject, such as `csv-sales-totals`.

**`prompt.md`** holds a frontmatter block, then the prompt a user would type. The prompt never names the skill.

```markdown
---
tags: [csv-summary]
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

Summarize sales.csv for me and save the summary as summary.md.
```

- Allowed keys: `schema_version`, `name`, `description`, `tags`, `plugins`, `runs`, `expected_outcome`, `max_turns` (1 to 200), `timeout_seconds` (1 to 3600), `allowed_tools`, `disallowed_tools`, `append_system_prompt` and `env` (`EVAL_*` names only). The runner rejects unknown keys.
- `tags` include the skill name, so `--tag <skill>` selects a skill's cases.
- A case never sets `model`. Models come from CI variables only, so no model identifier is ever committed.

**`case.yaml`** names the scaffold script and the expected outcome:

```yaml
schema_version: "1.1"
name: csv-sales-totals
expected_outcome: "summary.md reports 8 rows and 4 columns, ..."
context:
  scaffold_script: place-files.sh
```

`schema_version` is the quoted string `"1.1"`, `name` matches the directory, and every file `context` names exists inside the case directory. Write graders as files, not as a `graders:` list.

**`place-files.sh`** copies the case's `files/` into the empty run workspace. The runner executes it only under `--scaffold`, as `bash <case dir>/place-files.sh`, with the workspace as the working directory.

**Graders** live in `graders/NN-name.md`, one per file, with `type` and options in frontmatter (`type`, `name`, `weight`, `arm`, plus the type's options). Prefer free graders and keep one judge:

| Grader | Use |
|---|---|
| `tool_used` with `tool: Skill` and `arm: with-only` | Indicator that the skill fired. Two-arm runs report it without scoring it. Every case has one. |
| `file_exists` | The output file was created. It only sees files created during the run, not ones the scaffold placed. |
| `regex` with `target: {source: file, path: <file>}` | Exact checks over the produced file. JavaScript syntax; put flags such as `m` or `i` in `flags`, never inline `(?i)`. `match` is `contains`, `not_contains` or `count:N`. |
| `llm` with `focus: {source: file, path: <file>}` | One judge per case, with concrete `PASS if ...` and `FAIL if ...` lines as the body. |

## Running evals

Locally, from the repository root:

```bash
claude plugin eval packs/<pack> --model <model> --ablation with-without --runs 3 --threshold 0.8 \
  --scaffold --trust-plugin --no-publish --output-dir /tmp/<pack>-eval --allow-tools Write Edit
python3 tools/repo/skills/skills.py redact-results /tmp/<pack>-eval/aggregate-result.json \
  --out packs/<pack>/evals/results/<YYYY-MM-DD>/summary.json
python3 tools/repo/skills/skills.py registry
python3 tools/repo/site/build.py
```

Pass the model on the command line only; it never goes in a file. Keep `--output-dir` outside the repository so the raw output can't be committed. A second run on the same day goes in `<YYYY-MM-DD>-2/`, a third in `<YYYY-MM-DD>-3/`, and so on; readers order runs by the summary's own `startedAt`, not by folder name.

After committing a summary, regenerate both views of it: `skills.py registry` rewrites `REGISTRY.md`, and `tools/repo/site/build.py`, which the docs site lane owns, rewrites the eval dashboard and the README's proof block. CI runs both with `--check` and fails when either is stale.

Grant only the tools the cases need. Granting `Bash` requires a sandbox backend on the machine (bubblewrap and socat on Linux); without one, Claude Code refuses every run.

In CI, `.github/workflows/evals.yml` runs:

- **On pull requests that touch `packs/**` or `labs/**`:** only the changed plugins, one arm (`--ablation none`), `--threshold 0.8`, cost-capped by `vars.EVAL_MAX_COST_PR`.
- **On `workflow_dispatch` and release tags:** every plugin, two arms (`--ablation with-without`), cost-capped by `vars.EVAL_MAX_COST_FULL`.

Both pass `--model ${{ vars.EVAL_MODEL }}` and `--judge-model ${{ vars.EVAL_JUDGE_MODEL }}`, so the workflow file never names a model, and both upload only the redacted summaries. The job skips with a notice when the `ANTHROPIC_API_KEY` secret is absent, as it is on forks.

## The redacted summary (`summary.json`)

`claude plugin eval` writes `aggregate-result.json`, which holds model identifiers, absolute paths, prompts, transcripts and judge evidence. **Never commit it, or `report.html`.** `skills.py redact-results` turns it into `summary.json`, the only results file that is committed, at `packs/<pack>/evals/results/<YYYY-MM-DD>/summary.json`. Lint rejects a summary with any field outside this format.

The summary is a strict subset of `aggregate-result.json`: the same field names at the same paths, plus the fields marked *added*. A reader of one can read the other. An illustrative example, with made-up values and one run per arm shown:

```json
{
  "summaryFormat": 1,
  "schemaVersion": 1,
  "claudeVersion": "2.1.318",
  "startedAt": "2026-11-05T02:40:00.000Z",
  "date": "2026-11-05",
  "durationSeconds": 212,
  "costUsd": 1.42,
  "partial": false,
  "suite": {
    "ablation": "with-without",
    "threshold": 0.8,
    "modelPinned": true,
    "judgeModelPinned": true,
    "plugins": [{ "name": "example-pack", "version": "0.1.0" }]
  },
  "cases": [
    {
      "name": "csv-sales-totals",
      "skill": "csv-summary",
      "runsPerCase": 3,
      "aggregates": { "score": 1, "passRate": 1, "scoreWithout": 0.4, "passRateWithout": 0, "delta": 0.6 },
      "arms": {
        "with": [{ "score": 1, "passed": true, "errored": false, "skippedPaidGraders": false }],
        "without": [{ "score": 0.4, "passed": false, "errored": false, "skippedPaidGraders": false }]
      }
    }
  ],
  "aggregates": { "casesTotal": 1, "casesPassed": 1, "overallScore": 1, "overallPassRate": 1, "meanDelta": 0.6 }
}
```

| Field | Meaning |
|---|---|
| `summaryFormat` | *Added.* This format's version, `1`. |
| `schemaVersion` | The source document's `schemaVersion`. |
| `claudeVersion` | The Claude Code version that ran the suite. |
| `startedAt` | When the run started, ISO 8601 UTC, or `null`. |
| `date` | *Added.* The run date, `YYYY-MM-DD`, from `startedAt` unless `--date` overrides it. |
| `durationSeconds`, `costUsd` | Wall-clock seconds, and the list-price cost estimate including judge calls. |
| `partial`, `partialReason` | `true` with `cost_ceiling`, `interrupted` or `auth_failed` when the suite didn't finish. Leave partial runs out of trends; they never qualify a skill. |
| `suite.ablation` | `none` (one arm, no Δ) or `with-without` (two arms). |
| `suite.threshold` | The `--threshold` a case had to reach to count as passed. |
| `suite.modelPinned`, `suite.judgeModelPinned` | *Added.* Whether `--model` and `--judge-model` were passed. Booleans only: the model names are dropped. |
| `suite.plugins[]` | `name` and `version` of each plugin under test. |
| `cases[].name` | The case name. |
| `cases[].skill` | *Added.* The skill the case belongs to (its `evals/<skill>/` group), or `null`. |
| `cases[].runsPerCase` | The configured runs per arm. Count `arms.with` for the runs that actually happened. |
| `cases[].aggregates` | `score` and `passRate` (with-skill), `scoreWithout` and `passRateWithout` (no-skill baseline), and `delta`, the with-skill score minus the baseline score. The baseline fields and `delta` are absent for one-arm runs. |
| `cases[].arms.with[]`, `.without[]` | One entry per run: `score`, `passed`, `errored` (*added*: the run ended abnormally; the error text is dropped) and `skippedPaidGraders` (the cost ceiling skipped its judge graders, so its score isn't comparable). |
| `aggregates` | `casesTotal`, `casesPassed` (at or above the threshold), `overallScore`, `overallPassRate` and `meanDelta` (two-arm runs only). |

Dropped: model identifiers (`suite.modelOverride`, `suite.judgeModel`, `cases[].model`), paths (`suite.root`, `plugins[].path`, `cases[].dir`, `cases[].source`, `tracePath`), prompts, grader definitions, error text, judge votes, explanations, evidence, mock data, turn counts and per-run costs. As a last check the redactor collects every string the source holds under a key that names a model, and refuses to write if any of them appears in the output.

Readers: `skills.py registry` (latest score, Δ, runs and date per skill), the stable rule in `skills.py lint`, and the eval dashboard that `tools/repo/site/build.py` generates. A reader should ignore fields it doesn't know, so later format versions can add fields.

**Stability.** Every field in the table above is stable for `summaryFormat: 1`. Renaming or removing one, or changing its meaning or type, requires a new `summaryFormat`; adding a field doesn't. The dashboard depends in particular on `date`, `cases[].skill`, `cases[].arms.with` (and `cases[].runsPerCase` as its fallback), `suite.modelPinned` and `suite.threshold`, so those must keep their names, paths and meaning.

## The registry

`REGISTRY.md` at the repository root is generated, never edited by hand. Each row is one skill: Skill, Pack, Status, Min tier, Roles, Gate, then Latest score, Δ, Runs and Date from the newest summary that covers the skill (complete runs win over partial ones; partial and one-arm results are marked). Score and Δ are means over the skill's cases in that summary, and Runs is the smallest per-case run count in the with-skill arm. `skills.py registry --check` exits 1 when the committed file differs from what the tree generates.

## Commands

All commands are standard-library Python 3.9+, run from the repository root, and make no network or model calls.

| Command | Does | Exit |
|---|---|---|
| `skills.py lint [roots...] [--strict] [--list-rules]` | Checks every plugin, skill, eval case and summary under the roots (default `packs labs`). | 0 ok, 1 errors (or warnings with `--strict`), 2 usage |
| `skills.py registry [--check] [--root DIR] [--out FILE]` | Writes `REGISTRY.md`, or checks that it is current. | 0 ok, 1 stale or unbuildable |
| `skills.py new <pack> <skill> [--labs] [--roles R] [--min-tier T]` | Scaffolds `skills/<skill>/SKILL.md` and one eval case, `evals/<skill>/<skill>-basic/`, with `TODO` placeholders that lint flags until they are replaced. | 0 created, 1 refused |
| `skills.py redact-results <aggregate-result.json> --out <summary.json> [--date D] [--plugin-dir DIR]` | Writes the redacted summary and prints a Markdown table of it. | 0 written, 1 refused |
| `skills.py plugins [--changed-files FILE]` | Lists plugin directories under `packs/` and `labs/`, or only those containing a path listed in FILE (`-` reads stdin). CI uses it to evaluate only changed plugins. | 0 |

## Lint rules

Errors fail `lint`. Warnings fail it only under `--strict`. `skills.py lint --list-rules` prints this table.

| Code | Severity | Rule |
|---|---|---|
| PLG001 | error | every directory under packs/ and labs/ is a plugin with .claude-plugin/plugin.json |
| PLG002 | error | plugin.json is valid JSON holding an object |
| PLG003 | error | the plugin name is present and kebab-case |
| PLG004 | error | the plugin name contains neither 'claude' nor 'anthropic' |
| PLG005 | error | the plugin has a README.md |
| PLG006 | error | plugin.json has a semver version |
| PLG007 | error | plugin.json has a description |
| PLG008 | error | plugin.json has an author with a name |
| PLG009 | error | no top-level bin/ directory (claude.ai and Cowork refuse to install it) |
| PLG010 | error | no CLAUDE.md at the plugin root (plugins never load it; use a skill) |
| PLG011 | error | manifest component paths start with ./ and stay inside the plugin |
| PLG012 | error | no local stdio MCP servers (they block claude.ai and Cowork installs) |
| PLG013 | warning | settings.json holds only agent and subagentStatusLine |
| PLG014 | error | every SKILL.md sits in a skill folder the plugin loads |
| SKL001 | error | SKILL.md opens with '---' at byte 0, uses LF line endings and closes its frontmatter |
| SKL002 | error | the frontmatter stays inside the supported YAML subset |
| SKL003 | error | only the six portable top-level keys are present |
| SKL004 | error | name is lowercase letters, digits and single hyphens, at most 64 characters |
| SKL005 | error | name matches the skill folder name |
| SKL006 | error | name contains neither 'claude' nor 'anthropic' |
| SKL007 | error | description is a non-empty string of at most 1024 characters without < or > |
| SKL008 | error | compatibility, when present, is a string of at most 500 characters |
| SKL009 | error | license and allowed-tools, when present, are strings |
| SKL010 | error | metadata is a block mapping whose values are all strings |
| SKL011 | error | metadata has version, status, pack, min-tier, roles and gate |
| SKL012 | error | metadata values are valid (semver, enums, role ids, pack matches the folder) |
| SKL013 | error | retired skills, and only retired skills, carry metadata.replaces |
| SKL014 | error | stable skills have at least 3 eval cases |
| SKL015 | error | stable skills meet the stable rule in their newest complete two-arm run |
| SKL016 | warning | no scaffold placeholder (TODO) left in the description |
| SKL017 | warning | SKILL.md stays under 500 lines |
| SKL018 | warning | metadata.replaces names a skill that exists |
| SKL019 | error | skill names are unique across all packs and labs |
| SKL020 | error | stable skills qualify on a run that pinned the model under test (suite.modelPinned) |
| EVL001 | error | eval cases live at evals/<skill>/<case>/ for a skill in the same plugin |
| EVL002 | error | case directory names are valid claude plugin eval case names |
| EVL003 | error | case names are unique within a plugin |
| EVL004 | error | every case has a non-empty prompt |
| EVL005 | error | prompt.md has valid frontmatter with known keys and typed values |
| EVL006 | error | no case pins a model (models come from CI variables only) |
| EVL007 | error | the case's tags include its skill name |
| EVL008 | error | case.yaml is valid: schema_version "1.1", name, known keys |
| EVL009 | error | files that case.yaml names exist inside the case directory |
| EVL010 | error | every case has at least one grader in graders/ |
| EVL011 | error | graders have a known type and valid options |
| EVL012 | warning | llm graders state concrete PASS and FAIL conditions |
| EVL013 | warning | every case has a tool_used grader for the Skill tool |
| EVL014 | warning | no scaffold placeholder (TODO) left in a case |
| RES001 | error | committed summary.json files follow the redacted summary format |
| RES002 | warning | no raw eval output (aggregate-result.json, report.html, mock-recordings/) |
| RES003 | warning | summaries name this plugin and its skills |
| RES004 | warning | every case in a summary is a current eval case (no drift after a rename) |
