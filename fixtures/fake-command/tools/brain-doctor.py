#!/usr/bin/env python3
"""Structural health check for the knowledge base. Exit code = FAIL count."""

import os
import sys

KB_ROOT = "/Users/avery/hq/KB"
REQUIRED = ("README.md",)


def main():
    missing = [name for name in REQUIRED if not os.path.exists(os.path.join(KB_ROOT, name))]
    for name in missing:
        print("FAIL missing %s" % name)
    return len(missing)


if __name__ == "__main__":
    sys.exit(main())
