---
type: llm
focus:
  source: file
  path: CHANGELOG.md
---

PASS if the change is recorded as one bullet in a Security subsection of the Unreleased section, placed after Fixed, that describes the fix for upload file names writing outside the uploads folder and references issue #214.
FAIL if it is filed under Fixed, Changed or any other type, if it sits outside Unreleased, or if the existing bullets or the 0.9.0 section changed.
