#!/usr/bin/env python3
"""Stop hook: the close gate. Claude does not finish while verify fails.

Source: adapters/templates/engine/tools/session_close_gate.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

Reads ``close_gate`` from the project's ``os.config.json``::

    "close_gate": {
      "verify": "python3 -m unittest discover -s tests",
      "timeout_seconds": 300,
      "block_repeats": false
    }

``verify`` is a shell command string, or a list that runs without a shell. It
runs in the project root. With no os.config.json, no ``close_gate`` or an
empty ``verify``, the gate passes.

Exit codes, per the hooks reference for Stop:

* 0: verify passed, or there is nothing to verify. Claude stops.
* 2: verify failed, timed out or could not start. Claude keeps working and
  receives the reason. The reason is also printed as
  ``{"decision": "block", "reason": ...}`` on stdout, so Claude Code reads it
  even when the verify output mentions a missing file.
* 1: verify still fails after the gate already sent Claude back once
  (``stop_hook_active`` is true). This is a non-blocking error: Claude stops
  and the user sees the first stderr line. Set ``block_repeats`` to true to
  keep blocking instead (Claude Code ends the chain after 8 continuations).

When ``second_brain.lint_on_close`` is true, the gate also runs
``bin/kb lint`` in the connected SOTASECONDBRAIN folder after verify, and a
lint failure blocks the same way.

The gate passes without running verify while background tasks are still in
flight, because the session is pausing, not finishing. Stop fires at the end
of every turn, so keep the verify command fast.
"""

import argparse
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple, Union

# The plugin directory is not a place for state, so never write bytecode there.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)
import ledger  # noqa: E402

DEFAULT_TIMEOUT = 300
# Stay under the command-hook timeout (600 seconds) so the gate can report.
MAX_TIMEOUT = 570
TAIL_LINES = 30
TAIL_CHARS = 3000

Command = Union[str, List[str]]


class GateConfigError(Exception):
    """close_gate in os.config.json cannot be used."""


def read_gate(config: Optional[Dict[str, Any]]) -> Tuple[Optional[Command], int, bool]:
    """Return (verify command or None, timeout seconds, block_repeats)."""
    if not config or config.get("close_gate") is None:
        return None, DEFAULT_TIMEOUT, False
    gate = config["close_gate"]
    if not isinstance(gate, dict):
        raise GateConfigError("close_gate must be an object")
    verify = gate.get("verify")
    if verify is None or verify == "" or verify == []:
        command: Optional[Command] = None
    elif isinstance(verify, str):
        command = verify
    elif isinstance(verify, list) and all(isinstance(part, str) and part for part in verify):
        command = list(verify)
    else:
        raise GateConfigError("close_gate.verify must be a command string or a list of strings")
    timeout = gate.get("timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise GateConfigError("close_gate.timeout_seconds must be a positive integer")
    block_repeats = gate.get("block_repeats", False)
    if not isinstance(block_repeats, bool):
        raise GateConfigError("close_gate.block_repeats must be true or false")
    return command, min(timeout, MAX_TIMEOUT), block_repeats


def run_verify(command: Command, root: str, timeout: int) -> Tuple[Optional[int], str]:
    """Run verify in ``root``. Returns (exit code or None on timeout/start failure, output)."""
    try:
        completed = subprocess.run(
            command,
            shell=isinstance(command, str),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output or b""
        return None, "timed out after %d seconds\n%s" % (timeout, _decode(output))
    except OSError as exc:
        return None, "could not start: %s" % exc
    return completed.returncode, _decode(completed.stdout or b"")


def _decode(data: Union[bytes, str]) -> str:
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def tail(output: str) -> str:
    lines = output.rstrip().splitlines()[-TAIL_LINES:]
    text = "\n".join(lines)
    if len(text) > TAIL_CHARS:
        text = "..." + text[-TAIL_CHARS:]
    return text


def describe(command: Command) -> str:
    return command if isinstance(command, str) else " ".join(command)


def main(
    argv: Optional[Sequence[str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description="Stop hook that blocks finishing while verify fails.")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    args = parser.parse_args(argv)

    try:
        hook_input = os_config.read_hook_input(stdin)
    except os_config.InputError as exc:
        stderr.write("close gate: %s; the gate did not run.\n" % exc)
        return 1
    if hook_input.get("background_tasks"):
        return 0

    root = os_config.project_root(hook_input, args.root)
    try:
        config, _ = os_config.load_config(root)
        command, timeout, block_repeats = read_gate(config)
        brain = os_config.second_brain(config, root)
    except (os_config.ConfigError, GateConfigError) as exc:
        return _fail("close gate: %s." % exc, bool(hook_input.get("stop_hook_active")), False, stdout, stderr)
    checks = []  # type: List[Tuple[str, Command, str]]
    if command is not None:
        checks.append(("verify", command, root))
    if brain is not None and brain["lint_on_close"]:
        # bin/kb is a bash script; running it through bash also works where it isn't executable.
        checks.append(("second brain lint", ["bash", os.path.join(brain["root"], "bin", "kb"), "lint"], brain["root"]))

    for label, check, cwd in checks:
        code, output = run_verify(check, cwd, timeout)
        if code == 0:
            continue
        status = "exited %d" % code if code is not None else output.splitlines()[0]
        reason = "close gate: %s %s: %s" % (label, status, describe(check))
        details = tail(output if code is not None else "\n".join(output.splitlines()[1:]))
        if details:
            reason += "\nLast output:\n" + details
        ledger.record(root, "close_failed", hook_input, detail="%s %s" % (label, status))
        return _fail(reason, bool(hook_input.get("stop_hook_active")), block_repeats, stdout, stderr)
    return 0


def _fail(reason: str, already_retried: bool, block_repeats: bool, stdout: TextIO, stderr: TextIO) -> int:
    if already_retried and not block_repeats:
        first, _, rest = reason.partition("\n")
        stderr.write(first + " (still failing after one retry; stopping)\n")
        if rest:
            stderr.write(rest + "\n")
        return 1
    os_config.emit_json({"decision": "block", "reason": reason}, stdout)
    stderr.write(reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
