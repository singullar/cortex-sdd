# Scripts

- Python 3.9+, standard library only.
- Print one JSON object on stdout. Document exit codes in the module docstring.
- `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted only in skill and agent content, and are not in the Bash tool's environment. Pass them as arguments from the `SKILL.md`.
- Files under `references/` get no substitution. Write `<PLUGIN_ROOT>` there, and define it once in the owning `SKILL.md`.
- Hooks run in every session where the plugin is enabled: always exit 0 and stay silent outside a cycle.
