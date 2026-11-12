# Contributing

Thanks for helping. This repository ships an operating system for a team of AI agents: roles, skills, memory and governance. Each skill comes with eval results that show whether it helps. A few firm rules keep it portable and honest.

Agents working in this repository read `AGENTS.md` first. It holds the same rules, plus the standards each file format follows.

## Ground rules

1. **Standard-library Python 3.9 or later** for every tool, with no third-party packages. Tools make no network calls and no model calls: models plan and judge, tools check.
2. **Official formats only.** Skills use the Agent Skills format, and plugins use the Claude Code plugin layout. When a format question comes up, check the official docs, not memory.
3. **Proof before promotion.** A skill is stable only when its evals show it beats no skill. See [the stable rule](#the-stable-rule).
4. **Nothing private.** Outside `fixtures/`, never write:
   - personal names;
   - email addresses, except at `example.com`, `example.org` or `example.invalid`;
   - absolute home-directory paths;
   - keys or tokens;
   - AI model identifiers.

   Harness names (Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot) are fine. The leak gate checks every pull request for these.
5. **Tests come with changes**, including at least one failure case. Tests live in `tests/<area>/test_*.py`, and every folder that holds tests has an `__init__.py`. Without it, unittest skips the folder silently, and CI fails.
6. **Generated files stay generated.** Don't hand-edit `REGISTRY.md`, adapter outputs or the site's generated pages. Change the source and regenerate. CI fails on drift.

## What you need

- Git and Python 3.9 or later.
- Claude Code, but only to validate plugins or run evals.

There is nothing to install.

## Run the checks locally

CI runs these on every pull request (`.github/workflows/ci.yml` and `validate.yml`). Run them before you push. Skip a check whose script isn't in your checkout yet; CI skips it too.

**Syntax and unit tests**

```sh
python3 -m compileall -q tools adapters engine tests
python3 -m unittest discover -s tests -t .
```

**Leak gate**

Check every tracked file outside `fixtures/` (the leak gate's own test corpus), then run the benchmark. `bench` must print `recall=N/N` and `false_blocks=0`.

```sh
git ls-files -z -- . ':(exclude)fixtures/' | xargs -0 python3 tools/repo/leakgate/leakgate.py check
python3 tools/repo/leakgate/leakgate.py bench
```

**Skills lint and the generated registry**

```sh
python3 tools/repo/skills/skills.py lint packs labs
python3 tools/repo/skills/skills.py registry --check
```

**Generated outputs match their sources**

```sh
python3 adapters/generate.py --check
python3 tools/repo/site/build.py --check
```

**Plugins and the marketplace** (needs Claude Code)

```sh
for dir in $(python3 .github/scripts/plugins.py list --require-manifest); do
  claude plugin validate --strict "$dir"
done
claude plugin validate --strict .
```

CI also runs gitleaks over the commits in your pull request and actionlint over the workflow files. Both are optional locally.

## Propose a skill

1. **Open a Skill proposal issue.** The form asks for the trigger, what the skill does, the roles that use it, its model tier and an eval plan. Agreeing on those first saves rework.
2. **Scaffold it.** This creates the skill folder and one eval case in the layout `claude plugin eval` reads:

   ```sh
   python3 tools/repo/skills/skills.py new PACK SKILL
   ```

3. **Write `SKILL.md`.**
   - Frontmatter uses only the portable keys: `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`. Any other top-level key breaks uploads to claude.ai and the Skills API.
   - `name` uses lowercase letters, digits and hyphens, is at most 64 characters, and matches the folder name.
   - `description` says what the skill does and when to use it, in at most 1024 characters. It's what agents read to decide whether to load the skill.
   - Custom fields go under `metadata` as flat strings: `version`, `status`, `pack`, `min-tier`, `roles` and `gate`, plus `replaces` for a retired skill. `tools/repo/skills/CONTRACT.md` has the full contract.
   - Name no models. `min-tier` is a capability tier: `low`, `mid` or `high`.
4. **Add at least three eval cases** under the pack's `evals/<skill>/` folder, one folder per case (`packs/<pack>/evals/<skill>/<case>/`). Each case has:
   - `prompt.md`
   - `case.yaml`
   - one or more graders in `graders/`

   Prefer cheap graders (`regex`, `file_exists`, `tool_used`). Use an `llm` grader, with concrete pass and fail wording, only where judgment is needed. Inputs are synthetic, never real client or personal data.
5. **Land it as experimental** in a plugin under `labs/`. Promote it to a stable pack under `packs/` once it meets the stable rule.

## The stable rule

A skill is `stable` when a recorded `claude plugin eval` run on the pinned eval model shows both:

- a with-skill score of at least **0.8**, on a scale from 0 to 1;
- a gain (Δ) of at least **+0.2** over the same cases run without the skill.

That takes at least **3 runs per arm**, which is the default.

The other statuses:

- **`experimental`:** the skill loads but is unproven.
- **Δ at or below 0:** the skill doesn't beat no skill. Rework it, or retire it (`status: retired`, with `metadata.replaces`). A skill that loses to no skill is still useful data.

To run the evals and record the result:

```sh
claude plugin eval packs/PACK --ablation with-without --json "${TMPDIR:-/tmp}/aggregate-result.json"
python3 tools/repo/skills/skills.py redact-results "${TMPDIR:-/tmp}/aggregate-result.json" --out packs/PACK/evals/results/DATE/summary.json
python3 tools/repo/skills/skills.py registry
```

The eval model is pinned in the repository variable `EVAL_MODEL`. Pass it with `--model`.

Commit only the redacted `summary.json`, never raw results: those name models and hold transcripts. `.gitignore` keeps everything else under `evals/results/` out of commits.

## Pull requests

- Make one change per pull request, from its own branch to `main`.
- Fill in the template: summary, verification (commands and output), leak gate output, and the eval Δ if skills changed.
- Add an entry under "Unreleased" in `CHANGELOG.md` when behavior changes.
- Sign your commits; the branch rules require it. Merges are squash-only.
- The `CI result`, `Leak gate` and `Plugin validate` checks must pass.

## Releases (maintainers)

1. Move the "Unreleased" entries in `CHANGELOG.md` under a `## [X.Y.Z] - YYYY-MM-DD` heading.
2. Tag a commit on `main` as `vX.Y.Z` and push the tag.

`release.yml` then:

- builds one zip per plugin plus `SHA256SUMS`;
- attests every file;
- publishes an immutable release, using that `CHANGELOG.md` section as its notes.

## Conduct and security

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems privately, as [SECURITY.md](SECURITY.md) describes, never in a public issue.
