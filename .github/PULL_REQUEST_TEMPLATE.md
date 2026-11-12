## Summary

<!-- What changed and why, in two or three sentences. Link the issue if there is one. -->

## Verification (commands and output)

<!--
Paste each command you ran and its exact output. CI runs the same checks, and a
claim without output isn't verification. Run the ones that apply:

  python3 -m compileall -q tools adapters engine tests
  python3 -m unittest discover -s tests -t .
  python3 tools/repo/skills/skills.py lint packs labs
  python3 tools/repo/skills/skills.py registry --check
  python3 adapters/generate.py --check
  python3 tools/repo/site/build.py --check
  claude plugin validate --strict PLUGIN_DIR    (each plugin you changed)
-->

```text

```

## Leak gate output

<!--
Run the gate over every tracked file outside fixtures/, as CI does,
and paste the output. Expect no findings.

  git ls-files -z -- . ':(exclude)fixtures/' | xargs -0 python3 tools/repo/leakgate/leakgate.py check
-->

```text

```

## Eval Δ (if skills changed)

<!--
For each skill you added or changed: the with-skill score, Δ against no skill,
runs per arm, and the run date, from the redacted summary. Never paste raw
results that name models. If no skill changed, write "No skills changed".
-->

| Skill | With-skill score | Δ vs no skill | Runs per arm | Date |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Checklist

- [ ] CI is green, or each red job is explained above.
- [ ] Tests cover the change, including at least one failure case.
- [ ] `CHANGELOG.md` has an entry under "Unreleased" if behavior changed.
- [ ] Nothing private is added: no names, email addresses, home-directory paths, keys or model identifiers.
