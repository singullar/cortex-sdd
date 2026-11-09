---
name: doctor
description: Use to check that cortex is installed and working, when the user says "cortex:doctor", "check my setup", "validate the installation" or "why isn't cortex working". Read-only. Reports each check as pass, warn or fail with how to fix it.
---

# Skill: cortex:doctor

Read-only health check. It changes nothing.

## Steps

1. Run:

   ```bash
   python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}"
   ```

   Exit 1 means at least one check failed. That is a result to report, not an error.

2. Add one check the script cannot see: whether the `advisor` tool is in your tool list. If it is, `advisor: PASS`. If not, `advisor: WARN`, plans are not independently reviewed; this is optional.

3. Print one table with `Check | Status | Detail`, failures first, then the `fix` for every `FAIL` and `WARN`. End with one line: `Ready.` when nothing failed, otherwise `Not ready: fix the items above, then run /cortex:doctor again.`

## Constraints

- Never fix anything from this skill. Point to `/cortex:install` or the printed fix.
