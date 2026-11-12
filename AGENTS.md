# AGENTS.md: read this first if you are an AI agent

SOTACOMMAND (SOTA = state of the art) is an operating system for a team of AI agents. It works with any model and any agent harness.

## If the user says `WIZARD`

Read `WIZARD.md` and follow it exactly. WIZARD is the setup agent. It interviews the user in plain words (who they are, their brand, business or art, which helpers they want), builds their agent team in their own folder, hands them their team card and walks a first job with them. Assume they have never used an AI agent. This takes priority over everything below.

## If the user says hello, seems new, or doesn't say what they want

Say, in one or two lines, what this is (a team of AI helpers that gets set up by interview) and that typing `WIZARD` starts it. Don't explain the repository's internals unless they ask.

## If the user wants to use SOTACOMMAND

Read `START-HERE.md`. It has the install steps for every harness, written for people and for agents. Don't change files in this repository unless they ask you to.

## If you are building or changing SOTACOMMAND

Everything below applies. Open gaps are in `ROADMAP.md`, and `CONTRIBUTING.md` says how to propose a change. If your copy has an `internal/` folder, it holds the maintainers' plans and mission briefs; it never ships, and public copies don't have it.

## Rules for every session

1. **One mission per session.** Touch only the paths your mission owns (lane map below). If you need a change somewhere else, write it in your PR description under "Requests for other lanes" and don't make it yourself.
2. **One branch, one draft PR.** Work on the branch named in your brief and open one draft PR to `main`. Never merge.
3. **Standard-library Python 3.9+ only** for tools, unless your brief says otherwise. Tools make no network calls and no model calls. Models plan and judge; tools check.
4. **Verify before you say done.** Run your tests and every check in your brief, then paste the exact commands and their output into the PR description. A report is a claim; files and CI are the proof.
5. **Keep private material out of shipped paths.** Outside `fixtures/` (and a maintainer-only `internal/` folder, if your copy has one), never write model identifiers, vendor model names, personal names, email addresses, or absolute home-directory paths. Harness names (Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot) are allowed wherever they're needed.
6. **Stop on consequential blockers.** Write the blocker up in the PR instead of working around it.
7. **Check formats against the official docs, not memory.** code.claude.com is reachable from cloud sessions; agentskills.io and agents.md may be blocked.

## Standards (checked 2026-11-04 against official docs)

**Skills (Agent Skills format)**
- A skill is a folder holding a `SKILL.md`. Portable frontmatter keys are `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`. Any other top-level key is a hard error on claude.ai uploads and the Skills API.
- Custom fields go under `metadata` as flat string values.
- `name` uses lowercase letters, digits and hyphens, is at most 64 characters, and matches the folder name.
- `description` is at most 1024 characters.

**Claude Code plugins**
- The manifest lives at `<plugin>/.claude-plugin/plugin.json`.
- Components:
  - skills: `<plugin>/skills/<name>/SKILL.md`
  - agents: `<plugin>/agents/*.md`
  - hooks: `<plugin>/hooks/hooks.json`
  - MCP servers: `<plugin>/.mcp.json`
- A plugin `settings.json` honors only `agent` and `subagentStatusLine`.
- A `CLAUDE.md` inside a plugin is never loaded, so put instructions in a skill.
- No top-level `bin/` in packs, because it blocks claude.ai and Cowork installs.
- Manifest paths start with `./`. Reference plugin files as `${CLAUDE_PLUGIN_ROOT}/...`.
- Plugin agent frontmatter supports `name, description, model, effort, maxTurns, tools, disallowedTools, skills, memory, background, isolation` (worktree only). It ignores `hooks`, `mcpServers` and `permissionMode`. Use `model: inherit` and never a model name.

**Marketplace**
- `.claude-plugin/marketplace.json` sits at the repo root. Required fields are `name`, `owner` and `plugins`.
- Relative plugin sources start with `./`.
- Plugin and marketplace names must not start with `claude-` or `anthropic-` or contain those words. Avoid reserved marketplace names such as `agent-skills`.

**Validation:** `claude plugin validate --strict <dir>` must pass for every plugin directory and for the marketplace root.

**Evals (`claude plugin eval` case format)**
- `evals/<case>/prompt.md`: a frontmatter block with `tags, max_turns, timeout_seconds, allowed_tools`, then the prompt.
- `case.yaml`: `schema_version: "1.1"`, `name`, `expected_outcome`, and optionally `context.scaffold_script`.
- `graders/NN-name.md`: frontmatter with `type` (`llm`, `regex`, `file_exists`, `tool_used`, `tool_order` or `baseline`), plus `weight`, `focus`/`target` and `arm`.
- Each case runs 3 times per arm by default. `--ablation with-without` reports Δ against a no-plugin baseline.
- Results go to `evals/results/<timestamp>/aggregate-result.json`.
- Never commit raw results that contain model identifiers; commit redacted summaries.

**Instructions files:** `AGENTS.md` is canonical. Claude Code 2.1.277 and later reads it natively when no `CLAUDE.md` exists. Codex reads `AGENTS.md` and `.agents/skills/`.

**GitHub Actions**
- Pin every action to a full commit SHA with a version comment. Resolve SHAs with `git ls-remote`; never invent one.
- Set top-level `permissions: contents: read`.
- Use concurrency groups with cancel-in-progress.
- Set job timeouts.
- Use `persist-credentials: false` on checkout.

## Lane map

| Mission | Owns |
|---|---|
| M1 Governance and CI | `.github/` except `.github/workflows/evals.yml` and `.github/agents/`; `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.editorconfig`, `.gitattributes`, `.gitignore` |
| M2 Leak gate | `tools/repo/leakgate/`, `tests/leakgate/`, `fixtures/leak-corpus/`, `.gitleaks.toml`, `.pre-commit-config.yaml` |
| M3 Extraction tool | `tools/repo/extract/`, `tests/extract/`, `fixtures/fake-command/`, `export/` |
| M4 Skills toolchain | `tools/repo/skills/`, `tests/skills/`, `packs/`, `labs/`, `REGISTRY.md`, `.github/workflows/evals.yml` |
| M5 Engine and adapters | `engine/`, `adapters/`, `tests/engine/`, `fixtures/roles-sample/`, `.claude-plugin/`, `.github/agents/` |
| M6 Docs site and dashboard | `site/`, `tools/repo/site/`, `tests/site/`, `README.md` |
| Orchestrator | `AGENTS.md`, `tests/__init__.py`, `WIZARD.md`, `START-HERE.md`, `ROADMAP.md`, `LICENSE`, `NOTICE`, `tools/wizard/`, `tests/wizard/`, `integrations/`, `CLAUDE.md`, `GEMINI.md` |

## Command contracts (so lanes can integrate without waiting on each other)

| Command | Owner | Contract |
|---|---|---|
| `python3 tools/repo/leakgate/leakgate.py check <paths...> [--json]` | M2 | exit 0 clean, 1 findings, 2 usage or config error |
| `python3 tools/repo/leakgate/leakgate.py bench` | M2 | prints recall on the planted corpus and false blocks on the clean corpus |
| `python3 tools/repo/extract/extract.py --source D --manifest F --out D [--dry-run]` | M3 | exit 0 written clean, 1 refused (leak findings), 3 leak gate unavailable |
| `python3 tools/repo/skills/skills.py lint <roots...>` and `registry --check` | M4 | exit 0 ok, 1 problems |
| `python3 adapters/generate.py --check` | M5 | exit 0 committed outputs match the generators, 1 drift |
| `python3 tools/repo/site/build.py --check` | M6 | exit 0 committed site outputs match, 1 drift |
| `python3 tools/wizard/wizard.py doctor\|install\|interview\|questions\|check\|update\|uninstall` | Orchestrator | exit 0 ok, 1 a check failed or something was skipped or kept, 2 usage error |
| `python3 -m unittest discover -s tests -t .` | all | every lane's tests live in `tests/<lane>/test_*.py`, with an `__init__.py` |

CI (M1) calls each command only when its script exists, so every lane's PR can go green before the others land.
