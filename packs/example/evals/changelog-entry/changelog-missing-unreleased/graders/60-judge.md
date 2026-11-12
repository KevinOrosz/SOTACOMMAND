---
type: llm
focus:
  source: file
  path: CHANGELOG.md
---

PASS if CHANGELOG.md gained an Unreleased section placed above the 1.2.0 section, holding an Added subsection with one bullet about the new --json option of the export command, and the 1.2.0 and 1.1.0 sections are unchanged.
FAIL if the entry was added to the 1.2.0 or 1.1.0 section, if a new version number or release date was invented, or if either released section changed.
