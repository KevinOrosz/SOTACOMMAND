---
type: llm
focus:
  source: file
  path: summary.md
---

PASS if summary.md reports 4 data rows and 4 columns (a quoted vendor name that contains a comma is one field), totals of 477.75 for amount and 38.22 for tax, and says that no value is negative.
FAIL if the row or column count is wrong, if either total is wrong, or if any value is reported as negative.
