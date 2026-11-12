# Leak gate

`leakgate.py` stops private material from leaving this repository: personal names, email addresses, home-directory paths, keys and tokens, numbered house rules, dated rulings and vendor model identifiers. It proves itself in both directions with a benchmark: every planted leak must be caught, and no legitimate text may be blocked.

Standard-library Python 3.9 or later. No network calls, no model calls.

## Commands

```sh
python3 tools/repo/leakgate/leakgate.py check PATH... [--json]
python3 tools/repo/leakgate/leakgate.py bench [--json]
python3 tools/repo/leakgate/leakgate.py hash-denylist PLAIN.txt --salt-file FILE [--out FILE] [--create-salt]
```

### `check`

Scans files and directories. A directory inside a git work tree contributes the files git would publish: tracked files plus untracked files that aren't ignored. Pass `--all-files` to scan every file under a directory instead. A file named on the command line is always scanned.

| Option | Effect |
|---|---|
| `--json` | Print one JSON document on stdout (the extraction tool reads it) |
| `--config FILE` | Use another rules file instead of `rules.json` |
| `--denylist FILE` | Add a plaintext term list (repeatable) |
| `--hashed-denylist FILE` | Add a published list of salted term hashes (repeatable) |
| `--salt-file FILE` | Salt for hashed lists; otherwise `$LEAKGATE_SALT` |
| `--all-files` | Ignore git and scan every file |
| `--exclude GLOB` | Skip matching paths, relative to the root (repeatable) |
| `--root DIR` | Root that relative paths and globs are measured from |
| `--mode check\|bench` | Which allowlist entries apply (default `check`) |
| `--show-allowed` | Also list findings an allow entry covers, for audit |
| `--context N` | Show N characters of context, with every match masked |
| `--require-env-denylist` | Exit 2 when `$LEAKGATE_DENYLIST` holds no terms |

Findings never contain the matched text. An excerpt names the rule and the position, for example `[email @ 1:9]`.

### `bench`

Scans `fixtures/leak-corpus/planted/` and `fixtures/leak-corpus/clean/` with the test-only `fixtures/leak-corpus/bench.json` config, and prints:

```text
recall=<caught>/<planted>
false_blocks=<n>
```

A few planted values have the exact shape of a real provider token. They are stored with `{{seal}}` after their first character (`s{{seal}}k_test_...`), so GitHub's secret push protection doesn't block pushes of this repository. The bench copies the corpus to a temporary folder and removes the seals there before scanning; the scanner itself never removes one. Add a new provider-shaped sample the same way: a test fails if one is stored unsealed.

`planted/expected.json` declares every finding the gate must report, as path, line and rule id. A planted finding is caught when a finding has the same path, line and rule. Any other finding in `planted/` is unexpected, and any finding in `clean/` is a false block. The bench passes only with full recall, nothing unexpected and zero false blocks. It ignores the real environment, so a CI secret can't change its result.

### `hash-denylist`

Writes a JSON list of salted SHA-256 hashes of normalized terms. The list is safe to commit; the salt and the plaintext are not.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean (for `bench`: the benchmark passed) |
| 1 | Findings (for `bench`: a leak was missed, an unexpected finding fired, or clean text was blocked) |
| 2 | Usage or config error, an unreadable file, or an internal error. The gate never exits 1 unless it found something |

## Rules

Rules live in `rules.json`. Every content rule has positive and negative samples in `fixtures/leak-corpus/samples/rule-samples.json`, and the tests keep the two in step. Rule types:

- **`regex`**: shapes such as emails (reserved example domains allowed), home paths, API keys and tokens, private-key headers, numbered rules and lessons, dated rulings and model identifiers.
- **`term`**: literal terms. A term with no lowercase letters is matched case-sensitively, so an all-caps codename doesn't match the same word in prose.
- **`hashed-term`**: names matched by salted hash, so the list can be published.
- **`env-denylist`**: terms read from `$LEAKGATE_DENYLIST`, never written to disk.
- **`path`**: deny globs such as `internal/**`, `**/KB/**`, `**/.env*` and `**/HANDOFF.md`.

`allow` entries are exceptions scoped by path glob, rule and mode, and each one must give a reason. Run with `--show-allowed` to see what they hide.

## Adding private terms without publishing them

Never put a private name in `rules.json`. Use one of these instead:

1. **A CI secret.** Put the terms, one per line, in the repository secret `LEAKGATE_DENYLIST`. CI passes it to the gate; it is never written to a file.
2. **A hashed list.** Hash the terms and commit only the hashes:

   ```sh
   python3 tools/repo/leakgate/leakgate.py hash-denylist private-terms.txt \
     --salt-file ~/.config/leakgate/salt --create-salt --out hashed-denylist.json
   ```

   Keep the plaintext and the salt out of the repository, and store the salt in the `LEAKGATE_SALT` secret for CI.

## Running it

Locally, before you push:

```sh
git ls-files -z -- . ':(exclude)fixtures/' ':(exclude)internal/' | xargs -0 python3 tools/repo/leakgate/leakgate.py check
python3 tools/repo/leakgate/leakgate.py bench
```

The pre-commit hook in `.pre-commit-config.yaml` runs `check` on staged files and re-runs `bench` when the gate or its corpora change. In CI, the `Leak gate` job in `.github/workflows/ci.yml` runs both on every pull request.

## Tests

```sh
python3 -m unittest discover -s tests/leakgate -t .
```
