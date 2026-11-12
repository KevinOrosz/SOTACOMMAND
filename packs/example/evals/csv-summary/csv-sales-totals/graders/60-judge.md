---
type: llm
focus:
  source: file
  path: summary.md
---

PASS if summary.md reports 8 data rows, totals of 510.99 for amount and 16 for units, and flags exactly two negative values: -15.25 in the amount column on row 3 and -30.00 in the amount column on row 8.
FAIL if any of those numbers is wrong or missing, if the header row is counted as a data row, if the date or region column gets a total, or if a value that isn't negative is flagged.
