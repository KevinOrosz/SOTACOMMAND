# Second brain: SOTASECONDBRAIN as long-term memory

SOTACOMMAND remembers a session through two small files, `memory/HANDOFF.md` and `memory/lessons.md`. That memory is short by design. For what the team should know for months (research, decisions, how things work), connect a [SOTASECONDBRAIN](https://github.com/KevinOrosz/SOTASECONDBRAIN) knowledge base: a plain-Markdown wiki where agents propose pages and only you approve them.

**TL;DR:** clone SOTASECONDBRAIN next to your project, add a `second_brain` block to `os.config.json`, and register the roles that may propose. Boot then reports the wiki and the proposals waiting for you, agents can't write into the wiki directly, and the close gate can lint it.

## What you get

| Piece | What it does | Where |
|---|---|---|
| Boot line | `second brain: OK at ../my-brain: 42 wiki page(s), 3 proposal file(s) in KB/inbox for the owner to review`. It reads files only. | `engine/tools/session_open.py` |
| Write guard | Any Write or Edit under the brain's `KB/` is denied. Agents propose with `bin/kb write put` instead. | `engine/tools/protect_paths.py` |
| Close lint | With `lint_on_close`, a session can't finish while `bin/kb lint` fails. | `engine/tools/session_close_gate.py` |
| Long-term search | Roles search the wiki with `bin/kb query` or `bin/kb search` before they research from scratch. | the roles' own Bash tool |

## Set it up

1. **Get the second brain.** Clone it next to your project. The easy way: open it in your AI tool and type `WIZARD`, the same word that starts this repo. By hand, following its own `QUICKSTART.md`:

   ```bash
   git clone https://github.com/KevinOrosz/SOTASECONDBRAIN.git my-brain && cd my-brain
   ./bootstrap.sh --owner <you>
   bin/kb doctor
   ```

2. **Let the roles propose.** In the brain's `KB/kb.json`, list the agent ids that may write proposals. Ids are uppercase, so the researcher proposes as `RESEARCHER`:

   ```json
   { "owner": "<you>", "agents": ["RESEARCHER", "BUILDER"] }
   ```

3. **Connect it.** In your project's `os.config.json` (you edit it by hand: agents can't):

   ```json
   "second_brain": {
     "path": "../my-brain",
     "lint_on_close": true
   }
   ```

   `path` is the brain's folder, the one holding `bin/kb` and `KB/`, absolute or relative to the project. `lint_on_close` is optional and off by default.

4. **Check it.** Start a session, or run `python3 tools/wizard/wizard.py check --target <your project>` from your SOTACOMMAND clone. You want the `second brain: OK` line.

## How the team uses it

- **Before researching**, the researcher searches what is already known: `bin/kb query "billing providers"`, run in the brain's folder.
- **To add knowledge**, a role writes a draft in the project, then proposes it:

  ```bash
  bin/kb write put --agent RESEARCHER --kind article --slug billing-providers \
    --category research --type article --confidence moderate \
    --source-ref raw:billing-notes.md --body-file ../my-project/draft.md
  ```

  The proposal lands in `KB/inbox/RESEARCHER/`, with a ledger row. Nothing reaches the wiki yet.
- **You review and land.** `bin/kb write list --diff` shows every proposal. `make land ID=RESEARCHER/<date>-<slug>` lands one after you type `approve`, and `make unland` undoes it. Landing runs the brain's own checks and restores every byte if one fails.
- **At close**, with `lint_on_close`, the close gate runs `bin/kb lint`. A failure sends the session back to fix it, the same way a failed `close_gate.verify` does.

## Boundaries

- The engine never runs the brain's code at boot. It counts files.
- Direct writes into the brain's `KB/` are blocked for every role, the main session included. Proposals go through `bin/kb write put`, and the brain's own owner gate decides what lands.
- The write guard covers the file tools (Write and Edit). Roles without Bash (owner-auditor, sensor, repurposer) can't run `bin/kb` at all, so only roles with Bash can propose.
- Outside Claude Code there are no hooks: `AGENTS.md`-based harnesses get the same rules as instructions, not as enforcement.

## Upgrading

The integration reads three things from the brain: `bin/kb`, `KB/wiki/` and `KB/inbox/<AGENT>/`. If a future SOTASECONDBRAIN release moves them, the boot line reports `MISSING` instead of guessing.
