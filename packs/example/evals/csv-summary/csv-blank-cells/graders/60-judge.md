---
type: llm
focus:
  source: file
  path: summary.md
---

PASS if summary.md reports 5 data rows, totals of 30 for stock, 37 for reorder_level and 17.50 (or 17.5) for unit_cost, gives no total for the sku or item columns, and flags exactly one negative value: -2 in the stock column on row 5.
FAIL if any total is wrong, if an empty cell is counted as a value or as a row, if sku or item gets a total, or if anything other than -2 is flagged as negative.
