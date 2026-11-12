# labs

Experimental packs live here, one Claude Code plugin per directory, laid out exactly like a pack in `packs/`:

```text
labs/<pack>/
├── .claude-plugin/plugin.json
├── README.md
├── skills/<skill>/SKILL.md
└── evals/<skill>/<case>/...
```

A lab pack is listed in the marketplace with `defaultEnabled: false`, so nobody gets it without opting in. Its skills carry `status: experimental` until a qualifying eval run meets the stable rule in [the skills contract](../tools/repo/skills/CONTRACT.md). A pack moves to `packs/` once its skills are stable.

Start a lab pack by copying the layout of [`packs/example`](../packs/example), then scaffold skills into it:

```bash
python3 tools/repo/skills/skills.py new <pack> <skill> --labs
python3 tools/repo/skills/skills.py lint packs labs
```

There are no lab packs yet.
