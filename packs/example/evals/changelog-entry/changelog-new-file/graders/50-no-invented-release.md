---
type: regex
target:
  source: file
  path: CHANGELOG.md
pattern: '^## \[[0-9]'
flags: m
match: 'not_contains'
---
