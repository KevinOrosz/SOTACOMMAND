# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-11-11

The first public release. Type `WIZARD` in any AI tool opened on this repository.

### Known gaps

See [ROADMAP.md](ROADMAP.md). Five remain, and each needs the owner: GAP-6 (a live Claude Code smoke test), GAP-8 (the real extraction), GAP-9 (15 stable skills, and the first committed eval results), GAP-11 (launch decisions) and GAP-15 (signed commits).

### Fixed: findings from the final pre-launch review

- CI pinned gitleaks 8.24.3, but `.gitleaks.toml` needs 8.25 or later for its `[[allowlists]]`, so the leak gate's planted corpus was flagged on the first full scan. CI now runs 8.30.1, and a test keeps the pin at or above the config's `minVersion`.

- The leak gate's test corpus stores its four provider-shaped fake tokens sealed (`s{{seal}}k_test_...`), and the bench unseals them in a temporary copy before scanning. GitHub's secret push protection blocked the first public push over them, although each is an obvious placeholder. Recall is unchanged at 184/184.

- The shell guard now catches writing into a protected folder (`cp x profile/`, `cp -t profile x`, `mv x profile`), wildcards that reach a protected file (`rm -rf *`, `rm -f *.json`), commands that revert the whole tree (`git checkout -- .`, `git restore .`, `git reset --hard`, `git stash`), `curl -o`, `wget -O`, `find -delete` and `-exec`, `tar x`, `unzip`, `patch`, `git apply`, and options that hid the program (`command -p`, `sudo -u`, `env -u`, `timeout`, `git -C`).
- Re-running `install` no longer replaces a skill folder the owner edited. The skills installer records a hash of every file it installs and replaces a folder only while it is unchanged.
- `update` never replaces an earlier `os.config.json.bak` or an owner's own `<name>.new`, and no longer treats a file in another encoding as missing.
- `uninstall` keeps the skills list when it keeps an edited skill, so a later install still recognizes the folder.
- Public-facing files: issue contact links, Scorecard on a public repository, a concrete private reporting link, and no references to maintainer-only files.

### Added: Hermes Agent harness

- `wizard.py install --harness hermes`. Hermes Agent reads `AGENTS.md` and, once the project is trusted, the skills in `.agents/skills/`, so this is the neutral install plus the `hermes skills trust` command, a `git init` hint, and a warning when `.hermes.md`, `HERMES.md` or `AGENTS.override.md` would hide `AGENTS.md`. The interview offers Hermes Agent as a tool, and the team card gives its steps. Built from Hermes's documentation; not yet run on a real Hermes install.

### Added: wizard update

- `wizard.py update --target DIR [--dry-run]`. Replaces files the wizard wrote that the owner hasn't changed, keeps edited ones and writes the new version beside each as `<name>.new`, rebuilds `TEAM.md` from the saved answers, installs new pack skills, and adds missing settings and protected paths to `os.config.json` after a backup. It never changes an existing setting or touches `profile/` or `memory/`. Before this, new settings such as `shell_guard` and `ledger` only reached new installs.
- The install record carries a version. `wizard.py check` and the session-start report say when a project is behind.

### Added: recall

- `engine/tools/recall.py`. Session start keeps a copy of `memory/HANDOFF.md` whenever it changed, in `.sotacommand/history/` (newest 200 by default), so earlier sessions are no longer lost when the note is rewritten. `recall.py "<words>"` ranks passages from the history, `memory/`, the owner profile and the team card with a full-text index built in memory (SQLite FTS5, with a word-count fallback).

### Added: the learning loop

- `engine/tools/skill_loop.py`. The owner-auditor proposes a skill in `memory/skill-proposals/` when a problem repeats; on the owner's yes, `draft` writes it as an experimental project skill with the ledger's count of the problem as its baseline; `status` (and session start) measures it; `promote` marks it stable only after `min_sessions` sessions (default 5) without the refusals it targets. The verdict comes from the ledger, never from what the skill's file claims.

### Added: the ledger

- `engine/tools/ledger.py`: the hooks record sessions, the owner's messages (length only, never the text), refusals by each guard, approvals asked, failed closes, broken role registries and over-cap memory files in `.sotacommand/ledger.jsonl`. Local only, rotates at 2 MB, off with `"ledger": {"enabled": false}`.
- Session start refreshes `.sotacommand/ledger-summary.md` and reports seven-day counts. The owner-auditor's program starts from the summary and must label minutes and causes as estimates.
- `.sotacommand/**` joins the template's protected paths, so agents can read the ledger but not edit it.

### Added: the team guard

- `engine/tools/protect_team.py`, a PreToolUse hook on the Agent tool. A helper the owner turned off in the interview gets no work: the call is refused with a reason that tells the Lead not to route around it. The choice lives in `profile/answers.json`, which agents can't edit.
- The session-start report states which helpers are on and off.

### Added: the shell guard

- `engine/tools/protect_shell.py`, a PreToolUse hook on Bash and PowerShell. It works out what a shell command would write (redirections, `rm`, `mv`, `cp`, `tee`, `sed -i`, `git checkout` and the like, including inside `sh -c` and `$(...)`) and applies the protected paths, the role write scopes and the second-brain rule. Before this, a role with Bash could change `os.config.json` with one `echo`.
- `shell_guard` in `os.config.json`: `deny` patterns block a command; `ask` patterns hand it to the owner for a yes. The template denies `sudo` and asks before `git push`, package publishes, merges, releases and piping a download into a shell.
- It is a guard, not a sandbox, and the docs say exactly what it can't catch.

### Fixed: a desk walk-through of WIZARD as a newcomer

- The script no longer asks for a "path": WIZARD offers a new folder named after the project and creates it with `install --create`.
- The interview asks which AI app the owner uses, and `TEAM.md` opens with the exact steps to open their team in that app and folder.
- WIZARD says plainly that it is the setup guide, not the team, and no longer offers to run the team's work from the SOTACOMMAND folder.
- The in-project `wizard` skill told the Lead to run a command it has no permission to run. It now reads the session-start report instead.
- The `os-boot` skill handles the word `close`, which the team card tells owners to say.
- With the Builder on and no git project, `interview` and `check` say so and give the three commands.
- `interview` removes the agent's temporary answers file, so `uninstall` leaves a clean folder.
- Found by running the whole script in a sandbox and firing the engine's hooks: the team card wrote `cd "~/folder"`, where the quotes stop `~` from expanding (now `cd ~/folder`), and `install --create --dry-run` listed no files for a new folder (it now lists them).

### Added: launch assets

- A demo GIF at the top of the README, replayed from a real run of `wizard.py interview`, and a social preview card (`site/assets/img/`).

### Added: the interview

- **WIZARD now interviews the owner.** About ten plain-word questions (name, project, what it is and who it's for, goals, voice, what never to do, what needs approval, which helpers, long-term memory), from one source, `tools/wizard/questions.json`. `wizard.py interview` asks them on the terminal or takes an agent's `--answers` file; `wizard.py questions` prints them for agents. It writes `profile/owner.md`, `profile/answers.json` and `TEAM.md`, the owner's team card with the exact words to say to each helper, and never replaces them without `--redo`.
- Boot files read the profile and team card: the `AGENTS.md` template, the `os-boot` and `wizard` skills and the flat prompt. A helper the owner turned off gets no work.
- README and START-HERE open with WIZARD for people new to AI agents, and cross-link the companion project SOTASECONDBRAIN. Root `CLAUDE.md` and `GEMINI.md` import `AGENTS.md`, so `WIZARD` works in those tools too.
- Install commands name the real repository instead of placeholders. The leak gate allows the owner's GitHub handle (and only the handle) for that.
- The public tree is rehearsed: `git archive` without `internal/` passes every test; four leak-gate tests that need `internal/` now skip there.

### Closed gaps

- **GAP-1, GAP-2, GAP-4 (extraction).** The manifest's new `roles` section writes each role's frontmatter into `engine/roles/<id>/soul.md`, and its `plugins` section generates each extracted pack's `plugin.json` and `README.md`. A carried-over `status: stable` becomes `experimental`, and the example manifests target `labs/`. A test runs extraction end to end through the real leak gate, loads the staged roles and lints the staged skills.
- **GAP-3.** New lint rule RES004 flags an eval summary that names a case with no case folder. It caught the committed 2026-11-05 summary, which is removed: the proof table makes no claims until a fresh run is committed.
- **GAP-5.** The marketplace generator reads `SKILL.md` with the skills lint parser, so the two can't disagree about `stable`.
- **GAP-7.** Boot parity: the main role's `Agent(...)` must reach every delegate; `role_policy.json` carries the registry and routes; `session_open.py` reports `BOOT_INVALID` when the installed agents disagree with them, and the `os-boot` skill stops.
- **GAP-10, GAP-13 (wizard).** New harnesses `cursor` (`.cursor/rules/sotacommand.mdc`), `gemini` (`GEMINI.md` importing `AGENTS.md`) and `flat` (`SOTACOMMAND.prompt.md`, one paste-in prompt). `wizard.py uninstall` removes only recorded files that are unchanged since install. CI gained a Windows job.
- **GAP-12.** Three eval cases each for the engine's `os-boot` and `wizard` skills, linted in CI and included in the evals workflow and dashboard. They have not been run yet.
- **GAP-14.** `second_brain` in `os.config.json` connects a SOTASECONDBRAIN knowledge base: a boot line with page and proposal counts, a guard that denies direct writes into its `KB/`, an optional `bin/kb lint` at close, and `integrations/second-brain/README.md`.

### Changed

- Renamed the project from agent-os (briefly SOTABRAIN) to **SOTACOMMAND** (SOTA: state of the art); the GitHub repository is now SOTACOMMAND: plugin `sotacommand-engine`, marketplace `sotacommand`, author and owner `SOTACOMMAND`.
- The role source contract accepts an optional `prepare.md`, appended after `program.md`.
- The example extraction manifests put extracted scripts in `engine/scripts/`; `engine/tools/` belongs to the generator, which would have removed them.
- The docs site no longer says the engine is unbuilt; the quickstart now starts with `WIZARD`.

### Fixed

- CI: the leak gate's README was missing, which errored the unit tests, and the root `HANDOFF.md` failed the leak gate. It now lives in `internal/`.
- `CONTRIBUTING.md` and `site/qualify-a-skill.md` describe the real eval layout, `evals/<skill>/<case>/`.
- `.github/rulesets.md` lists `EVAL_MAX_COST_FULL`.

### Added

- **WIZARD, the one-word boot.** Type `WIZARD` in any agent tool opened on the repository. `WIZARD.md` is the procedure any harness follows; `tools/wizard/wizard.py` (`doctor`, `install`, `check`, `uninstall`) installs the team into a project for Claude Code, Codex, GitHub Copilot or any other tool, never overwriting a file. The engine's `wizard` skill and the Codex `AGENTS.md` template answer `WIZARD` inside an installed project.
- `START-HERE.md`: the full guide for people and for agents, with a TL;DR and an explain-like-I'm-10 version. The README gained both, plus five concrete tasks to try.
- `LICENSE` (Apache-2.0) and `NOTICE`. The leak gate allows the copyright holder's name only in the root `NOTICE`.
- `ROADMAP.md`: known gaps GAP-1 to GAP-15, each with an issue and, where it lives in code, a `GAP-n` comment.
- The engine plugin (`sotacommand-engine`): six role subagents with tool limits, a protected-path hook, a close gate, a session-open report, the convergence workflow and the `os-boot` skill, generated from role sources by `adapters/generate.py`, plus Codex and GitHub Copilot adapters and the root marketplace.
- The leak gate (`tools/repo/leakgate/`) with recall and false-block benchmarks, and its README.
- The one-time extraction tool (`tools/repo/extract/`), proven on a synthetic source tree.
- The skills toolchain (`tools/repo/skills/`): lint, registry, scaffolding and eval-result redaction, and the example pack with six eval cases.
- The docs site, eval dashboard and `llms.txt` (`site/`, `tools/repo/site/`).
- CI on Linux and macOS with Python 3.9 and 3.12. It byte-compiles the tools and runs the unit tests. Each tool's own check also runs once that tool lands: leak gate check and benchmark, skills lint, registry drift, adapter drift and site drift.
- Plugin validation with a pinned Claude Code CLI, running `claude plugin validate --strict` on every plugin directory and on the marketplace.
- Supply-chain checks:
  - gitleaks secret scanning;
  - actionlint over the workflow files;
  - CodeQL for Python, on public repositories;
  - OpenSSF Scorecard, dispatch-only while the repository is private;
  - weekly grouped Dependabot updates for the SHA-pinned actions.
- A release workflow for `v*` tags. It builds a reproducible zip per plugin plus `SHA256SUMS`, attests each file's build provenance, then publishes the GitHub release from a draft so immutable releases lock complete.
- A GitHub Pages workflow that builds `site/` on pull requests and deploys it from a public repository.
- Repository governance:
  - the settings to apply by hand (`.github/rulesets.md`);
  - code owners;
  - issue forms for bugs, skill proposals and harness support;
  - a pull request template.
- Community files:
  - `CONTRIBUTING.md`, with the stable rule for skills;
  - `SECURITY.md`, with private vulnerability reporting;
  - `CODE_OF_CONDUCT.md`, based on Contributor Covenant 2.1.
- `.editorconfig` and `.gitattributes`.
- `.gitignore` rules that keep raw eval results, except the redacted `summary.json`, out of commits.

[Unreleased]: https://github.com/KevinOrosz/SOTACOMMAND/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KevinOrosz/SOTACOMMAND/releases/tag/v0.1.0
