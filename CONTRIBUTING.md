# Contributing

Thanks for helping. Cortex stays useful by staying small, so please open an issue before proposing a new skill.

## Setup

```bash
git clone https://github.com/singullar/cortex-sdd
claude --plugin-dir ./cortex-sdd
```

## Workflow

1. Branch from `main` as `feat/<slug>`, `fix/<slug>` or `chore/<slug>`.
2. Make the change, following `.claude/rules/`.
3. Run the checks:

   ```bash
   python3 -m unittest discover -s test
   claude plugin validate .
   ```

4. Open a pull request that explains the problem it solves.

## What a change must keep true

- **Nothing private.** No company or client names, personal paths, internal links or real incident details. `test/test_plugin_integrity.py` scans for common leaks; review your diff anyway.
- **No dead references.** Every `/cortex:*` command, script, reference and template a file mentions must exist. The same test enforces it.
- **Scripts stay stdlib-only** and run on Python 3.9.
- **Vault format changes come in pairs.** `.rules/ingestion.md` in [cortex-brain](https://github.com/singullar/cortex-brain) is a contract with `agents/ingestor.md` and `scripts/cortex_vault_lint.py`.

`claude plugin validate --strict` reports one known warning: a root `CLAUDE.md` is not loaded for plugin users. It is here for contributors, so CI allows exactly that warning.
