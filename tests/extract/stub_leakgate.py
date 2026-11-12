#!/usr/bin/env python3
"""A small stand-in for the leak gate, so the extraction tests don't depend on it.

test_cli.StagedTreeIsUsable runs one extraction end to end with the real gate.

It speaks the leak gate's command contract from AGENTS.md::

    stub_leakgate.py [options] check <paths...> [--json]
    exit 0 clean, 1 findings, 2 usage or config error

Options (they come before ``check``, because the extraction tool appends
``check <dir> --json`` to whatever ``--leakgate`` command it is given):

    --terms-from MANIFEST   flag every persona_map and owner_terms term in an
                            extraction manifest, and every flag_patterns match
    --term TEXT             flag TEXT (repeatable)
    --shape SHAPE           JSON output: object (default), list or lines
    --behave MODE           normal (default), blind, crash, usage-error,
                            garbage, lie, exit1-empty, or sleep=SECONDS

It also reads newline-separated terms from LEAKGATE_DENYLIST, like the real
gate's env-denylist rule, and flags absolute home-directory paths. Terms match
case-sensitively on letter-and-digit boundaries. Excerpts are redacted to the
rule and the position, as the real gate does. Standard library only.
"""

import json
import os
import re
import sys
import time

HOME_ROOTS = ("Users", "home")
HOME_PATH = re.compile(r"(?<![\w.\-])/(?:%s)/[A-Za-z0-9._-]+" % "|".join(HOME_ROOTS))


def fail_usage(message):
    sys.stderr.write("stub_leakgate: %s\n" % message)
    return 2


def load_rules(terms_from, extra_terms):
    """Return ``[(rule_id, compiled regex)]``."""
    terms = list(extra_terms)
    terms.extend(line.strip() for line in os.environ.get("LEAKGATE_DENYLIST", "").splitlines() if line.strip())
    patterns = []
    if terms_from:
        with open(terms_from, encoding="utf-8") as handle:
            manifest = json.load(handle)
        terms.extend(manifest.get("persona_map", {}))
        terms.extend(manifest.get("owner_terms", {}))
        for item in manifest.get("flag_patterns", []):
            patterns.append(("manifest-" + item["id"], re.compile(item["pattern"])))
    rules = [("home-path", HOME_PATH)]
    for term in sorted(set(terms)):
        body = r"\s+".join(re.escape(word) for word in term.split())
        rules.append(("term", re.compile(r"(?<![^\W_])%s(?![^\W_])" % body)))
    return rules + patterns


def iter_files(paths):
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, files in os.walk(path):
                dirs.sort()
                for name in sorted(files):
                    yield os.path.join(folder, name)
        elif os.path.isfile(path):
            yield path


def scan(paths, rules):
    findings = []
    for path in iter_files(paths):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                for rule_id, regex in rules:
                    for match in regex.finditer(line):
                        findings.append(
                            {
                                "path": path,
                                "line": number,
                                "col": match.start() + 1,
                                "rule_id": rule_id,
                                "excerpt": "[%s at col %d]" % (rule_id, match.start() + 1),
                            }
                        )
    return findings


def emit(findings, shape):
    if shape == "list":
        print(json.dumps(findings, indent=2))
    elif shape == "lines":
        for finding in findings:
            print(json.dumps(finding))
    else:
        print(json.dumps({"findings": findings}, indent=2))


def main(argv):
    terms_from, terms, shape, behave = None, [], "object", "normal"
    i = 0
    while i < len(argv) and argv[i] != "check":
        option = argv[i]
        if option in ("--terms-from", "--term", "--shape", "--behave") and i + 1 < len(argv):
            value = argv[i + 1]
            if option == "--terms-from":
                terms_from = value
            elif option == "--term":
                terms.append(value)
            elif option == "--shape":
                shape = value
            else:
                behave = value
            i += 2
            continue
        return fail_usage("unknown option %r" % option)
    if i >= len(argv):
        return fail_usage("expected: check <paths...> [--json]")
    rest = argv[i + 1 :]
    paths = [arg for arg in rest if arg != "--json"]
    if not paths:
        return fail_usage("check needs at least one path")

    if behave.startswith("sleep="):
        time.sleep(float(behave.split("=", 1)[1]))
        behave = "normal"
    if behave == "crash":
        raise RuntimeError("simulated leak gate crash")
    if behave == "usage-error":
        return fail_usage("simulated config error")
    if behave == "garbage":
        print("this is not json")
        return 0
    if behave == "blind":
        emit([], shape)
        return 0
    if behave == "exit1-empty":
        emit([], shape)
        return 1

    findings = scan(paths, load_rules(terms_from, terms))
    if behave == "lie":
        emit(findings or [{"path": paths[0], "line": 1, "col": 1, "rule_id": "term", "excerpt": "[term]"}], shape)
        return 0
    emit(findings, shape)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
