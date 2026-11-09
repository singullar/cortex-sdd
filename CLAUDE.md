# cortex-sdd

A Claude Code plugin (namespace `/cortex:*`) that runs a spec-driven development cycle against a knowledge vault. This file is for people developing the plugin; it is not loaded for plugin users.

The flow is `init → explore → plan → exec (with review) → end → ingest`. Each step reads and writes files in a cycle folder, so the artifacts and receipts, not the conversation, carry the state.

## Map

- `skills/<name>/SKILL.md`: one per command, with `references/` for content loaded on demand.
- `agents/ingestor.md`: writes to the vault. It never runs git.
- `scripts/cortex_*.py`: everything deterministic. Stdlib only, JSON output.
- `hooks/hooks.json`: the compact bridge, which re-injects a resume brief after `/compact`.
- `test/`: `python3 -m unittest discover -s test`.

Rules by topic live in `.claude/rules/`. The vault's format is owned by the separate cortex-brain template.
