#!/usr/bin/env python3
"""Unit tests for scripts/cortex_compact_bridge.py — the hook that carries a
cycle across /compact.

Contract, in order of importance: never crash, never speak outside an SDD
cycle, and never inline a stale handoff as if it were the current turn.

Self-contained on purpose (stdlib unittest only): run with
    python3 test/test_compact_bridge.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_compact_bridge.py"

HANDOFF_BODY = """## Activity Understanding
Wiring the compact bridge.

## Current Status
Bridge script written; docs updated.

## Key Decisions
Plugin-level hook only, never blocking.

## Pending Items
- Wire the unit tests
- Bump the plugin version

## Next Steps to Continue
Run the test file, then open the draft PR.

## Suggested Skills
/cortex:status
"""


class CompactBridgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # --- helpers -------------------------------------------------------------

    def run_script(self, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(self.tmp),
            input=stdin,
        )

    def make_cycle(self, name: str = "260801-1200-bridge", stamp: bool = True) -> Path:
        cycle = self.tmp / name
        (cycle / ".claude").mkdir(parents=True)
        (cycle / "CLAUDE.md").write_text("# cycle\n@SETUP.md\n")
        settings = {"model": "opus", "effortLevel": "high"}
        if stamp:
            settings["cortexCycle"] = name
        (cycle / ".claude" / "settings.json").write_text(json.dumps(settings))
        return cycle

    def write_handoff(self, cycle: Path, age_seconds: float = 0,
                      body: str = HANDOFF_BODY) -> Path:
        path = cycle / "handoff-2026-08-01-1200.md"
        path.write_text(body)
        if age_seconds:
            stamp = path.stat().st_mtime - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    def session_start(self, cwd, extra: dict = None) -> subprocess.CompletedProcess:
        payload = {
            "session_id": "abc123",
            "transcript_path": str(self.tmp / "transcript.jsonl"),
            "cwd": str(cwd),
            "hook_event_name": "SessionStart",
            "source": "compact",
        }
        payload.update(extra or {})
        return self.run_script(stdin=json.dumps(payload))

    def pre_compact(self, cwd, extra: dict = None) -> subprocess.CompletedProcess:
        payload = {
            "session_id": "abc123",
            "transcript_path": str(self.tmp / "transcript.jsonl"),
            "cwd": str(cwd),
            "hook_event_name": "PreCompact",
            "compaction_trigger": "auto",
        }
        payload.update(extra or {})
        return self.run_script(stdin=json.dumps(payload))

    def brief(self, proc: subprocess.CompletedProcess) -> str:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        out = data["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIsInstance(out, str)
        return out

    # --- silence outside a cycle ----------------------------------------------

    def test_non_cycle_cwd_is_a_silent_noop(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        proc = self.session_start(plain)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_claude_md_without_context_stamp_is_not_a_cycle(self):
        cycle = self.make_cycle(stamp=False)
        self.write_handoff(cycle)
        proc = self.session_start(cycle)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_empty_and_malformed_stdin_exit_zero_quietly(self):
        for payload in ("", "not json", "[]", "{}"):
            with self.subTest(payload=payload):
                proc = self.run_script(stdin=payload)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")

    def test_malformed_settings_json_is_tolerated(self):
        cycle = self.make_cycle()
        (cycle / ".claude" / "settings.json").write_text("{not json")
        proc = self.session_start(cycle)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")

    # --- SessionStart brief -----------------------------------------------------

    def test_cycle_without_handoff_still_reports_chain_state(self):
        cycle = self.make_cycle()
        (cycle / "PLAN.md").write_text("# plan\n")
        (cycle / "EXEC.md").write_text("- [x] one\n- [x] two\n- [ ] three\n")
        (cycle / ".cortex-exec-receipt.json").write_text(json.dumps({"status": "partial"}))
        brief = self.brief(self.session_start(cycle))
        self.assertIn("## Cycle chain", brief)
        self.assertIn("PLAN.md", brief)
        self.assertIn("2/3 tasks done", brief)
        self.assertNotIn("(no heading)", brief)  # bare checkboxes never name a synthetic active phase
        self.assertIn("status=partial", brief)
        self.assertIn("None found in this cycle", brief)

    def test_exec_progress_names_the_active_phase(self):
        cycle = self.make_cycle()
        (cycle / "EXEC.md").write_text(
            "# EXEC\n\n## Phase 1 — Build\n- [x] make it\n\n## Phase 2 — Ship\n- [ ] open the PR\n- [ ] merge\n"
        )
        out = self.brief(self.session_start(cycle))
        self.assertIn("- EXEC.md progress: 1/3 tasks done, 2 open · active: Phase 2 — Ship", out)

    def test_fresh_handoff_is_inlined_with_an_age_label(self):
        cycle = self.make_cycle()
        self.write_handoff(cycle)
        brief = self.brief(self.session_start(cycle))
        self.assertIn("FRESH", brief)
        self.assertIn("seconds ago", brief)
        self.assertIn("### Current Status", brief)
        self.assertIn("Bridge script written; docs updated.", brief)
        self.assertIn("### Pending Items", brief)
        self.assertIn("Bump the plugin version", brief)
        self.assertIn("### Next Steps to Continue", brief)
        self.assertIn("open the draft PR", brief)
        # Sections NOT in the inline set must stay out — the budget is for the
        # three that matter.
        self.assertNotIn("### Key Decisions", brief)
        self.assertNotIn("### Suggested Skills", brief)

    def test_stale_handoff_is_pointed_at_never_inlined(self):
        cycle = self.make_cycle()
        path = self.write_handoff(cycle, age_seconds=3 * 24 * 3600)
        brief = self.brief(self.session_start(cycle))
        self.assertIn("STALE", brief)
        self.assertIn("3 days ago", brief)
        self.assertIn(str(path), brief)
        self.assertNotIn("### Current Status", brief)
        self.assertNotIn("Bridge script written; docs updated.", brief)

    def test_receipt_pointer_wins_over_mtime_glob(self):
        cycle = self.make_cycle()
        decoy = self.write_handoff(cycle)
        chosen = cycle / "handoff-2026-07-31-0900.md"
        chosen.write_text(HANDOFF_BODY.replace("docs updated", "receipt wins"))
        # Make the pointed-at file OLDER than the decoy, so only the receipt
        # branch can pick it — a dead receipt branch would fail this test.
        stamp = decoy.stat().st_mtime - 3600
        os.utime(chosen, (stamp, stamp))
        (cycle / ".cortex-handoff-receipt.json").write_text(json.dumps({
            "schemaVersion": 1,
            "handoff": str(chosen),
            "cycle_dir": str(cycle),
        }))
        brief = self.brief(self.session_start(cycle))
        self.assertIn(chosen.name, brief)
        self.assertIn("receipt wins", brief)

    def test_malformed_handoff_receipt_falls_back_to_the_newest_file(self):
        cycle = self.make_cycle()
        path = self.write_handoff(cycle)
        (cycle / ".cortex-handoff-receipt.json").write_text("{broken")
        brief = self.brief(self.session_start(cycle))
        self.assertIn(path.name, brief)
        self.assertIn("### Current Status", brief)

    def test_handoff_without_the_expected_headings_degrades_to_a_pointer(self):
        cycle = self.make_cycle()
        self.write_handoff(cycle, body="# Notes\n\nFreeform, no sections.\n")
        brief = self.brief(self.session_start(cycle))
        self.assertIn("none of the expected sections", brief)
        self.assertNotIn("### Current Status", brief)

    def test_oversized_handoff_stays_within_the_budget(self):
        cycle = self.make_cycle()
        bloat = "\n".join(f"- pending item {i}" for i in range(4000))
        body = HANDOFF_BODY.replace("- Wire the unit tests", bloat)
        self.write_handoff(cycle, body=body)
        brief = self.brief(self.session_start(cycle))
        self.assertLess(len(brief), 6000, "brief blew past the injection budget")
        self.assertIn("truncated", brief)
        self.assertIn("### Next Steps to Continue", brief)  # later sections survive

    # --- PreCompact breadcrumb ---------------------------------------------------

    def test_precompact_writes_a_breadcrumb_and_stays_quiet(self):
        cycle = self.make_cycle()
        proc = self.pre_compact(cycle)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        log = json.loads((cycle / ".cortex-compact-log.json").read_text())
        self.assertEqual(log["schemaVersion"], 1)
        self.assertEqual(len(log["events"]), 1)
        self.assertEqual(log["events"][0]["trigger"], "auto")
        self.assertEqual(log["events"][0]["session_id"], "abc123")
        self.assertTrue(log["events"][0]["transcript_path"].endswith("transcript.jsonl"))

    def test_precompact_appends_and_caps_at_ten_events(self):
        cycle = self.make_cycle()
        for _ in range(13):
            self.pre_compact(cycle)
        log = json.loads((cycle / ".cortex-compact-log.json").read_text())
        self.assertEqual(len(log["events"]), 10)

    def test_precompact_repairs_a_malformed_log(self):
        cycle = self.make_cycle()
        (cycle / ".cortex-compact-log.json").write_text("garbage")
        self.pre_compact(cycle)
        log = json.loads((cycle / ".cortex-compact-log.json").read_text())
        self.assertEqual(len(log["events"]), 1)

    # --- the auto-compact note must not contradict a fresh handoff ----------------

    def test_auto_note_absent_when_a_fresh_handoff_covers_the_compact(self):
        cycle = self.make_cycle()
        self.write_handoff(cycle, age_seconds=30)
        self.pre_compact(cycle)  # breadcrumb is NEWER than the handoff
        brief = self.brief(self.session_start(cycle))
        self.assertIn("FRESH", brief)
        self.assertNotIn("## Auto-compact", brief)

    def test_auto_note_present_when_no_fresh_handoff_covers_the_compact(self):
        cycle = self.make_cycle()
        self.write_handoff(cycle, age_seconds=3 * 24 * 3600)
        self.pre_compact(cycle)
        brief = self.brief(self.session_start(cycle))
        self.assertIn("## Auto-compact", brief)
        self.assertIn("transcript.jsonl", brief)

    def test_unknown_event_name_does_nothing(self):
        cycle = self.make_cycle()
        self.write_handoff(cycle)
        proc = self.session_start(cycle, extra={"hook_event_name": "PostToolUse"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")


class HooksJsonTests(unittest.TestCase):
    """Structural guard: a typo'd hook path or matcher would surface as a
    visible hook error in EVERY user session — fail the build instead."""

    REQUIRED_HOOKS = {"SessionStart": "compact", "PreCompact": "auto"}

    def setUp(self):
        self.data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())

    def test_declares_both_compact_bridge_events(self):
        hooks = self.data["hooks"]
        for event, matcher in self.REQUIRED_HOOKS.items():
            with self.subTest(event=event):
                entries = hooks.get(event)
                self.assertIsInstance(entries, list)
                self.assertTrue(entries, f"{event} must be a non-empty array")
                matchers = [e.get("matcher") for e in entries]
                self.assertIn(matcher, matchers)

    def test_commands_are_plugin_root_relative_and_resolve(self):
        for event, entries in self.data["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    with self.subTest(event=event):
                        self.assertEqual(hook.get("type"), "command")
                        command = hook.get("command", "")
                        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
                        rel = command.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1]
                        rel = rel.split('"')[0]
                        self.assertTrue(
                            (REPO_ROOT / rel).is_file(),
                            f"hook command path does not resolve: {rel}",
                        )


if __name__ == "__main__":
    unittest.main()
