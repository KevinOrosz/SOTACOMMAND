---
title: Concepts
nav_order: 3
description: "The four layers: a team of roles with hard boundaries, skills that must prove themselves, tiered memory with caps, and governance that verifies work."
---

# Concepts

The OS has four layers: [team](#team), [skills](#skills), [memory](#memory) and [governance](#governance). Each is a set of plain files, and each comes with rules that tools and CI check, so the system doesn't depend on an agent remembering to behave.

## Team

A team is a set of roles, and each role is a contract: what it owns, what it hands back, and a hard boundary it never crosses. Roles are named for the job they do. Display names are optional configuration, so you can rename them without changing any contract.

Every request goes to the orchestrator first. It routes the work to exactly one owner, and the specialist works inside its own lane and hands back a defined result. The orchestrator then accepts the result or sends it back.

```text
                                    owner
                                      |
                                      | request
                                      v
                             +----------------+
                             |  orchestrator  |  routes, sequences, accepts
                             +----------------+
                                      |
        +-------------+---------------+---------------+-------------+
        |             |               |               |             |
        v             v               v               v             v
 +------------+  +---------+  +---------------+  +--------+  +------------+
 | researcher |  | builder |  | owner-auditor |  | sensor |  | repurposer |
 +------------+  +---------+  +---------------+  +--------+  +------------+
   evidence,      artifact     burden ledger,     audience       drafts
      spec         + proof         top fix         brief          only
        |             |               |               |             |
        +-------------+---------------+---------------+-------------+
                                      |
                                      | handoff: result + done conditions
                                      v
                  orchestrator accepts it or sends it back
```

| Role | Owns | Hands back | Hard boundary |
|---|---|---|---|
| orchestrator | Intake, routing, sequencing, synthesis, acceptance and the final handoff | A route plan, the decisions left to the owner, and a status report | Never does a specialist's work without loading that role first. The owner steers; the orchestrator never opens with directives. |
| researcher | Research, analysis, specs, and knowledge and file engineering | Evidence, a thesis, a build-ready spec and a self-scored rubric | Doesn't take on builds or content work |
| builder | Code, apps, automations and tests | A working artifact and proof that it was verified | Stops on consequential blockers and doesn't set product direction |
| owner-auditor | The time, attention and repeated intervention the system costs its owner, measured with a fixed set of checks | A burden ledger, the defect behind it, the highest-leverage fix and the decision left to the owner | No health, finance, security or wellbeing lane, and no review of specialist work |
| sensor | Read-only audience evidence, summarized in one periodic brief | Verbatim audience language and one falsifiable bet | No account access, replies or content creation |
| repurposer | Turning one approved piece into drafts for each platform, in the priority order set in configuration | Drafts only | Never posts, uploads, schedules or invents material |

**Boundaries are enforced, not requested.** The engine plugin turns each role into a Claude Code agent whose tool list enforces its boundary. The sensor gets read-only tools, the repurposer gets no posting tools, the builder works in an isolated git worktree, and a protected-path hook confines the owner-auditor's writes to memory. Roles also declare a capability tier (low, mid or high) instead of a model, and the agents run on whatever model the session uses. In other harnesses, the same contracts ship as plain markdown.

## Skills

A skill is a folder holding a `SKILL.md` file in the open Agent Skills format: a short frontmatter block, then the instructions. The frontmatter uses only the portable keys (`name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`), so the same folder loads wherever the format is supported, including Claude Code, claude.ai and Codex. Project fields such as the version and the status go under `metadata`, as plain strings:

```yaml
---
name: release-notes
description: Drafts release notes from merged changes. Use when preparing a release.
metadata:
  version: "1.0.0"
  status: experimental
  pack: ops
  min-tier: mid
  roles: orchestrator,builder
  gate: none
---
```

Skills ship in packs. Each pack is a Claude Code plugin under `packs/`, with its skills under `skills/` and its eval cases under `evals/`. Experimental packs live under `labs/` and install switched off.

Every skill moves through a status lifecycle, and only a measured run moves it forward:

```text
   new skill
       |
       v
 +--------------+    delta at or below 0  +--------------------------+
 | experimental | ----------------------> | rework and run again,    |
 +--------------+ <---------------------- | or retire                |
       |                 reworked         +--------------------------+
       | qualifying run meets                          |
       | the stable bar                                | retire
       v                                               v
 +--------------+          replaced          +--------------------+
 |    stable    | -------------------------> |      retired       |
 +--------------+                            | a stub that points |
                                             | to its replacement |
                                             +--------------------+
```

| Status | What it means | How it behaves |
|---|---|---|
| experimental | Loads, but has no qualifying run yet | Offered, never fired on its own, and says that it's unproven |
| stable | Passed a recorded qualifying run; its eval cases and results ship with it | Fires on matching requests and says which skill it is |
| retired | Replaced or withdrawn | Ships only as a short stub that points to its replacement |

Some skills are mandatory gates: they fire in every situation that matches, and a skill that bundles others never hides one. [Qualify a skill](qualify-a-skill.md) gives the exact stable rule and how to measure it.

## Memory

Memory comes in three tiers, and only the first loads by itself. Tier 1 is capped at about 1.5 KB, so a long-running project starts every session with the same small, current context instead of a pile that keeps growing.

```text
 +----------------------------------------------------------------+
 | Tier 1: boot memory                      loaded at every start |
 | status report (15 lines max) + standing lessons (12 max)       |
 +----------------------------------------------------------------+
            |
            | entries age out or pass their caps
            v
 +----------------------------------------------------------------+
 | Tier 2: working memory                          read on demand |
 | task board + run ledger                                        |
 +----------------------------------------------------------------+
            |
            | the decay report proposes moves; a move leaves a tombstone
            v
 +----------------------------------------------------------------+
 | Tier 3: archive                             searched on demand |
 | cold storage; nothing is ever deleted                          |
 +----------------------------------------------------------------+
```

- **Caps are checked by tools.** A verification step reports any tier-1 file that's over its cap, so you see the overflow instead of carrying it.
- **Decay is report-only by default.** It proposes what to move, and you decide.
- **Moves leave tombstones.** Nothing is deleted, so you can always trace where an entry went.
- **Board items age out.** Entries on the task board move on after 30 days unless they're pinned.
- **One search covers every tier.** A single recall command searches all three.

## Governance

Governance is the set of rules that keeps a team of agents honest. Here every rule is backed by a tool, a hook or a check, not by a reminder in a prompt.

```text
  session opens              during work                session closes
 +----------------+    +------------------------+    +--------------------+
 | preflight      |    | one owner per artifact |    | close gate         |
 |  memory sizes  | -> | pushback with evidence | -> |  verification      |
 |  drift, stated |    | independent passes,    |    |  must pass         |
 |  as facts      |    |  then one synthesis    |    |  status report,    |
 +----------------+    +------------------------+    |  lessons, run log  |
          ^                                          +--------------------+
          |                                                     |
          +--- the next session starts from the status report --+
```

| Rule | What it means | What enforces it |
|---|---|---|
| Routing | Every artifact has exactly one owner, and lanes don't overlap | The orchestrator's route plan and each role's tool limits |
| Pushback | Agents argue from evidence, tag their confidence, and don't give in without new evidence | The role contracts |
| Convergence | Several independent passes over the same input, then one synthesizer that lists where they agree and where they contradict each other | A workflow that keeps the passes separate |
| Owner-time audit | The owner-auditor measures what the system costs you | Its weekly report |
| Verify before done | Work isn't done until its verification passes | The close gate, which fails while a configured check fails |

Two more rules keep sessions predictable. When a session opens, the preflight reports drift as facts, never as instructions. And when a role source breaks its contract, for example an unknown file in a role folder or a delegation to a role that doesn't exist, the generator stops with an error instead of guessing.
