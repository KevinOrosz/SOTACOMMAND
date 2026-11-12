---
type: regex
target:
  source: file
  path: CHANGELOG.md
pattern: '^### Security$'
flags: m
match: 'count:1'
---
