---
type: regex
target:
  source: file
  path: CHANGELOG.md
pattern: '^## \[Unreleased\]\n+### Fixed\n+- [A-Z][^\n]*\(#42\)\.$'
flags: m
---
