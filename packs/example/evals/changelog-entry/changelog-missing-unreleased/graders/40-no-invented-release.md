---
type: regex
target:
  source: file
  path: CHANGELOG.md
pattern: '^## \[(?!Unreleased\]|1\.2\.0\]|1\.1\.0\])'
flags: m
match: 'not_contains'
---
