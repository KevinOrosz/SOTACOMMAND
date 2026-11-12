"""Library behind ``tools/repo/skills/skills.py``.

Modules:

* ``yamlsubset``: the strict YAML-subset parser used for every frontmatter
  block and ``case.yaml`` file.
* ``model``: discovery and loading of plugins, skills, eval cases and
  redacted result summaries.
* ``summary``: the redacted ``summary.json`` format, its validator and the
  per-skill evidence the registry and the stable rule read from it.
* ``lint``: the rules behind ``skills.py lint``.
* ``registry``: ``REGISTRY.md`` generation and its drift check.
* ``scaffold``: ``skills.py new``.
* ``redact``: ``skills.py redact-results``.

Everything here is standard-library Python 3.9+, makes no network calls and
no model calls.
"""
