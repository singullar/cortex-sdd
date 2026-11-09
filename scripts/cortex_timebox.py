#!/usr/bin/env python3
"""cortex_timebox.py — run a test suite under a hard wall-clock limit, its whole process group included.

The review's test-run probe runs in the background, beside the reviewer fan-out, so no tool timeout bounds it, and
macOS has no `timeout`. An `alarm` wrapper kills only the process it execs: `npm test` or `make test` leave the hung
test behind as an orphan, and Go ignores SIGALRM. A missing runner must never read as a pass.

Stdlib only.

    cortex_timebox.py <seconds> -- <command> [args...]

Exit: the command's own code (128 + n when a signal killed it) · 124 on timeout · 127 when the command cannot start ·
2 on usage. However the command ends, the group it leaves is stopped: TERM, then KILL after a grace to whatever is left.

Its stderr says `cortex_timebox: pid <n>` once the command starts and `cortex_timebox: exit <code>` when it is done, so a
resumed review tells a run still going (that pid alive) from one that finished or died, from the output file alone.
"""
import os
import signal
import subprocess
import sys
import time

TIMEOUT_EXIT = 124
NOT_RUN_EXIT = 127
GRACE_SECONDS = 5


def _kill_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(proc: subprocess.Popen) -> bool:
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_group(proc: subprocess.Popen) -> None:
    """TERM to the group, then KILL to whatever is left after the grace: a member that ignores TERM would keep the
    worktree's files, and the caller's output pipe, open."""
    _kill_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None and not _group_alive(proc):
            return
        time.sleep(0.1)
    _kill_group(proc, signal.SIGKILL)
    proc.wait()


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: cortex_timebox.py <seconds> -- <command> [args...]", file=sys.stderr)
        return 2
    try:
        seconds = float(argv[0])
    except ValueError:
        seconds = -1
    if seconds <= 0:
        print("cortex_timebox: <seconds> must be a positive number", file=sys.stderr)
        return 2
    code = _run(argv[2:], seconds)
    print(f"cortex_timebox: exit {code}", file=sys.stderr, flush=True)
    return code


def _run(command: list[str], seconds: float) -> int:
    try:
        proc = subprocess.Popen(command, start_new_session=True)  # its own group: a kill reaches every child
    except OSError as exc:
        print(f"cortex_timebox: cannot run {command[0]}: {exc}", file=sys.stderr)
        return NOT_RUN_EXIT
    print(f"cortex_timebox: pid {os.getpid()}", file=sys.stderr, flush=True)
    # The group left the caller's: a stop of the caller reaches it only through here.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: _kill_group(proc, signum))
    try:
        code = proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        _stop_group(proc)
        print(f"cortex_timebox: timed out after {seconds:g} s: {' '.join(command)}", file=sys.stderr)
        return TIMEOUT_EXIT
    _stop_group(proc)  # what the suite left behind
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
