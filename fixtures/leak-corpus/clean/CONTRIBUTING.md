# Contributing

Rules for contributors are short: open a draft PR, keep the diff in one lane,
and paste the commands you ran with their output.

Every lint rule has a test. Lessons learned go into the changelog as prose.
Run the checks locally before you push:

    python3 -m unittest discover -s tests -t .
    python3 tools/repo/leakgate/leakgate.py bench
