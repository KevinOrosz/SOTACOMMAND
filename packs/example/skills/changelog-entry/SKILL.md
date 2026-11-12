---
name: changelog-entry
description: Add an entry for a described change to CHANGELOG.md in Keep a Changelog format, under Unreleased and the right change type (Added, Changed, Deprecated, Removed, Fixed or Security). Use when the user asks to log, record or note a change in the changelog, or wants a changelog line for work that isn't released yet.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
  pack: example
  min-tier: low
  roles: builder
  gate: none
---

# Changelog entry

Record each change the user describes in `CHANGELOG.md` in the working directory, or in the changelog file they name, following [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). Change only what these rules call for. Then reply with the bullet you added and the heading it went under.

## Pick one change type

| Type | Use it when the change |
|---|---|
| Added | adds a feature, option, command or supported format |
| Changed | changes how existing behavior works |
| Deprecated | marks something for removal in a later release |
| Removed | removes a feature, option or supported platform or version |
| Fixed | fixes a bug |
| Security | fixes a vulnerability |

## Write the bullet

- One bullet per change, in the imperative mood like a commit subject: `- Add ...`, `- Fix ...`, `- Drop ...`.
- Say what changed for users, start with a capital letter and end with a period.
- When the user gives an issue or pull request number, end the sentence with it in parentheses: `- Fix the timeout when the server is slow to respond (#17).`
- Put flags, commands and file names in backticks.

## Place it

1. Find the `## [Unreleased]` section. It comes before every released section. If it's missing, add it right after the introduction, followed by one blank line.
2. Inside it, find the `### <Type>` subsection for the change type. If it's missing, create it so the subsections keep this order: Added, Changed, Deprecated, Removed, Fixed, Security. Never create a second subsection of the same type.
3. Append the bullet after the last bullet of that subsection, on the next line, with no blank line in between.
4. Keep exactly one blank line after every heading and one blank line before the next heading.
5. Leave released sections such as `## [1.2.0] - 2026-10-01` and the link references at the bottom exactly as they are. Never invent a version number or a release date.

## When CHANGELOG.md doesn't exist

Create it with this introduction, then add the entry under `## [Unreleased]`:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add a `--dry-run` flag to the `deploy` command.
```
