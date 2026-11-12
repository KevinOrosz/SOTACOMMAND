"""Generators that turn harness-neutral role sources into harness outputs.

Modules:

* ``frontmatter``: strict YAML-subset parser for role sources.
* ``roles``: the role source contract (adapters/ROLES.md).
* ``claude``: the Claude Code engine plugin under ``engine/``.
* ``copilot``: GitHub Copilot custom agents under ``.github/agents/``.
* ``marketplace``: the root ``.claude-plugin/marketplace.json``.
* ``codex``: the Codex install (``.agents/skills/`` and ``AGENTS.md``).
* ``outputs``: writing generated files and ``--check`` drift detection.

Standard library only, Python 3.9+. No network or model calls.
"""

# Product names are placeholders until the prefix decision. Change them here
# and regenerate; nothing else hard-codes them.
PLUGIN_NAME = "sotacommand-engine"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = (
    "Executable agent roles: each role is a subagent whose boundaries are enforced by "
    "tool limits, a protected-path hook and a worktree, plus a session ritual run by hooks "
    "and a convergence workflow."
)
PLUGIN_LICENSE = "Apache-2.0"
PLUGIN_KEYWORDS = ("agents", "roles", "hooks", "workflow", "governance")
# Neutral placeholder; the real owner is set when the public repo is cut.
OWNER_NAME = "SOTACOMMAND"

MARKETPLACE_NAME = "sotacommand"
MARKETPLACE_DESCRIPTION = (
    "The SOTACOMMAND engine plugin and its skill packs. The engine and packs whose skills are "
    "all stable start enabled; other packs and everything in labs/ are listed but start disabled."
)

ENGINE_DIR = "engine"
