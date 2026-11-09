#!/usr/bin/env python3
"""Unit tests for scripts/cortex_timebox.py — the review probe's hard wall-clock limit.

Contract (cortex#136 review): the command's exit code is kept; a hang anywhere in its process group is stopped at the
limit with exit 124 and leaves no orphan behind; a command that cannot start exits 127, never 0.

Self-contained on purpose (stdlib unittest only): run with
    python3 test/test_cortex_timebox.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_timebox.py"


def timebox(*argv: str, seconds: str = "1") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), seconds, "--", *argv], capture_output=True, text=True,
                          timeout=30)


class TimeboxTests(unittest.TestCase):
    def test_the_commands_own_exit_code_is_kept(self):
        self.assertEqual(timebox("sh", "-c", "exit 3").returncode, 3)
        self.assertEqual(timebox("sh", "-c", "exit 0").returncode, 0)

    def test_the_output_says_its_pid_and_its_exit(self):
        # the Nexus's static review (S2): a resumed review reconciles the run from its output, never reruns a live one
        lines = timebox("sh", "-c", "exit 3").stderr.splitlines()
        self.assertRegex(lines[0], r"^cortex_timebox: pid \d+$")
        self.assertEqual(lines[-1], "cortex_timebox: exit 3")
        self.assertEqual(timebox("cortex-no-such-runner-xyz").stderr.splitlines()[-1], "cortex_timebox: exit 127")

    def test_a_hang_in_a_child_is_stopped_at_the_limit_with_no_orphan(self):
        # npm test / make test: the runner's child hangs, not the runner itself.
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            started = time.monotonic()
            done = timebox("sh", "-c", f"sleep 30 & echo $! > {pid_file}; wait")
            self.assertEqual(done.returncode, 124)
            self.assertLess(time.monotonic() - started, 15)
            self.assertIn("timed out", done.stderr)
            child = int(pid_file.read_text())
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)

    def test_a_child_that_ignores_term_is_killed_after_the_runner_exits(self):
        # the Nexus's static review (S3): a normal exit sent TERM only, so a TERM-resistant child outlived the wrapper
        with tempfile.TemporaryDirectory() as tmp:
            ready, pid_file = Path(tmp) / "ready", Path(tmp) / "child.pid"
            leader = (f"sh -c 'trap \"\" TERM; : > {ready}; exec sleep 30' & echo $! > {pid_file}; "
                      f"while [ ! -e {ready} ]; do sleep 0.05; done; exit 5")
            started = time.monotonic()
            done = timebox("sh", "-c", leader, seconds="20")  # capture_output: a live child would hold the pipe
            self.assertEqual(done.returncode, 5)
            self.assertLess(time.monotonic() - started, 15)
            child = int(pid_file.read_text())
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)

    def test_a_command_that_cannot_start_is_never_a_pass(self):
        done = timebox("cortex-no-such-runner-xyz")
        self.assertEqual(done.returncode, 127)
        self.assertIn("cannot run", done.stderr)

    def test_usage(self):
        for argv in ([str(SCRIPT), "x", "--", "true"], [str(SCRIPT), "5", "true"], [str(SCRIPT), "0", "--", "true"]):
            with self.subTest(argv=argv):
                self.assertEqual(subprocess.run([sys.executable, *argv], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
