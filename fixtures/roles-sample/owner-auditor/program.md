## Program

1. Read `.sotacommand/ledger-summary.md` first. It holds counts the engine's hooks recorded: sessions, the owner's messages, refusals by each guard, approvals asked and failed closes. Report these numbers as counted. If the file is missing, say the ledger is off and that every number below is an estimate.
2. Read the session sitreps, the loop log and the open board for the audit period.
3. List every point where the owner had to step in, with its cause and its cost in minutes. Minutes and causes are your judgment: label them as estimates, and never present an estimate as a count.
4. Group interventions by cause and rank the causes by total owner time.
5. Propose one fix for the top cause. A refusal the ledger shows more than once is a strong candidate. Then, and say what the owner still has to decide.
6. When the top cause is something the team keeps doing wrong the same way, propose a skill: write `memory/skill-proposals/<name>.md`, starting with a `---` block that holds `name` (lowercase words joined by dashes, the same as the file name), `description` (when to use the skill) and, when the ledger shows the refusal it should end, `match` (text copied from that refusal). Put the steps under the block. A proposal changes nothing until the owner approves it.
7. Write the ledger to `memory/owner-ledger.md`.
