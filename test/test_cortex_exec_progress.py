#!/usr/bin/env python3
"""Unit tests for scripts/cortex_exec_progress.py — the one EXEC.md progress parser/renderer.

Contract: counts come from the checkboxes (never narrated), the section grammar
of skills/plan/SKILL.md's template is recognised in all its spellings, the
historical `done/total/active_phase` keys stay stable for /cortex:brief and the
compact bridge, the table collapses every phase after the active one into a
`Phase N+` row unless --full, and the script always exits 0.

Self-contained on purpose (stdlib unittest only): run with
    python3 test/test_cortex_exec_progress.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_exec_progress.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cortex_exec_progress as prog  # noqa: E402

EXEC = """# EXEC — widget

## Execution model

- Inline subagents only.

## Cross-cutting > Setup
- [x] Cut a worktree _(inline)_
  - **Gate:** worktree present
- [x] Pin the apply locus _(inline)_

## Phase 1 — Modules (wave)
> **Mates:** `a` (sonnet), `b` (sonnet) _(parallelizable)_
- [x] module a _(owner: `a`)_
  - **Gate:** validate clean
- [x] module b _(owner: `b`)_
- [x] module c _(owner: `b`)_

## Phase 2 — Units, plan-only
- [x] unit one _(owner: `u`)_
- [ ] unit two _(owner: `u`)_
- [ ] amendment _(owner: `u`)_

## Phase 3 — Apply
- [ ] apply cluster _(inline)_
- [ ] apply lb _(inline)_

## Phase 4 — Ship
- [ ] docs _(owner: `d`)_

## Cross-cutting > Wrap up
- [ ] Confirm docs _(inline)_
- [ ] Ship & autonomous review _(inline)_

## Cross-cutting > Deferred (file, do not execute)
- [ ] File the client's broken migrate-stage issue _(inline)_
"""


class ExecProgressTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cycle = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # --- helpers -------------------------------------------------------------

    def write(self, text: str = EXEC) -> None:
        (self.cycle / "EXEC.md").write_text(text, encoding="utf-8")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.cycle), *args], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return proc

    # --- parsing -------------------------------------------------------------

    def test_counts_every_checkbox_and_keeps_the_historical_keys(self):
        self.write()
        out = prog.read_progress(self.cycle)
        self.assertEqual((out["done"], out["total"]), (6, 14))
        self.assertEqual(out["active_phase"], "Phase 2 — Units, plan-only")
        self.assertEqual(out["phase_count"], 4)

    def test_sections_carry_kind_short_label_and_state(self):
        self.write()
        rows = {s["short"]: s for s in prog.read_progress(self.cycle)["phases"]}
        self.assertEqual(rows["Setup"]["kind"], "setup")
        self.assertEqual(rows["Setup"]["state"], "done")
        self.assertEqual(rows["Phase 1"]["name"], "Modules (wave)")
        self.assertEqual(rows["Phase 1"]["n"], 1)
        self.assertEqual(rows["Phase 2"]["state"], "active")
        self.assertEqual(rows["Phase 3"]["state"], "pending")
        self.assertEqual(rows["Wrap up"]["kind"], "wrapup")
        self.assertEqual(rows["Deferred"]["kind"], "deferred")
        self.assertNotIn("Execution model", rows)  # no checkboxes → omitted

    def test_heading_spellings_are_tolerated(self):
        for heading in ("## Phase 3 — Apply", "## Phase 3 - Apply", "## Phase 3 – Apply (wave)", "## Phase 3"):
            kind, short, n, _ = prog.classify(heading[3:])
            self.assertEqual((kind, short, n), ("phase", "Phase 3", 3), heading)
        self.assertEqual(prog.classify("Cross-cutting > Wrap up")[:2], ("wrapup", "Wrap up"))
        self.assertEqual(prog.classify("Cross-cutting > Team setup")[0], "other")
        self.assertEqual(prog.classify("Removed by replan (was completed)")[0], "other")

    def test_missing_exec_is_empty_not_an_error(self):
        out = prog.read_progress(self.cycle)
        self.assertEqual(out, {"done": 0, "total": 0, "active_phase": None, "phases": [], "phase_count": 0})
        self.assertIn("no tasks found", self.run_script("--table").stdout)

    def test_all_done_has_no_active_phase(self):
        self.write(EXEC.replace("[ ]", "[x]"))
        out = prog.read_progress(self.cycle)
        self.assertIsNone(out["active_phase"])
        self.assertTrue(all(s["state"] == "done" for s in out["phases"]))

    # --- rendering -----------------------------------------------------------

    def test_table_collapses_phases_after_the_active_one(self):
        self.write()
        lines = self.run_script("--table").stdout.splitlines()
        self.assertEqual(lines[0], "Progress: 6/14 tasks")
        labels = [line.split()[0] + (" " + line.split()[1] if line.split()[0] == "Phase" else "") for line in lines[1:]]
        self.assertEqual(labels, ["Setup", "Phase 1", "Phase 2", "Phase 3+", "Deferred"])
        collapsed = next(line for line in lines if "Phase 3+" in line)
        self.assertIn("0/5", collapsed)          # Phase 3 + Phase 4 + Wrap up
        self.assertIn("······", collapsed)       # fixed 6-cell dotted bar
        self.assertIn("(deferred, not executed)", lines[-1])

    def test_full_expands_every_phase(self):
        self.write()
        out = self.run_script("--table", "--full").stdout
        for label in ("Phase 3 ", "Phase 4 ", "Wrap up"):
            self.assertIn(label, out)
        self.assertNotIn("Phase 3+", out)

    def test_bar_is_one_cell_per_task_and_caps(self):
        self.assertEqual(prog.bar(2, 2), "██")
        self.assertEqual(prog.bar(1, 5), "█····")
        self.assertEqual(prog.bar(0, 3), "···")
        self.assertEqual(len(prog.bar(3, 20)), prog.BAR_CAP)
        self.assertEqual(prog.bar(20, 20), "█" * prog.BAR_CAP)
        self.assertEqual(prog.bar(0, 17, 6), "······")
        self.assertEqual(prog.bar(1, 17, 6), "█·····")   # any progress shows at least one cell
        self.assertEqual(prog.bar(16, 17, 6), "█████·")  # never looks complete before it is
        self.assertEqual(prog.bar(1, 12), "█·······")

    def test_checkboxes_above_the_first_heading_never_become_the_active_phase(self):
        self.write("- [x] one\n- [ ] two\n\n## Phase 1 — Build\n- [ ] three\n")
        out = prog.read_progress(self.cycle)
        self.assertEqual((out["done"], out["total"]), (1, 3))
        self.assertIsNone(out["active_phase"])
        self.assertEqual(out["phases"][0]["state"], "active")
        self.assertEqual(out["phases"][1]["state"], "pending")

    def test_ledes_apply_to_closed_and_active_rows_only_and_truncate(self):
        self.write()
        ledes = json.dumps({
            "Phase 1": "three modules, every gate re-run by the lead",
            "Phase 2 — Units, plan-only": "← " + "x" * 80,
            "Phase 3": "must not appear",
        })
        out = self.run_script("--table", "--ledes", ledes).stdout
        self.assertIn("3/3   three modules, every gate re-run by the lead", out)
        phase2 = next(line for line in out.splitlines() if line.strip().startswith("Phase 2"))
        self.assertTrue(phase2.rstrip().endswith("…"))
        self.assertLessEqual(len(phase2.split("1/3", 1)[1].strip()), prog.LEDE_CHARS)
        self.assertNotIn("must not appear", out)

    def test_malformed_ledes_are_ignored(self):
        self.write()
        out = self.run_script("--table", "--ledes", "{not json").stdout
        self.assertIn("Progress: 6/14 tasks", out)

    def test_json_mode_is_the_default(self):
        self.write()
        data = json.loads(self.run_script().stdout)
        self.assertEqual(data["done"], 6)
        self.assertEqual(data["phases"][0]["short"], "Setup")


if __name__ == "__main__":
    unittest.main()
