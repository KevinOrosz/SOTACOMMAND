# Extraction tool

A maintainer tool: it moved the original roles and skills from a private working system into this public tree, with every private name rewritten and the result checked by the leak gate. You don't need it to use SOTACOMMAND.

`extract.py` runs once, locally, against the private source tree. It copies the files a manifest allowlists into a staging tree, applies the manifest's safe rewrites, flags what it can't rewrite safely, and runs the leak gate on the result. It writes nothing unless nothing was flagged and the gate found nothing.

Standard-library Python 3.9 or newer. No network calls, no model calls.

```
python3 tools/repo/extract/extract.py --source DIR --manifest FILE --out DIR \
    [--dry-run] [--report FILE] [--leakgate CMD]
```

| Exit | Meaning | Written? |
|---|---|---|
| 0 | Clean. With `--dry-run`, a real run would write clean. | Yes (no, with `--dry-run`) |
| 1 | Refused: at least one flag or leak-gate finding. | No |
| 2 | Usage, manifest or I/O error. | No |
| 3 | Leak gate unavailable, failed, or failed its canary check. The tool fails closed. | No |

## What it guarantees

- **Allowlist only.** Only files listed under `files` are read for output. Every other source file is counted in the report, never copied. `never_export` globs make it a manifest error to list a private area (KB, outputs, task logs, handoffs, owner fixtures) at all.
- **Nothing half-written.** The output is built in a temporary directory, checked, then copied next to `--out` and renamed into place. `--out` must not exist or must be empty, so a run never merges into or overwrites an earlier tree. A refused run leaves no `--out`, no partial copy and no temporary files.
- **Nothing slips through unreported.** Whatever rewrites an entry lists, every output line is checked for persona names, owner terms and home-directory paths left behind, plus every `flag_patterns` match. Anything found is a flag, and every flag blocks the run.
- **Fails closed.** A missing gate, a crash, a timeout, exit code 2, unreadable output, or an exit code that contradicts the output all mean exit 3. So does a temporary directory inside a git work tree, where a gate may scan only tracked files.
- **The gate proves it can see.** Before scanning the output, the tool runs the gate on a canary: one file per `persona_map` and `owner_terms` term. If the gate misses any of them (typically because it ran without the private denylist), the run fails closed and the report says which terms, masked.
- **Deterministic.** The same source and manifest give byte-identical output and the same report: sorted paths, no timestamps, no temporary paths.

## Before the first run

1. **Keep the real manifest private.** It names the owner, clients and personas. Keep it outside the repository, or under `internal/`, which never ships. `export/.gitignore` ignores `export/manifest.json` and `*.local.json` as a backstop. Start from a copy of `export/manifest.example.json`.
2. **Load the private denylist into the leak gate.** Set `LEAKGATE_DENYLIST` (or use a hashed list; see `tools/repo/leakgate/README.md`). It must hold at least every `persona_map` and `owner_terms` term. The canary checks that it does.
3. **Keep `TMPDIR` outside any repository.** The tool refuses to run its gate inside a git work tree.
4. **Write the report outside the repository too.** It quotes private source text. The tool refuses a `--report` path inside `--out` or `--source`.

## Runbook

### 1. Dry run first

```
python3 tools/repo/extract/extract.py --source ~/path/to/private-tree \
    --manifest ~/private/manifest.json --out ~/private/stage \
    --dry-run --report ~/private/report.md
```

A dry run does everything except write `--out`: it validates the manifest, transforms every file, runs the canary and the gate, and writes the report. Its exit code says what a real run would do.

### 2. Read the report

| Section | What to do with it |
|---|---|
| Result | The verdict and the counts. |
| Errors | Manifest or path problems. Fix them first; nothing else ran. |
| Flags | Every flag, with source file, line, the matched text and why. Each one needs a manifest change. |
| Leak gate | The canary result, then the gate's findings on the output, mapped back to source files. |
| Warnings | Entry rules that matched nothing (stale or mistyped), files left empty. |
| Rewrites by rule | Totals per rule, to spot a rule that fired far more or less than expected. |
| Files | Every exported file: source, line counts, and each rewrite with the source lines it touched. |
| Source files not in the manifest | Counts per top-level folder. Check that nothing you meant to export is missing. |

### 3. Fix the manifest

Handle each flag with the narrowest rule that works, then dry-run again until it exits 0:

| Flag | Typical fix |
|---|---|
| `persona-name` | The name is part of an identifier (`NAME_HOME`). Add an entry `replace` such as `{"find": "NAME_HOME", "with": "OS_HOME"}`. |
| `owner-term` | A case variant or a handle (`name-surname`). Rewrite or drop that line with an entry rule. |
| `home-path` | Add a `path_map` entry (`"~/project/": ""`), or drop the line. |
| model names | Replace the model with its capability tier, using a structural regex (`"find": "^MODEL=.*$"`), so the manifest never spells out the model id. |
| mythic framing | Drop the section: `{"start": "^## Section", "before": "^## "}`. The framing belongs in an optional persona pack. |
| owner identity | Replace the sentence with a neutral mission. |
| client names | Replace with a neutral description, or drop the line or file. |
| rule codes, ruling dates | Add a `rewrite_patterns` entry when the shape is common and the removal always reads right; otherwise rephrase that line with an entry `replace`. |
| `frontmatter-*` | Fix the source skill's frontmatter (one-line values only), or the description. |

`diff export/manifest.example.json export/manifest.handled.example.json` shows a complete worked example: the first-pass manifest and the same manifest after every flag in the fixture was handled.

Never "fix" a flag by deleting the flag pattern that found it, and never by turning the gate off. If a pattern is too broad, narrow it.

### 4. Run for real

The same command without `--dry-run`. It exits 0 and prints the number of files written to `--out`.

### 5. Review the diff by hand before anything leaves staging

The tool is a filter, not a reviewer. Read every staged file against its source:

```
python3 -c 'import json,sys; [print(f["from"], f["to"]) for f in json.load(open(sys.argv[1]))["files"]]' \
    ~/private/manifest.json |
while read -r from to; do diff -u "SOURCE/$from" "STAGE/$to"; done | less
```

Replace `SOURCE` and `STAGE` with your folders. Look for:

- **Grammar.** Persona names become bare role ids, so prose may need an article ("the orchestrator"). Owner terms become "the owner".
- **Meaning.** A drop or a select can remove context a reader needs.
- **Anything private the patterns don't know about**: nicknames, project codenames, third-party names, dates.

Then run the other lanes' checks on the stage (the leak gate, the skills linter), and bring the stage into the staging repository through a reviewed pull request. Nothing goes public until a squashed tree passes the leak gate again.

## Manifest reference

The format is `export/manifest.schema.json` (JSON Schema 2020-12; point an editor at it with `"$schema"`). The tool checks everything the schema does and more: each regex compiles and can't match an empty line, each `from` exists in exact letter case and resolves inside the source, targets are unique ignoring case, and a `.../<skill>/SKILL.md` target applies `frontmatter`. All errors are reported together.

### Sections

| Key | Purpose |
|---|---|
| `schema_version` | Always `"1"`. |
| `defaults.rewrites` | Rule sets for entries that don't list their own. Default: all but `frontmatter`. |
| `persona_map` | Persona name -> role id. Case-sensitive whole words. |
| `owner_terms` | Owner name, nickname or address -> neutral text, usually `"the owner"`. |
| `path_map` | Path prefix -> replacement. `~/` in a key means any home directory. |
| `rewrite_patterns` | Safe regex rewrites: `id`, `pattern`, `replace`. |
| `drop_patterns` | Lines (`pattern`) or blocks (`start` with `end` or `before`) to remove. |
| `flag_patterns` | Text no rule rewrites safely. Any match is a flag. |
| `never_export` | Globs a `from` must never match. |
| `frontmatter` | `drop_keys`, `rename_keys` and `list_separator` for skill conversion. |
| `roles` | Role id -> the `adapters/ROLES.md` frontmatter (`description`, `tier`, and optionally `main`, `isolation`, `allowed-tools`, `disallowed-tools`, `write-paths`). Required for every `engine/roles/<id>/soul.md` target. |
| `plugins` | `packs/<name>` or `labs/<name>` -> `name`, `description`, `version` (default `0.1.0`). The tool generates that folder's `.claude-plugin/plugin.json` and `README.md`. |
| `files` | The allowlist. |

Each `files` entry has `from` and `to`, and optionally `rewrites`, `replace` (entry-scoped exact or regex replacements), `drop` (entry-scoped drops), `select` (export only one block, for splitting a long file), `binary` (copy bytes verbatim; the gate still scans them) and `note` (shown in the report).

### How rules apply

Per line, always in this order: entry `select` and drops, entry `replace`, `rewrite_patterns`, `path_map`, the home-path check, `owner_terms`, `persona_map`, then `flag_patterns` and the residual checks. Entry rules come first because they are written against the source text you read in the report.

- **Word boundaries.** Terms match whole words: a term inside a longer word is left alone, and a term inside an identifier (`NAME_X`) is left and flagged.
- **Case.** `persona_map` is case-sensitive. `owner_terms` rewrites the exact case and the ALL-CAPS form (in capitals) and flags any other variant. In prose, a replacement that starts a sentence is capitalized; code, and fenced code inside Markdown, is never capitalized.
- **Paths.** Absolute and home keys run before relative keys, so a home prefix and a folder rename can both apply to one path. The longest key wins, and keys only match at a path boundary.
- **Line endings, byte order marks and the executable bit** are preserved. Modes are normalized to 0644 or 0755.

### Skill frontmatter

With `frontmatter`, an old-style `SKILL.md` gets the portable format: `name` (always the skill folder's name, so it matches), `description`, `license`, `compatibility` and `allowed-tools` at the top level, and every other key under `metadata` as a double-quoted string, keys sorted. Keys become lowercase kebab-case (`min_tier` becomes `min-tier`), lists are joined (`roles` with `", "`, `allowed-tools` with spaces), maps are flattened (`eval: {score: 9}` becomes `eval-score`) unless `drop_keys` drops them, and comments are removed. Values keep their source text, so `1.0` stays `"1.0"`. The parser accepts a strict YAML subset (scalars, quoted strings, one-line flow lists and maps, block lists, one level of block map) and flags anything else rather than guessing. It also flags a missing, empty, too long (over 1024 characters) or angle-bracketed description, and `roles` entries that aren't role ids from `persona_map`. A `status: stable` becomes `experimental`: stable is earned by a qualifying eval run in this repository (see `tools/repo/skills/CONTRACT.md`), never carried over. Extracted skills belong under `labs/` until they qualify.

### Roles and plugins

Role sources carry prose, not frontmatter. For each `engine/roles/<id>/soul.md` target the tool writes the frontmatter block from the manifest's `roles` section above the body, so the staged roles load with `adapters/osgen`. A source that already has a frontmatter block raises `role-source-frontmatter` instead of guessing which one wins. A `roles` entry with no matching target, or a role target with no `roles` entry, is a manifest error.

For each `plugins` folder the tool generates `.claude-plugin/plugin.json` (license Apache-2.0, author SOTACOMMAND) and a `README.md` listing the folder's skills. A `files` entry that targets either path is a manifest error. The tests check that the staged roles pass `osgen.roles.validate_set`, that the staged plugins lint clean with `skills.py lint`, and that the real leak gate passes the staged tree.

## The leak gate contract

The tool runs `<gate> check <dir> --json` with the default gate `python3 tools/repo/leakgate/leakgate.py` from this repository, or with the `--leakgate CMD` prefix you pass. Exit 0 with no findings is clean and exit 1 with findings is a refusal. The JSON may be a list of findings, an object with a `findings` list, or JSON lines; each finding's `path` (relative to the scanned directory, or absolute), `line`, `col`, `rule_id` and redacted `excerpt` go into the report.

## Tests and the synthetic tree

```
python3 -m unittest discover -s tests/extract -t .
```

The tests never call the real gate: `tests/extract/stub_leakgate.py` speaks the same contract, reads `LEAKGATE_DENYLIST`, and can simulate a blind gate, a crash, a config error, a timeout and contradictory output. `fixtures/fake-command/` is a synthetic private tree with planted markers; `fixtures/fake-command/planted-markers.json` lists each one and what the first-pass manifest must do with it. To try the whole flow on it:

```
python3 tools/repo/extract/extract.py --source fixtures/fake-command \
    --manifest export/manifest.example.json --out /tmp/stage --dry-run --report /tmp/report.md \
    --leakgate "python3 tests/extract/stub_leakgate.py --terms-from export/manifest.example.json"

python3 tools/repo/extract/extract.py --source fixtures/fake-command \
    --manifest export/manifest.handled.example.json --out /tmp/stage --report /tmp/report.md \
    --leakgate "python3 tests/extract/stub_leakgate.py --terms-from export/manifest.handled.example.json"
```

The first exits 1 with every unsafe marker flagged. The second exits 0 and writes the tree. To use the real gate instead, drop `--leakgate` and load the fixture's private terms into its denylist first:

```
export LEAKGATE_DENYLIST="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("\n".join(list(m["persona_map"]) + list(m["owner_terms"])))' export/manifest.handled.example.json)"
```
