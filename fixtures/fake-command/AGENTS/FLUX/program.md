# FLUX: program

1. Read the spec from RUNE and restate the done condition.
2. Write the failing test first, then the smallest change that passes it.
3. Run `tools/verify.sh`; its exit code is the FAIL count.
4. Hand back the diff and the output to SAGE.

The two-reviewer rule (ordered 10-23 by Avery) still stands for anything that ships.
