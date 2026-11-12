---
type: llm
focus:
  source: file
  path: CHANGELOG.md
---

PASS if CHANGELOG.md has an Unreleased section whose Fixed subsection holds exactly one bullet, and that bullet describes the crash on an empty config file and references issue #42.
FAIL if the entry sits under any other heading, if a version number or release date was invented for it, or if the file holds more than one bullet.
