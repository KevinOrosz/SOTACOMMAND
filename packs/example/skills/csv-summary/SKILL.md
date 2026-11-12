---
name: csv-summary
description: Summarize a CSV file into summary.md with its row count, a total for every numeric column and a table of negative values by row. Use when the user asks to summarize, total or sanity-check a CSV file, or asks for a summary.md of tabular data.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
  pack: example
  min-tier: low
  roles: builder, researcher
  gate: none
---

# CSV summary

Read the CSV file the user names. If they name none and the working directory holds exactly one `.csv` file, use that one. Write the summary to `summary.md` in the working directory unless the user asks for another path. Then reply with one line giving the row count and the path you wrote.

## Read the file

- The first row is the header. Every later row that isn't blank is a data row.
- Fields are separated by commas. A field in double quotes can contain commas, and `""` inside quotes is one literal quote.
- Trim spaces around each field.
- Number the data rows from 1: the first row after the header is row 1.

## Decide which columns are numeric

- A column is numeric when every non-empty cell in it is a plain number: an optional leading `-`, digits, and optionally a decimal point followed by digits, such as `12`, `-3`, `4.50` or `-0.25`.
- Empty cells don't make a column non-numeric. Any other text does, including currency symbols, thousands separators and dates.
- A column whose cells are all empty is not numeric.

## Total the numeric columns

- Add every non-empty cell of each numeric column.
- Write each total with as many decimal places as the most precise cell in that column: `4.5` and `1.25` total `5.75`, and `12` and `30` total `42`.
- Don't use thousands separators. Write a negative total with a leading `-`.
- Add each column a second time before you write its total, and fix any difference.

## Flag negative values

List every cell of a numeric column whose value is below zero, ordered by row and then by column. Copy each value exactly as the file writes it.

## Write summary.md

Use exactly this structure, with one totals row per numeric column in header order:

```markdown
# Summary of <file name>

- Rows: <number of data rows>
- Columns: <number of columns>

## Column totals

| Column | Total |
|---|---|
| <column> | <total> |

## Negative values

| Row | Column | Value |
|---|---|---|
| <row number> | <column> | <value as written> |
```

When no value is negative, write `None.` on the line after the `## Negative values` heading instead of that table. When no column is numeric, write `None.` after the `## Column totals` heading instead of that table.
