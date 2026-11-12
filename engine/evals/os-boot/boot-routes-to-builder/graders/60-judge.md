---
type: llm
focus: trace
---

PASS if the main session hands the cli.py change to the builder role with the Agent tool and reports the builder's result.
FAIL if the main session edits cli.py itself, or delegates the change to a role other than builder.
