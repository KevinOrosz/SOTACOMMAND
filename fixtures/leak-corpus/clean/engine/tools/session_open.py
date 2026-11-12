"""Report memory-file sizes against the caps in os.config.json."""
import json
import os
import signal
import sys


def main() -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    with open(os.path.join(root, "os.config.json"), encoding="utf-8") as handle:
        caps = json.load(handle).get("memory_caps", {})
    for name, cap in sorted(caps.items()):
        size = os.path.getsize(os.path.join(root, "memory", name))
        print("%s: %d/%d bytes" % (name, size, cap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
