# Roadmap and known gaps

SOTACOMMAND is at v0.1. This page is the honest list of what isn't done yet. Each gap has an id. Gaps that live in a specific piece of code also carry a `GAP-n` comment there, so `git grep GAP-` finds them. When a gap closes, its row moves to `CHANGELOG.md`.

## Known gaps

| Id | Gap | What it affects |
|---|---|---|
| GAP-6 | The engine's hooks are tested with the input Claude Code sends, but not yet in a recorded live session. The same goes for the Hermes Agent adapter on a real Hermes install. | Confidence in the enforcement |
| GAP-8 | More roles' protocols, memory-tier tools and a router are planned from an existing private system, and aren't in the repository yet | A fuller engine |
| GAP-9 | Two example skills, and the target is 15 stable skills. No skill has a committed eval result yet, the engine's six eval cases included; runs need an API key | Proof numbers |
| GAP-11 | A listing in the Claude Code plugin directory, and a call to action at the end of the README | Discovery |
| GAP-15 | Early commits are unsigned, so the signed-commits branch rule waits | Branch rules |

Want to help with one? Open an issue that names the gap id.

## Closed

GAP-1, 2, 3, 4, 5, 7, 10, 12, 13 and 14 are closed; `CHANGELOG.md` says how. Ids are never reused.

## What's next

| Next | Why |
|---|---|
| A recorded live run of WIZARD in Claude Code | Closes GAP-6, and is the best demo |
| First eval results for the example and engine skills | Puts real numbers in the README's proof table |
| More skill packs, each earning `stable` through its evals | The library people come for |
| A voice kit: the owner's writing voice as a reusable skill | The most-asked-for helper behaviour |

## Proposing work

Open an issue with the skill proposal or harness support form, or a pull request that follows `CONTRIBUTING.md`. Upgrades are welcome. The stable rule and the leak gate keep the bar where it is.
