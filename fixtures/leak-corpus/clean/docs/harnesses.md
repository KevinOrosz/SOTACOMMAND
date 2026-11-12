# Harnesses

| Harness | Entry file | Skills |
|---|---|---|
| Claude Code | `AGENTS.md` (native since 2.1.277) | plugin marketplace, or `~/.claude/skills` |
| Codex | `AGENTS.md` | `.agents/skills/` |
| GitHub Copilot | `.github/agents/<role>.agent.md` | repository instructions |
| Cursor | coming in v0.2 | `skills/` |
| Gemini CLI | coming in v0.2 | `skills/` |

The marketplace manifest lives at `.claude-plugin/marketplace.json` and every
plugin keeps its manifest in `<plugin>/.claude-plugin/plugin.json`. Plugin files
are referenced as `${CLAUDE_PLUGIN_ROOT}/tools/session_open.py`, never through a
top-level `bin/` folder.

Install the Gemini CLI with `npm install -g @google/gemini-cli` and the Claude
Code CLI with `npm install -g @anthropic-ai/claude-code@2.1.277`.
