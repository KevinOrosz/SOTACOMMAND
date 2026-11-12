#!/usr/bin/env python3
"""Route an intent to an agent. Prints an explainable plan; never executes it."""

import os
import sys

# Maintainer: Avery Example
SAGE_HOME = os.path.expanduser("~/hq")

ROUTES = {
    "research": "RUNE",
    "build": "FLUX",
    "audit": "BEACON",
    "audience": "HERALD",
    "cascade": "EMBER",
}


def route(intent):
    for keyword, agent in ROUTES.items():
        if keyword in intent.lower():
            return agent
    return "SAGE"


def main(argv):
    if len(argv) < 2:
        print("usage: command-router.py INTENT", file=sys.stderr)
        return 2
    agent = route(" ".join(argv[1:]))
    print("route: %s (home %s)" % (agent, SAGE_HOME))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
