---
title: Qualify a skill
nav_order: 5
description: "The stable rule (a score of at least 0.80 and a Δ of at least +0.20 over 3 or more runs) and how to measure it with claude plugin eval against a no-skill baseline."
---

# Qualify a skill

A skill becomes stable by beating no skill, measured and published. This page gives the rule, then walks through measuring a skill yourself: writing eval cases, running them with and without the skill, and publishing the result.

## The stable rule

| Status | Rule |
|---|---|
| **stable** | A with-skill score of at least **0.80** and a Δ of at least **+0.20**, over at least **3 runs** per arm on the pinned model |
| **experimental** | Loads, but has no qualifying run yet |
| **rework or retire** | Δ at or below 0: the skill does no better than no skill |

- **With-skill score:** the mean case score with the skill's plugin loaded, from 0 to 1.
- **Δ (delta):** the with-skill score minus the score of the same cases run with no plugin loaded. Δ is what the skill contributed.
- **Runs:** each case runs 3 times in each arm by default, because a single run of a non-deterministic agent tells you little.

The numbers must come from one complete run, not one stopped early, that ran every case with and without the skill and covered at least 3 of the skill's eval cases. Pinned means the run named its models with `--model` and `--judge-model`, so a later run can be compared like for like.

A high score alone proves nothing. If a case scores 1.0 both with and without the skill, the skill isn't what made it pass, which is why the rule needs both numbers.

## How a score is computed

1. A run's score is the fraction of its graders that passed, weighted if you set weights.
2. A case's score is the mean of its runs.
3. A suite's score is the mean of its cases. Δ is computed per case and then averaged.

## Write the eval cases

Each pack keeps its cases in an `evals/` folder, grouped by skill: one folder per case under `evals/<skill>/`. `claude plugin eval init` can draft them for you: it asks what a good result looks like, proposes prompts and graders, tries them once and writes the files. The layout it writes looks like this:

```text
packs/<pack>/
├── .claude-plugin/plugin.json
├── skills/<skill>/SKILL.md
└── evals/
    └── <skill>/<case>/
        ├── prompt.md            run limits and tools in frontmatter; the prompt below
        ├── case.yaml            case name, expected outcome, optional setup script
        ├── place-files.sh       optional: creates the input files
        └── graders/
            ├── 01-summary-exists.md
            ├── 02-negative-flagged.md
            └── 03-skill-fired.md
```

**`prompt.md`** sets the run limits and the tools the case may use, then gives the prompt. Phrase it the way a person would type it, without naming the skill, so the case also tests whether the skill gets picked:

```markdown
---
tags: [csv]
max_turns: 10
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write]
---

Summarize sales.csv for me: how many rows it has, the total of each numeric column, and any negative values.
```

**`case.yaml`** names the case and points at an optional setup script, which runs in the empty workspace before the agent starts:

```yaml
schema_version: "1.1"
name: summarize-sales
expected_outcome: summary.md gives the row count, the column totals and every negative value.
context:
  scaffold_script: place-files.sh
```

```bash
#!/usr/bin/env bash
# place-files.sh: write the case's input file into the workspace.
set -euo pipefail
cat > sales.csv <<'CSV'
region,units,revenue
north,12,340.50
south,-3,-75.00
CSV
```

**`graders/`** holds one check per file. Each grader's frontmatter sets its `type`, plus an optional `weight` and `arm`. These three cost nothing to run:

```markdown
---
type: file_exists
path: summary.md
---
```

```markdown
---
type: regex
target: { source: file, path: summary.md }
pattern: "south.*-3"
---
```

```markdown
---
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:[\w-]+:)?csv-summary"'
---
```

An `llm` grader asks a judge model for a verdict, so give it concrete PASS and FAIL conditions in the file body:

```markdown
---
type: llm
---

PASS if the reply gives the row count and names the negative value in the south row.
FAIL if the row count is missing or wrong, or if the negative value isn't mentioned.
```

### Choosing graders

- **Prefer free graders.** `regex`, `file_exists`, `tool_used` and `tool_order` are computed from the transcript and files, so they cost nothing and give the same verdict every time. `llm` and `baseline` graders call a judge model and add cost.
- **Grade long output with `regex`.** A judge's verdict varies more the longer the text it reads, so keep `llm` graders for short output.
- **Check the result and the steps.** Give each case one grader on what was produced and one on how, such as `tool_used`, so you learn whether the answer was right and whether the skill produced it.
- **`tool_used: Skill` is an indicator.** In a run with a baseline it can never pass without the skill, so it's left out of the score in both arms and can't inflate Δ. Mark any other grader that can only pass with the skill `arm: with-only`.
- **Suspect the judge before the skill.** If the skill fires but Δ is negative, a judge may be marking a correct answer wrong because of its format. Tighten the rubric so formatting doesn't decide the verdict.

## Run against the no-skill baseline

From the repository root:

```bash
claude plugin eval packs/<pack> \
  --ablation with-without \
  --runs 3 \
  --threshold 0.8 \
  --allow-tools Write \
  --scaffold \
  --max-cost-usd 5 \
  --output-dir ../eval-results/<pack>
```

| Option | Why |
|---|---|
| `--ablation with-without` | Runs every case again with no plugin loaded. That baseline arm is what produces Δ |
| `--runs 3` | Three runs per case in each arm, the minimum the stable rule accepts |
| `--threshold 0.8` | A case passes at a score of 0.8, so the exit code matches the score half of the stable bar |
| `--allow-tools Write` | Grants a tool beyond the read-only set. Granting `Bash` runs every command in Claude Code's OS-level sandbox, which needs `bubblewrap` and `socat` on Linux |
| `--scaffold` | Runs each case's setup script. Only use it on cases you wrote or reviewed |
| `--max-cost-usd 5` | A ceiling on the run's list-price cost estimate. When it's hit, nothing new starts, and the command exits 2 with partial results |
| `--output-dir` | Writes `aggregate-result.json` and `report.html` outside the repository, because the raw result holds model identifiers and transcripts |

Two more options matter when you compare runs over time or script them:

- **Pin the models** with `--model "$EVAL_MODEL" --judge-model "$EVAL_JUDGE_MODEL"`, so a model change isn't mistaken for a change in the skill. This repository's CI reads both from repository variables, so no model name is written in any file.
- **Write JSON** with `--json results.json`. A run with `--json` can't stop to ask whether to trust the plugin directory, so add `--trust-plugin` for a plugin you'd run on your own machine.

Every agent run and every judge call is a real model call, counted against your plan or your API bill. One case is six agent runs, three per arm, plus three short judge calls per `llm` grader in each run.

## Read the result

When the run finishes, it prints a summary table. With illustrative numbers:

```text
CASE             WITH  W/OUT  Δ      RUNS  COST
summarize-sales  0.92  0.42   +0.50  6     $0.38
```

`WITH` is the with-skill score, `W/OUT` the score with no plugin, and `Δ` the difference. `RUNS` counts both arms, so 6 means 3 per arm. The `report.html` in the output folder shows every run, every grader's verdict and, for `llm` graders, what the judge saw.

| Exit code | Meaning |
|---|---|
| 0 | Every case scored at or above the threshold |
| 1 | A case scored below the threshold, or something failed: a case didn't load, a run couldn't start, or the plugin directory wasn't trusted |
| 2 | Partial run: the cost ceiling was hit, or the credential was rejected |

The exit code never reflects Δ, so the Δ half of the stable bar is read from the result, which is what the dashboard does.

## Publish the result

Raw results are never committed. Commit a redacted summary instead:

1. Redact the raw result with the repository's skills tool:

   ```bash
   python3 tools/repo/skills/skills.py redact-results ../eval-results/<pack>/aggregate-result.json \
     --out packs/<pack>/evals/results/<date>/summary.json
   ```

   The summary keeps only the score fields: the schema version, the suite aggregates (overall score, cases passed, cases in total and mean Δ), each case's name, score and Δ, the cost, the duration, the Claude Code version and the run date.

2. Rebuild the registry and the site:

   ```bash
   python3 tools/repo/skills/skills.py registry
   python3 tools/repo/site/build.py
   ```

3. Commit the summary with the regenerated `REGISTRY.md`, `site/evals/index.html`, `site/llms.txt`, `site/llms-full.txt` and `README.md`. CI runs both tools with `--check` and fails if any of them is out of date.

## In CI

- **Every pull request** that touches `packs/` or `labs/` runs only the changed plugins, with cheap graders, `--ablation none` and a cost ceiling. One arm halves the cost, and the result catches breakage without claiming a Δ.
- **Releases and manual runs** run the full suite with `--ablation with-without`, which is what qualifies a skill.
- Both pin the models from repository variables and pass `--trust-plugin` and `--no-publish`, so a job never waits at a prompt and the report stays with the job.

## Read the dashboard

The [eval dashboard](evals/index.html) shows each skill's with-skill score, its Δ against no skill, its runs and the date of its latest complete run. Regressions, where Δ is at or below 0, are marked in red with a label, partial runs are flagged, and nothing on the page is estimated: every number comes from a committed summary.
