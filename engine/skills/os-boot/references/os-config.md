# os.config.json reference

`os.config.json` sits at the project root and belongs to the owner. The engine ships a template at `templates/os.config.json` inside the plugin; copy it once, then edit it by hand. Plugin updates never touch the copy. Keys that start with `$` are ignored, so `$comment` can hold notes. The T1 memory templates are `templates/memory/HANDOFF.template.md` and `templates/memory/lessons.template.md`; copy them to `memory/HANDOFF.md` and `memory/lessons.md`.

The project root is `CLAUDE_PROJECT_DIR` when Claude Code runs the hooks. Other callers pass `--root`, or the tools search upward from the working directory for `os.config.json`.

## `memory_caps`

Read by `tools/session_open.py` (SessionStart).

```json
"memory_caps": [
  { "path": "memory/HANDOFF.md", "max_bytes": 900, "max_lines": 15 }
]
```

Each entry names a project-relative file and at least one of `max_bytes` and `max_lines` (positive integers). The session-open report marks each file OK, OVER, MISSING or INVALID and shows its size. The report states facts; it blocks nothing. `session_open.py --text` prints the same report and exits 1 when a file needs attention, for use outside Claude Code.

## `protected_paths`

Read by `tools/protect_paths.py` (PreToolUse on Write and Edit).

```json
"protected_paths": ["os.config.json", "profile/**", ".git/**"]
```

No agent may write a matching path. Patterns are relative to the project root and anchored there:

- `*` and `?` match within one path segment; `[abc]` is a character class.
- `**` matches any number of segments, so `**/.env*` matches at any depth.
- A pattern also covers everything below a matching directory: `profile`, `profile/` and `profile/**` protect the same files.
- Matching ignores case, so the rule holds on case-insensitive filesystems.

Paths are checked as written and with symlinks resolved, and relative to the nearest git checkout as well, so the rules also hold inside worktrees under `.claude/worktrees/`. Keep `os.config.json` itself in the list so agents cannot loosen their own limits.

## `role_write_paths`

Read by `tools/protect_paths.py`.

```json
"role_write_paths": { "owner-auditor": ["reports/**"] }
```

A role's write scope starts with the `write-paths` declared in its role source, which the generator writes to `tools/role_policy.json` in the plugin. Entries here are added to that scope and cannot remove any of its paths. A role whose source declares no scope may write anywhere outside `protected_paths`; an entry here gives it a scope, so it may then write only the listed paths. To forbid a path for every role, use `protected_paths`. Scopes match case-sensitively. The role comes from the hook input's `agent_type`, with the plugin prefix removed.

## `shell_guard`

Read by `tools/protect_shell.py` (PreToolUse on Bash and PowerShell).

```json
"shell_guard": {
  "deny": ["\\bsudo\\b"],
  "ask": ["\\bgit\\s+push\\b"]
}
```

Regular expressions matched against the whole command. A `deny` match blocks it. An `ask` match hands the decision to you: Claude Code shows the command and waits for your yes. Use `ask` for anything that publishes, sends or spends.

The same hook also applies `protected_paths`, the role write scopes and the second-brain rule to shell commands. It works out what a command would write: redirections (`>`, `>>`), and the arguments of commands such as `rm`, `mv`, `cp`, `tee`, `sed -i`, `chmod` and `git checkout`, including inside `sh -c` and `$(...)`. Reading a protected file is always allowed. Inline programs (`python -c`, `node -e`) can't be read reliably, so they are denied when they name a protected path, and always for a role that has a write scope.

It is a guard, not a sandbox. It stops the direct and careless routes; a command that builds a path from variables, or a script written elsewhere and then run, can get past it. A role that must never write outside its scope should not have Bash, which is how the generated roles are set up.

## `ledger`

Read by `tools/ledger.py`, which every hook calls.

```json
"ledger": { "enabled": true }
```

The hooks append one line per event to `.sotacommand/ledger.jsonl` in the project: a session starting, a message from you, a refusal by a guard, an approval you were asked for, a failed close, a broken role registry or an over-cap memory file at boot. Session start refreshes `.sotacommand/ledger-summary.md` and adds one line of seven-day counts to the report. The owner-auditor starts from that summary, so its report separates counted events from its own estimates.

- Local only: nothing is sent anywhere.
- Your messages are counted by length; their text is never stored. Shell commands are never stored either, only the guard's reason, cut to 200 characters.
- On when the project has an `os.config.json`; `"enabled": false` switches it off. The file rotates at 2 MB.
- Keep `.sotacommand/**` in `protected_paths` so agents can read the ledger but not edit it.

Print it yourself with `python3 <plugin>/tools/ledger.py report --root . --days 7`.

## `recall`

Read by `tools/recall.py`.

```json
"recall": { "keep": 200 }
```

At session start, when `memory/HANDOFF.md` differs from the last copy kept, a copy goes to `.sotacommand/history/handoff-<UTC time>.md`. The newest `keep` copies stay (default 200). `recall.py "<words>"` ranks passages from that history, `memory/`, `profile/owner.md` and `TEAM.md`, using a full-text index built in memory on each call. Local only. Keep `.sotacommand/**` in `protected_paths` so the history can't be rewritten.

## `skill_loop`

Read by `tools/skill_loop.py`.

```json
"skill_loop": { "min_sessions": 5 }
```

A skill drafted from an owner-auditor proposal starts as experimental. `promote` marks it stable only when the ledger shows at least `min_sessions` sessions since it was adopted (default 5) with none of the refusals it was written to end, against a baseline of at least one. A skill with no `match` text can't be measured and stays experimental.

## `close_gate`

Read by `tools/session_close_gate.py` (Stop).

```json
"close_gate": {
  "verify": "python3 -m unittest discover -s tests",
  "timeout_seconds": 300,
  "block_repeats": false
}
```

- `verify`: a shell command string, or a list of strings that runs without a shell, in the project root. Empty or absent means the gate passes.
- `timeout_seconds`: positive integer, default 300, capped at 570 so the gate reports before the hook's own 600-second timeout.
- `block_repeats`: when false (the default), a verify that still fails after the gate already sent Claude back once lets Claude stop with a visible hook error. When true, the gate keeps blocking until Claude Code's limit of 8 consecutive continuations.

Stop fires at the end of every turn, so keep `verify` fast. The gate skips the check while background tasks are still running.

## `second_brain`

Optional. Connects a SOTASECONDBRAIN knowledge base as the team's long-term memory. Read by `tools/session_open.py` (SessionStart) and `tools/session_close_gate.py` (Stop).

```json
"second_brain": {
  "path": "../my-brain",
  "lint_on_close": true
}
```

- `path`: the second brain's folder (the one that holds `bin/kb` and `KB/`), absolute or relative to the project root.
- `lint_on_close`: when true, the close gate runs `bin/kb lint` there after `verify`, and a failure blocks like a failed verify. Default false.

At boot, session-open adds one line: the wiki page count and how many proposals wait in `KB/inbox` for the owner. It reads files only. A `path` without `bin/kb` and `KB/` is reported as `MISSING`. Agents propose with `bin/kb write put`; only the owner lands proposals. The full wiring is in `integrations/second-brain/README.md` in the SOTACOMMAND repository.

## Failure behavior

| Situation | session_open | protect_paths | close gate |
|---|---|---|---|
| No `os.config.json` | reports that caps were not checked | only role scopes apply | passes |
| Invalid JSON | reports it | denies every write until it is fixed | blocks once, then lets Claude stop |
| Tool crash or missing `python3` | hook error, session continues | the write is blocked (`onFailure: block`) | hook error, Claude stops |
