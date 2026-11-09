---
name: install
description: Use right after installing the plugin, or to change or repair the brain setup, when the user says "cortex:install", "set up cortex", "create my brain" or "connect my vault". Checks prerequisites, creates a private knowledge vault from the public cortex-brain template (or clones or adopts an existing one), records its path, and validates the result with the doctor.
---

# Skill: cortex:install

Sets up the knowledge vault ("the brain") that `/cortex:explore` reads and `/cortex:ingest` writes. It writes only to the vault location and the plugin's own data directory, and never touches `~/.claude`.

## Steps

1. **Prerequisites.** Run:

   ```bash
   python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_doctor.py" --prereqs-only
   ```

   On any `FAIL`, show its `detail` and `fix` and stop. Show `WARN` lines and continue.

2. **Existing setup.** Read `${CLAUDE_PLUGIN_DATA}/config.json` if it exists. When it has a `vault_path`, ask with `AskUserQuestion`: `Keep this vault` (jump to step 6) or `Change it`.

3. **Choose the vault** with `AskUserQuestion`:
   - `Create a new private vault (Recommended)`
   - `Clone a vault I already have on GitHub`
   - `Use a local folder`

   Then ask for what the choice needs: the GitHub repository name and the local parent folder (default: `~`), the repository to clone, or the absolute path.

4. **Create or fetch it.** Run from the parent folder:

   - New: `gh repo create <name> --template singullar/cortex-brain --private --clone`. Always `--private`, because a vault holds what the user knows about their work; never offer `--public`.
   - Clone: `gh repo clone <owner/repo>`.
   - Local: nothing to run.

   `gh repo create --template` can return before the template's files are available. If the clone has no `index.md`, wait a few seconds, then `git -C <path> pull` once.

5. **Record it:**

   ```bash
   python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_init.py" configure --data-dir "${CLAUDE_PLUGIN_DATA}" --vault "<absolute path>"
   ```

   Exit 2 prints `{"error": ...}`: show it and stop.

6. **Validate.** Run `/cortex:doctor`'s command:

   ```bash
   python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}"
   ```

   Show the result as a short table. When nothing fails, finish with:

   ```text
   Brain ready at <path>.
   Start a cycle: /cortex:init project:<name>
   ```

## Constraints

- Never create a public vault, and never push anything to a vault here.
- A failed step stops the skill with what failed and how to fix it. Do not continue past it.
