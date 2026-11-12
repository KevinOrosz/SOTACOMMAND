---
name: csv-summary
description: Summarize a CSV file into summary.md with row count, column totals and flagged negative values. Use when the user asks for a quick CSV summary.
license: Apache-2.0
metadata:
  version: "1.0.0"
  status: experimental
  pack: example
  min-tier: low
  roles: builder
  gate: none
---

1. Read the CSV with the standard library.
2. Count rows, total each numeric column and list negative values.
3. Write `summary.md`.
