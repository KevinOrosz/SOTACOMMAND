# Role source contract

Roles are written once, in a harness-neutral source format, and `adapters/generate.py` turns them into harness outputs. In the Claude Code plugin, a role's boundaries are enforced by tool limits, a protected-path hook and worktrees. Other harnesses get the same boundaries as instructions. This document is the contract the generator reads. It assumes nothing about a role beyond what is written here.

## Layout

```
roles/
└── <role-id>/
    ├── soul.md        required: frontmatter, then the role's instructions
    ├── program.md     optional
    ├── prepare.md     optional (for example, a researcher's pre-loop checklist)
    ├── protocols.md   optional
    ├── rubric.md      optional
    └── checks.md      optional
```

- The folder name is the role id.
- The optional files are appended to the body of `soul.md` in exactly this order: `program.md`, `prepare.md`, `protocols.md`, `rubric.md`, `checks.md`. A blank line separates each part. Start each optional file with its own heading. Only `soul.md` carries frontmatter.
- Any other file or folder inside a role folder is an error, so content is never dropped silently. Hidden files (names starting with `.`) are ignored. Files next to the role folders, such as `roles/README.md`, are ignored.
- Text is UTF-8. A byte order mark and CRLF line endings are accepted.

The generator reads `engine/roles/` once real roles exist there, and `fixtures/roles-sample/` until then. Pass `--roles DIR` to choose another directory.

## Frontmatter keys

| Key | Required | Value | Rules |
|---|---|---|---|
| `id` | yes | string | Lowercase kebab-case (`[a-z][a-z0-9]*(-[a-z0-9]+)*`), at most 64 characters, equal to the folder name |
| `description` | yes | string | When to delegate to this role. One line, at most 1024 characters. Whitespace runs collapse to one space |
| `tier` | yes | `low`, `mid` or `high` | A capability tier, never a model name. Each install maps tiers to models |
| `allowed-tools` | no | tool list | The only tools the role may use. Omit it, or leave it empty, to inherit every tool. An empty list (`[]`) is an error |
| `disallowed-tools` | no | tool list | Tools removed from the role. A tool may not appear in both lists |
| `isolation` | no | `worktree` or `none` | `worktree` runs the role in a temporary git worktree. Default `none` |
| `main` | no | `true` or `false` | `true` for exactly one role: the one that runs the session. Default `false`. The main role cannot set `isolation` |
| `write-paths` | no | list of path globs | The only project paths the role may write with the file tools. Omit it for no role-specific limit. `[]` means the role may write nothing |

Unknown keys are errors.

### Tool lists

A tool list is a comma-separated string or a YAML list. In a string, commas inside parentheses stay with their entry, so `Agent(builder, researcher), Read` has two entries.

Entries use Claude Code tool names, which other adapters map to their own vocabulary:

- Built-in tools are PascalCase and case-sensitive: `Read`, `Grep`, `Glob`, `Write`, `Edit`, `NotebookEdit`, `Bash`, `PowerShell`, `WebFetch`, `WebSearch`, `Skill`, `Workflow`, `Agent`, and so on. `read` is an error.
- A specifier may follow in parentheses, as in `Bash(git log *)`. In `disallowed-tools`, Claude Code removes the whole tool.
- MCP entries: `mcp__*` (every MCP tool), `mcp__<server>` or `mcp__<server>__*` (one server), `mcp__<server>__<tool>` (one tool).
- `Agent(<role-id>, ...)` in `allowed-tools` limits which roles this role may delegate to. List role ids, not plugin-scoped names. Every id must exist, and a role cannot name itself. At most one `Agent` entry is allowed. When the main role restricts `Agent(...)`, it must name every delegate role, or the router couldn't reach that role. The generator writes the role list and the main role's routes into `engine/tools/role_policy.json`, and `session_open.py` checks the installed agents against both at boot: any drift is `BOOT_INVALID`, and the boot skill stops. In Claude Code the restriction applies when the role runs as the main session; the generator rewrites the ids to plugin-scoped agent types.

### Path globs

`write-paths` entries are relative to the project root, use `/` separators, and never contain `..`. A leading `./` is dropped. `*` and `?` match within one path segment, `**` matches any number of segments, and a pattern also covers everything below a matching directory, so `memory`, `memory/` and `memory/**` mean the same scope. Write scopes match case-sensitively.

## Frontmatter syntax

YAML is not in the Python standard library, so the parser accepts a strict subset of YAML and rejects everything else with an error that names the line. It never guesses.

- The file starts with a line that is exactly `---`; the block ends at the next line that is exactly `---`.
- One `key: value` entry per line, with a lowercase kebab-case key.
- Values: a plain scalar; a double-quoted string with JSON escapes; a single-quoted string (`''` stands for one quote); `true` or `false`; a flow list `[a, "b, c"]`; or nothing, followed by an indented block list of `- item` lines.
- Full-line comments start with `#`. Blank lines are ignored.
- Quote a value that contains `": "` or `" #"`, or that starts with one of ``[ ] { } & * ! | > ' " % @ ` , #``.
- Errors: duplicate keys, tabs, nested maps, anchors and aliases, multi-line scalars, trailing comments, and the ambiguous words `yes`, `no`, `on`, `off` and `null`.

## Example

```markdown
---
id: owner-auditor
description: "Delegate an audit of what the agent system costs its owner. Reads anything; writes only under memory/."
tier: mid
allowed-tools: Read, Grep, Glob, Write, Edit
disallowed-tools: Bash, PowerShell, NotebookEdit
write-paths: [memory/**]
---

# Owner-auditor

You measure how much of the owner's time the agent system uses.
```

## What each adapter generates

| Key | Claude Code plugin (`engine/`) | GitHub Copilot (`.github/agents/`) | Codex (`AGENTS.md`) |
|---|---|---|---|
| `id` | `agents/<id>.md` with `name: <id>`; the agent type is `<plugin>:<id>` | `<id>.agent.md` with `name: <id>` | Row in the roles table |
| `description` | `description` | `description` | Row in the roles table |
| `tier` | A comment only; every agent sets `model: inherit` | Not used | Not used |
| `allowed-tools` | `tools`, with `Agent(...)` ids rewritten to `<plugin>:<id>` | `tools` aliases: `execute`, `read`, `edit`, `search`, `agent`, `web`, `todo` and `server/tool` for MCP | Listed as a boundary |
| `disallowed-tools` | `disallowedTools` | Narrows the aliases of a role that inherits every tool | Listed as a boundary |
| `isolation` | `isolation: worktree` | Stated in the prompt as an unenforced rule | Listed as a boundary |
| `main` | `settings.json` sets `{"agent": "<id>"}` | Not used | Named as the routing role |
| `write-paths` | `tools/role_policy.json`, enforced by the PreToolUse hook `tools/protect_paths.py` | Stated in the prompt as an unenforced rule | Listed as a boundary |
| Body | The agent's system prompt | The agent's prompt (at most 30,000 characters, or the generator fails) | Not copied |

Copilot aliases are coarser than Claude Code tools: `edit` covers Write, Edit and NotebookEdit, and `web` covers WebSearch and WebFetch. A role with an explicit allowlist gets each alias that covers one of its allowed tools. A role that inherits every tool gets each alias none of whose tools it denies. An allowed entry with a specifier, such as `Bash(git *)`, is not granted, because its alias would grant the whole tool. Tools with no Copilot alias, such as `Skill` and `Workflow`, are listed in the prompt as unavailable.

## Enforcement in Claude Code, and its limits

- `tools` and `disallowedTools` decide which tools a role can call at all. This is the hard boundary.
- The PreToolUse hook (`Write|Edit`) denies writes to the owner's `protected_paths` in `os.config.json`, and writes outside a role's `write-paths`. It identifies the role from the hook input's `agent_type`, with the plugin prefix removed. When it cannot evaluate a call, it denies, and the hook is configured with `onFailure: block`.
- The hook guards the file-editing tools only. A role with `Bash` can still write files through the shell, so a role that must stay inside a write scope should not have `Bash`. The sample roles follow this.
- A skill can run in a forked subagent of another agent type, with that type's tools. Roles whose boundary must be airtight leave `Skill` out.
- Workflow agents are not bound by the main role's write scope. Claude Code asks the owner to approve each workflow run, unless the owner has opted out.

## Generating and checking

```sh
python3 adapters/generate.py                         # write engine/, .github/agents/, .claude-plugin/marketplace.json
python3 adapters/generate.py --check                 # exit 1 when committed outputs drift
python3 adapters/generate.py --roles DIR --out DIR   # explicit sources and output root
python3 adapters/generate.py --harness codex --target PROJECT_DIR
```

`--check` regenerates into a temporary directory and compares the result with the committed files. A changed, missing or stale generated file is drift. Exit codes: 0 success; 1 drift, or skills skipped by a Codex install; 2 usage, role-source or configuration error.

The marketplace lists `engine/` plus every `packs/<name>/` and `labs/<name>/` that holds `.claude-plugin/plugin.json`. A plugin directory without a manifest is skipped with a notice, and is picked up on the next run once its manifest exists. Every entry states `defaultEnabled`, so the default install holds proven work only:

- `engine`: always `true`.
- `packs/<name>`: `true` only when the pack has at least one `skills/<skill>/SKILL.md` and every one of them sets `metadata.status: stable`. The generator reads the status with the same strict parser, allowing one level of map for the `metadata` block. A skill whose frontmatter it cannot read counts as not stable, and the run prints why each pack starts disabled.
- `labs/<name>`: always `false`.

Promoting a pack's last skill to `stable` therefore changes the marketplace, and `--check` reports it until the generator runs again.

The Codex install copies every `packs/*/skills/<skill>/` folder (and `labs/` with `--include-labs`) into `PROJECT_DIR/.agents/skills/`, as real files rather than symlinks, without each skill's `evals/` folder. It writes `PROJECT_DIR/AGENTS.md` only when that file is absent, and it never replaces a skill folder it did not install unless `--force` is given.
