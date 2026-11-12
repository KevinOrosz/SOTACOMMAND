# example-pack

A small Claude Code plugin whose two skills have deterministic, checkable outputs. It exists to exercise the whole skills path: the frontmatter contract, `skills.py lint`, `claude plugin eval` cases with mostly free graders, redacted results and the generated `REGISTRY.md`. Copy its layout when you start a real pack.

| Skill | What it does |
|---|---|
| [`csv-summary`](skills/csv-summary/SKILL.md) | Reads a CSV file and writes `summary.md` with the row count, a total for every numeric column and a table of negative values |
| [`changelog-entry`](skills/changelog-entry/SKILL.md) | Turns a described change into a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) entry under `## [Unreleased]` in `CHANGELOG.md` |

Both skills are `experimental` until a qualifying run on the pinned model meets the stable rule. Their latest scores are in [`REGISTRY.md`](../../REGISTRY.md).

## Install

Add this repository's marketplace, then install the plugin from it:

```bash
claude plugin marketplace add KevinOrosz/SOTACOMMAND
claude plugin install example-pack@sotacommand
```

The skills use only the portable frontmatter keys, so each skill folder also works as a standalone skill in other harnesses that read `SKILL.md`.

## Layout

```text
packs/example/
├── .claude-plugin/plugin.json
├── skills/
│   ├── csv-summary/SKILL.md
│   └── changelog-entry/SKILL.md
└── evals/
    ├── csv-summary/<case>/        prompt.md, case.yaml, place-files.sh, files/, graders/
    ├── changelog-entry/<case>/
    └── results/<date>/summary.json   redacted results only
```

Each case seeds its workspace from `files/` with `place-files.sh` (named in `case.yaml` as `context.scaffold_script`) and grades the result with a `tool_used: Skill` indicator, `file_exists` and `regex` graders over the produced file, and one `llm` grader with concrete PASS and FAIL conditions.

## Run the evals

From the repository root, with Claude Code 2.1.269 or later:

```bash
claude plugin eval packs/example --ablation with-without --runs 3 \
  --scaffold --trust-plugin --no-publish \
  --output-dir /tmp/example-eval --allow-tools Write Edit
python3 tools/repo/skills/skills.py redact-results /tmp/example-eval/aggregate-result.json \
  --out packs/example/evals/results/<date>/summary.json
python3 tools/repo/skills/skills.py registry
```

`--output-dir` keeps the raw `aggregate-result.json` and `report.html` outside the repository. Never commit them: they hold model identifiers and transcripts. Commit only the redacted `summary.json`. The skills need only `Read`, `Write` and `Edit`, so no `Bash` grant (and no sandbox backend) is required.
