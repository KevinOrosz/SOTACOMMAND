---
type: regex
target:
  source: file
  path: CHANGELOG.md
pattern: '^and this project adheres to \[Semantic Versioning\]\(https://semver\.org/spec/v2\.0\.0\.html\)\.\n+## \[Unreleased\]\n+### Added\n+- [A-Z][^\n]*--json[^\n]*\.\n+## \[1\.2\.0\] - 2026-10-01$'
flags: m
---
