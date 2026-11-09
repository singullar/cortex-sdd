---
name: init
description: Use at the start of a spec-driven work cycle, when the user says "cortex:init", "start a new cycle", "init this folder" or "bootstrap a cycle". Creates a cycle folder (or stamps the current non-repo folder) with a CLAUDE.md block, the cycle and vault stamps in .claude/settings.json, and SETUP.md. Accepts project:<name> repos:<path,path> ai-brain:<path> model:<id> effort:<level>. Trigger only on explicit init intent.
---

# Skill: cortex:init

Lays down a cycle: the folder where a unit of work's artifacts live.

## Input

Optional `key:value` arguments:

- `project:<name>`: creates `<cycles_root>/<YYMMDD-HHMM-name>`. `cycles_root` comes from `/cortex:install` and defaults to `~/cortex-cycles`. Without it, the current folder is stamped.
- `repos:<path,path>`: source repositories the session may read and edit, as absolute paths or paths relative to the current folder.
- `ai-brain:<path>`: use this vault instead of the configured one.
- `model:<id>`, `effort:<level>`: pin them in the cycle's settings. Omit to keep your defaults.

## Steps

1. If `project:` was not given and the current folder is inside a git repository, ask for a project name with `AskUserQuestion`. A cycle never lives inside a source repository.

2. Run one command, passing only the flags you have:

   ```bash
   python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_init.py" init --data-dir "${CLAUDE_PLUGIN_DATA}" --cwd "$(pwd)" --project "<name>" --repos "<csv>" --vault "<path>" --model "<id>" --effort "<level>"
   ```

   Exit 2 prints `{"error": ...}`: show it and stop.

3. Read `<target_dir>/CLAUDE.md` so the block enters this turn's context.

4. Report `target_dir` and any `warnings`. When `created_cycle_folder` is true, this session's working directory is not the cycle, so tell the user to continue there:

   ```text
   cd <target_dir> && claude
   ```

   Then the next step is `/cortex:explore`.

## Constraints

- Never edit content outside the `cortex-init:begin` / `cortex-init:end` markers in `CLAUDE.md`. The script enforces this.
- Never overwrite unrelated keys in `settings.json`, and never remove entries from `permissions.additionalDirectories`.
- An existing `SETUP.md` is never overwritten.
