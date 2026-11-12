# Start here

This is the one page you need. Part 1 is for people. Part 2 is for AI agents setting SOTACOMMAND up on someone's behalf. Everything else in the repository is reference.

**The one-word start:** open this folder in any AI tool and type **`WIZARD`**. You don't need to know anything about AI agents: it interviews you in plain words and builds your team for you.

**The companion:** [SOTASECONDBRAIN](https://github.com/KevinOrosz/SOTASECONDBRAIN) is the long-term memory for this team. It also starts with `WIZARD`. Use either alone, or [connect them](integrations/second-brain/README.md).

---

## TL;DR

1. `git clone` this repository.
2. Open the folder in your agent tool (Claude Code, Codex, GitHub Copilot, Cursor, Gemini CLI, Hermes Agent, or any agent that can read files and run commands).
3. Type `WIZARD`. It asks about ten plain questions: your name, your brand, business or art project, who it's for, what you want help with, how your team should sound, what needs your OK, and which helpers you want.
4. It builds the team in a folder you choose and hands you `TEAM.md`: your helpers and the exact words to say to each.
5. Open that folder in your tool and ask for something. The Lead hands it to the right helper.

No tool handy? `python3 tools/wizard/wizard.py install --target ~/my-project`, then `python3 tools/wizard/wizard.py interview --target ~/my-project` asks the same questions in your terminal, and `python3 tools/wizard/wizard.py check --target ~/my-project` confirms it.

## Explain it like I'm 10

You know how a sports team works better when everyone has a position? A goalie doesn't run off to score, and a striker doesn't guard the net. SOTACOMMAND does that for AI helpers.

You get a **coach** (the orchestrator) and five **players**: a **builder** who writes code in its own practice field, a **researcher** who finds facts, a **time-keeper** who tracks how much of your time the team uses, a **listener** who reads what people say but never talks back, and a **writer** who makes drafts but never posts them. The rules aren't just written down: a player who isn't allowed to post simply has no "post" button.

The team keeps a tiny notebook so it remembers yesterday, and new tricks only count once they beat "no trick" in a test. Type `WIZARD` and a helper sets the whole team up for you.

---

## Part 1 · For people

### What you need

- **Python 3.9 or later.** Check with `python3 --version`. Mac: `brew install python` or python.org. Linux: your distro's `python3` package.
- **git**, to clone and update.
- **An agent tool.** Any of them. If you use Claude Code you get the strongest version: role limits enforced by the tool itself.

Nothing else: no accounts, no API keys, no packages to install.

### Step 1 · Get it

```bash
git clone https://github.com/KevinOrosz/SOTACOMMAND.git SOTACOMMAND
cd SOTACOMMAND
```

### Step 2 · Boot it

Open the `SOTACOMMAND` folder in your agent tool and type `WIZARD`. It interviews you, then does Steps 3 to 5 for you and shows each command first. Prefer to do it yourself? Keep reading.

### Step 3 · Install into your project

Your project is any folder you want the team to work in, even an empty one. Pick your tool:

| Your tool | Command |
|---|---|
| Claude Code | `python3 tools/wizard/wizard.py install --target ~/my-project --harness claude`, then run the two `claude plugin ...` commands it prints |
| Codex | `python3 tools/wizard/wizard.py install --target ~/my-project --harness codex` |
| GitHub Copilot | `python3 tools/wizard/wizard.py install --target ~/my-project --harness copilot --harness generic` |
| Cursor | `python3 tools/wizard/wizard.py install --target ~/my-project --harness cursor` |
| Gemini CLI | `python3 tools/wizard/wizard.py install --target ~/my-project --harness gemini` |
| Hermes Agent | `python3 tools/wizard/wizard.py install --target ~/my-project --harness hermes`, then run the `hermes skills trust` command it prints |
| A chat assistant that can't read files | `python3 tools/wizard/wizard.py install --target ~/my-project --harness flat`, then paste `SOTACOMMAND.prompt.md` into its instructions |
| Anything else | `python3 tools/wizard/wizard.py install --target ~/my-project --harness generic` |

Add `--create` if the folder doesn't exist yet, and `--dry-run` first to see what it would write. It never overwrites a file that already exists. To take it out again, `python3 tools/wizard/wizard.py uninstall --target ~/my-project` removes what it wrote and keeps every file you changed.

### Step 3b · Do the interview

```bash
python3 tools/wizard/wizard.py interview --target ~/my-project
```

About ten questions, in plain words. Press Enter to skip an optional one. It writes three files into your project:

| File | What it is |
|---|---|
| `TEAM.md` | Your team card: your helpers, what each does for your project, and the exact words to say |
| `profile/owner.md` | Who you are, how the team should sound, what needs your OK, what it must never do. Helpers read it every session and can't change it |
| `profile/answers.json` | Your raw answers |

Changed your mind? Run it again with `--redo`.

### Step 4 · Check it

```bash
python3 tools/wizard/wizard.py check --target ~/my-project
```

You want `wizard: ready`.

### Step 5 · Use it

Open your project in your tool and talk to it normally. Five things to try:

1. *"Add a dark-mode toggle to the settings page."* The builder does it in an isolated git worktree and shows the test output.
2. *"Compare three ways to send transactional email and recommend one."* The researcher returns evidence, a counterargument and a spec.
3. *"Run the owner-auditor for this week."* You get `memory/owner-ledger.md`: where your time went, and the one fix that saves the most.
4. *"Run convergence on: should we move billing to a new provider?"* Independent opinions, then one honest summary of agreements and disagreements.
5. *"Summarize sales.csv into summary.md."* The `csv-summary` skill does it.

### What got installed in your project

| File | What it is | Who changes it |
|---|---|---|
| `os.config.json` | Your settings: memory caps, protected paths, the verify command the close gate runs | You, by hand. Agents can't edit it |
| `memory/HANDOFF.md` | The short status note every session starts from (at most 15 lines) | The orchestrator, at the end of each session |
| `memory/lessons.md` | Standing rules the team keeps (at most 12) | The orchestrator, when you agree a new rule |
| `AGENTS.md` | Instructions any agent tool reads first | You can edit it freely |
| `.agents/skills/` | The skills, as plain folders | Reinstall to update |
| `.github/agents/` | Copilot agent profiles (if you chose Copilot) | Reinstall to update |

### Updating

```bash
cd SOTACOMMAND && git pull
python3 tools/wizard/wizard.py install --target ~/my-project
```

`git pull`, then `python3 tools/wizard/wizard.py update --target ~/my-project` (add `--dry-run` to see the plan first). It replaces files you haven't changed, keeps the ones you have and writes the new version beside each as `<name>.new`, and adds new settings to `os.config.json` after backing it up. Your profile and memory are never touched. Claude Code users also run `claude plugin update sotacommand-engine@sotacommand`.

### If something goes wrong

| You see | Do this |
|---|---|
| `FAIL python` | Install Python 3.9 or later, then try again. |
| `target folder does not exist` | Create it: `mkdir -p ~/my-project`. |
| `MISSING ...` from `check` | Run `install` again. |
| `OVER memory/HANDOFF.md` | The note grew past its cap. Ask the orchestrator to shorten it. |
| Anything else | Type `WIZARD`: it reads the output and tells you what to do. Or open an issue with the bug template. |

---

## Part 2 · For agents

You are setting SOTACOMMAND up for a user. If they typed `WIZARD`, follow [`WIZARD.md`](WIZARD.md) instead: it is the full procedure with its hard rules. Otherwise:

1. **Don't edit this repository.** It is the source. The user's project is the workspace.
2. **Ask for the project folder.** Never guess it.
3. **Detect the harness.** `python3 tools/wizard/wizard.py doctor` lists which agent CLIs are on the machine. Use the one you are running in; add `generic` so any other tool gets the same team.
4. **Preview, then install:** `wizard.py install --target <folder> --harness <h> --dry-run`, show the user, then run it without `--dry-run` after they agree.
5. **Interview:** `wizard.py questions` prints the interview. Ask the questions one at a time in those words, save the answers as JSON, and run `wizard.py interview --target <folder> --answers <file>`. Never invent an answer.
6. **Verify:** `wizard.py check --target <folder>` must print `wizard: ready`. Report every line as a fact.
7. **Show them `TEAM.md`** and walk one line from its **Try saying** column.

### What each harness gets

| Harness | Entry point | How role limits are enforced |
|---|---|---|
| Claude Code | The `sotacommand-engine` plugin: six subagents, hooks, the `os-boot` and `wizard` skills, the convergence workflow; the session starts as the orchestrator | Tool allowlists and denylists per role, a protected-path hook on every write, a git worktree for the builder, a close gate that runs the project's verify command |
| Codex | `AGENTS.md` plus `.agents/skills/` | Written rules in `AGENTS.md`; the agent follows them |
| GitHub Copilot | `.github/agents/<role>.agent.md` | Copilot tool aliases per role, plus written rules for what Copilot can't enforce |
| Any other tool | `AGENTS.md` plus `.agents/skills/` | Written rules |

Model choice is never written in a file. Roles declare a capability tier (`low`, `mid`, `high`); each install maps tiers to models.

### Rules every agent follows here

- Route each task to exactly one role. Hand back evidence with every result.
- Write session state only to `memory/`. Never edit `os.config.json`.
- Report facts, not directives. Stop on a consequential blocker instead of working around it.
- Verify before you say done: run the project's verify command, or the close gate does it for you in Claude Code.

### Building on SOTACOMMAND

Contributing to this repository is different from using it: read [`AGENTS.md`](AGENTS.md) (rules, lane map, command contracts) and [`CONTRIBUTING.md`](CONTRIBUTING.md). Upgrades and new skills are welcome; the stable rule and the leak gate keep the bar high. Known gaps are in [`ROADMAP.md`](ROADMAP.md).
