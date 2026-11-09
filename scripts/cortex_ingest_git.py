#!/usr/bin/env python3
"""
cortex_ingest_git.py — the vault git lifecycle behind /cortex:ingest.

The ingestor agent is a pure CONTENT agent: it writes wiki files and never
touches git. The lead owns the whole git lifecycle of the ai-brain vault,
and this script is where that lifecycle lives so it is deterministic and
testable (real temp repos in test/test_cortex_ingest_git.py):

    resolve   → where is the vault? (explicit --vault → cycle stamp → cwd stamp
                → CLAUDE.md line → plugin config). Pure, no side effects.
    prepare   → preflight the vault's MAIN checkout under the lock: baseline-
                commit anything dirty (never asks), fetch + fast-forward the
                checked-out branch, create a per-run worktree on
                `ingest/<YYMMDD-HHMM>-<cycle-slug>` under `<vault>/.worktrees/`.
    land      → commit what the ingestor wrote in the worktree (message with
                Cortex-* trailers pointing at the originating cycle), merge it
                into the checked-out branch under the lock (index.md/log.md
                auto-resolve by union; other conflicts are handed to the lead),
                push when a remote exists, clean up worktree + branch.
    continue  → after the lead resolved the conflicts `land` handed over:
                stage, commit the merge, push, clean up.
    abort     → the lead could not resolve: `git merge --abort`, release the
                lock, KEEP the ingest branch + worktree (the work is safe),
                print the resume command.

Doctrine (mirrors skills/ingest/SKILL.md):
  - The vault path comes from an explicit `--vault`, the cycle's own
    `.claude/settings.json` stamp (`cortexAiBrainPath`), the cwd's stamp, the
    rendered CLAUDE.md "ai-brain vault for this project" line, or the plugin
    config written by /cortex:install (`--data-dir`).
  - The checked-out branch of the vault IS the target branch (`main`,
    `master`, whatever) — never hardcoded, never switched.
  - Never `--force` push, never `-X theirs`. Union merge only for the two
    append-only files (`index.md`, `log.md`) via `.git/info/attributes`
    (local-only — the vault repo's tracked files are never edited by this
    script outside commits of the ingestor's own output).
  - Parallel ingests serialize on a mkdir-lock in the
    git common dir; a worktree per run keeps the write phase contention-free.
  - Anomalies pause instead of guessing: detached HEAD, a foreign merge in
    progress, a lock that never frees → exit 3 with the reason.

Commit-message grammar (vault `.rules/git.md`: `:type: desc`, English):
    :add: ingest <cycle-slug>                                   (in the worktree)
    :chore: land ingest <cycle-slug>                            (merge commit, non-ff)
    :chore: reconcile <target> before landing ingest <cycle-slug>
    :chore: baseline pending vault changes                      (no cycle to attribute)
  followed by a body and the trailers `Cortex-Cycle` and `Cortex-Cycle-Path` —
  `git log --format='%(trailers:key=Cortex-Cycle)'` is the tracking query.

Exit codes:
  0 = ok (one JSON object on stdout; `mode: legacy` from prepare is also 0)
  2 = usage / validation error (bad argv, `..` in paths, unknown worktree)
  3 = git anomaly → needs human (detached HEAD, foreign MERGE_HEAD / rebase,
      lock timeout, unexpected git failure). Nothing half-done is left
      behind except what the JSON says.
  4 = conflicts pending lead resolution: the merge is left IN PROGRESS in
      the main checkout and the lock is HELD (stage `lead-resolving`) until
      `continue` or `abort`.
  5 = land: nothing was written anywhere (neither worktree nor main
      checkout changed) — nothing touched, worktree kept.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UNION_PATHS = ("index.md", "log.md")
WORKTREES_DIR = ".worktrees"
LOCK_NAME = "cortex-ingest.lock"
STALE_MINUTES = {"prepare": 15, "landing": 15, "lead-resolving": 45}
CLAUDE_MD_VAULT_RE = re.compile(r"ai-brain vault for this project:\s*`([^`]+)`")
CYCLE_STAMP_RE = re.compile(r"^\d{6}-\d{4}-")
CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


class GitError(RuntimeError):
    def __init__(self, argv: list[str], cwd: str, output: str):
        super().__init__(f"git {' '.join(argv)} (cwd {cwd}) failed:\n{output.strip()}")
        self.argv, self.cwd, self.output = argv, cwd, output


# --------------------------------------------------------------------------- io


def emit(obj: dict, code: int = 0) -> "Never":  # type: ignore[valid-type]
    print(json.dumps(obj, indent=2, sort_keys=True))
    sys.exit(code)


def die(msg: str, code: int, **extra) -> "Never":  # type: ignore[valid-type]
    print(f"cortex-ingest-git: {msg}", file=sys.stderr)
    emit({"status": "error", "error": msg, **extra}, code)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -------------------------------------------------------------------------- git


def git(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    argv = ["git", *args]
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(list(args), cwd, (proc.stdout or "") + (proc.stderr or ""))
    return proc


def git_out(cwd: str, *args: str) -> str:
    return git(cwd, *args).stdout.strip()


def is_git_toplevel(path: str) -> bool:
    proc = git(path, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        return False
    return os.path.realpath(proc.stdout.strip()) == os.path.realpath(path)


def current_branch(vault: str) -> str:
    """Checked-out branch, or '' when HEAD is detached."""
    proc = git(vault, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def remote_of(vault: str) -> str:
    remotes = [r for r in git_out(vault, "remote").splitlines() if r.strip()]
    if not remotes:
        return ""
    return "origin" if "origin" in remotes else remotes[0]


def git_common_dir(vault: str) -> str:
    common = git_out(vault, "rev-parse", "--git-common-dir")
    return os.path.realpath(os.path.join(vault, common))


def git_dir(path: str) -> str:
    d = git_out(path, "rev-parse", "--git-dir")
    return os.path.realpath(os.path.join(path, d))


def dirty_paths(repo: str) -> list[str]:
    """Porcelain status minus anything under .worktrees/ (never vault content)."""
    out = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith(WORKTREES_DIR + "/") or path == WORKTREES_DIR:
            continue
        paths.append(path)
    return paths


def rev_parse(repo: str, ref: str) -> str:
    proc = git(repo, "rev-parse", "--verify", "-q", ref, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def is_ancestor(repo: str, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def merge_in_progress(repo: str) -> str:
    """Name of the foreign operation in progress ('' when clean)."""
    gd = git_dir(repo)
    for marker, name in (
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    ):
        if os.path.exists(os.path.join(gd, marker)):
            return name
    return ""


def unmerged_paths(repo: str) -> list[str]:
    out = git(repo, "diff", "--name-only", "--diff-filter=U").stdout
    return [p for p in out.splitlines() if p.strip()]


def registered_worktrees(vault: str) -> list[str]:
    out = git(vault, "worktree", "list", "--porcelain").stdout
    return [os.path.realpath(l[len("worktree "):]) for l in out.splitlines() if l.startswith("worktree ")]


def branch_exists(vault: str, branch: str) -> bool:
    return bool(rev_parse(vault, f"refs/heads/{branch}"))


# ---------------------------------------------------------------- cycle stamp


def read_stamp(target_dir: str) -> dict:
    path = os.path.join(target_dir, ".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cycle_slug(target_dir: str) -> str:
    base = os.path.basename(os.path.normpath(target_dir))
    base = CYCLE_STAMP_RE.sub("", base).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40].strip("-")
    return slug or "cycle"


def trailers_for(target_dir: str) -> list[str]:
    return [
        f"Cortex-Cycle: {cycle_slug(target_dir)}",
        f"Cortex-Cycle-Path: {os.path.realpath(target_dir)}",
    ]


def message(subject: str, body: str, target_dir: str | None) -> str:
    parts = [subject, "", body]
    if target_dir:
        parts += ["", *trailers_for(target_dir)]
    return "\n".join(parts) + "\n"


def commit_all(repo: str, msg: str) -> str:
    git(repo, "add", "-A")
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return ""
    git(repo, "commit", "-q", "-m", msg)
    return git_out(repo, "rev-parse", "HEAD")


def baseline_message() -> str:
    return (
        ":chore: baseline pending vault changes\n\n"
        "Committed automatically by /cortex:ingest before touching the vault\n"
        "branch — pending changes from earlier (uncommitted) ingests.\n\n"
        "Cortex-Ingest: baseline\n"
    )


# -------------------------------------------------------------------- resolve


def config_vault(data_dir: str) -> str:
    """The vault recorded by /cortex:install, or '' when there is none."""
    if not data_dir:
        return ""
    try:
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8") as fh:
            value = json.load(fh).get("vault_path")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def resolve_vault(target_dir: str, cwd: str, explicit: str, data_dir: str = "") -> tuple[str, str]:
    """Returns (abs path or '', source)."""
    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append((explicit, "arg"))
    for d, src in ((target_dir, "target-settings"), (cwd, "cwd-settings")):
        v = read_stamp(d).get("cortexAiBrainPath")
        if isinstance(v, str) and v.strip():
            candidates.append((v, src))
    try:
        with open(os.path.join(cwd, "CLAUDE.md"), encoding="utf-8") as fh:
            m = CLAUDE_MD_VAULT_RE.search(fh.read())
        if m:
            candidates.append((m.group(1), "claude-md"))
    except OSError:
        pass
    configured = config_vault(data_dir)
    if configured.strip():
        candidates.append((configured, "config"))
    for raw, src in candidates:
        path = os.path.realpath(os.path.expanduser(raw.strip()))
        if os.path.isdir(path):
            return path, src
    return "", "none"


def resolve_report(target_dir: str, cwd: str, explicit: str, data_dir: str = "") -> dict:
    vault, source = resolve_vault(target_dir, cwd, explicit, data_dir)
    rep = {"vault": vault, "source": source, "is_git": False, "git_reason": "",
           "target_branch": "", "remote": ""}
    if not vault:
        rep["git_reason"] = "vault path unresolved"
        return rep
    if shutil.which("git") is None:
        rep["git_reason"] = "git binary not found"
        return rep
    if not is_git_toplevel(vault):
        rep["git_reason"] = "vault is not the top level of a git repository"
        return rep
    rep["is_git"] = True
    rep["target_branch"] = current_branch(vault)
    rep["remote"] = remote_of(vault)
    return rep


# ----------------------------------------------------------------------- lock


class Lock:
    def __init__(self, vault: str, stage: str, branch: str, timeout: float):
        self.path = os.path.join(git_common_dir(vault), LOCK_NAME)
        self.stage, self.branch, self.timeout = stage, branch, timeout
        self.held = False

    def owner(self) -> dict:
        try:
            with open(os.path.join(self.path, "owner.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _stale(self, owner: dict) -> bool:
        stage = owner.get("stage", "landing")
        limit = STALE_MINUTES.get(stage, 15)
        try:
            created = datetime.fromisoformat(owner["created_at"].replace("Z", "+00:00"))
            aged = now_utc() - created > timedelta(minutes=limit)
        except (KeyError, ValueError, TypeError):
            # No readable owner → fall back on the directory mtime.
            try:
                aged = time.time() - os.path.getmtime(self.path) > limit * 60
            except OSError:
                return False
        if aged:
            return True
        if stage == "lead-resolving":
            return False  # a human/lead may legitimately hold this for a while
        pid, host = owner.get("pid"), owner.get("host")
        if isinstance(pid, int) and host == socket.gethostname():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        return False

    def acquire(self) -> None:
        poll = float(os.environ.get("CORTEX_INGEST_LOCK_POLL", "2"))
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.mkdir(self.path)
                self.held = True
                break
            except FileExistsError:
                owner = self.owner()
                if self._stale(owner):
                    print(f"⚠ breaking stale ingest lock held by {owner or 'unknown'}", file=sys.stderr)
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                if time.monotonic() >= deadline:
                    die("vault merge lock busy — another ingest is landing", 3,
                        status="lock-timeout", lock=self.path, lock_owner=owner)
                time.sleep(poll)
        self.write_owner(self.stage)

    def write_owner(self, stage: str) -> None:
        self.stage = stage
        with open(os.path.join(self.path, "owner.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "stage": stage,
                       "branch": self.branch, "created_at": iso(now_utc())}, fh)

    def release(self) -> None:
        if self.held:
            shutil.rmtree(self.path, ignore_errors=True)
            self.held = False


def release_lock_path(vault: str) -> None:
    shutil.rmtree(os.path.join(git_common_dir(vault), LOCK_NAME), ignore_errors=True)


def lock_stage(vault: str) -> str:
    path = os.path.join(git_common_dir(vault), LOCK_NAME, "owner.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("stage", "")
    except (OSError, ValueError):
        return ""


# ------------------------------------------------------------------ hygiene


def ensure_local_config(vault: str) -> None:
    """Exclude .worktrees/ and union-merge index/log — via .git/info only."""
    info = os.path.join(git_common_dir(vault), "info")
    os.makedirs(info, exist_ok=True)
    # Probe a path INSIDE the dir: a dir-only `.worktrees/` pattern does not
    # match a bare `.worktrees` probe while the directory does not exist.
    ignored = git(vault, "check-ignore", "-q", f"{WORKTREES_DIR}/probe", check=False).returncode == 0
    if not ignored:
        with open(os.path.join(info, "exclude"), "a", encoding="utf-8") as fh:
            fh.write(f"{WORKTREES_DIR}/\n")
    attrs_path = os.path.join(info, "attributes")
    try:
        with open(attrs_path, encoding="utf-8") as fh:
            existing = fh.read()
    except OSError:
        existing = ""
    missing = [f"{p} merge=union" for p in UNION_PATHS if f"{p} merge=union" not in existing]
    if missing:
        with open(attrs_path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(missing) + "\n")


def anomaly_gate(vault: str) -> None:
    if not current_branch(vault):
        die("vault HEAD is detached — check out the target branch first", 3,
            status="detached-head", vault=vault)
    op = merge_in_progress(vault)
    if op:
        die(f"a {op} is already in progress in the vault main checkout — finish or abort it first", 3,
            status="foreign-operation", operation=op, vault=vault)


def fetch_and_ff(vault: str, remote: str, target: str, warnings: list[str]) -> dict:
    res = {"fetched": False, "fast_forwarded": False, "diverged": False}
    if not remote:
        warnings.append("no-remote: fetch skipped")
        return res
    proc = git(vault, "fetch", remote, target, check=False)
    if proc.returncode != 0:
        warnings.append(f"fetch-failed: {(proc.stderr or proc.stdout).strip()}")
        return res
    res["fetched"] = True
    remote_ref = rev_parse(vault, f"refs/remotes/{remote}/{target}")
    if not remote_ref:
        return res
    local = rev_parse(vault, f"refs/heads/{target}")
    if local == remote_ref or is_ancestor(vault, remote_ref, local):
        return res
    if is_ancestor(vault, local, remote_ref):
        git(vault, "merge", "--ff-only", remote_ref)
        res["fast_forwarded"] = True
        return res
    res["diverged"] = True
    return res


# --------------------------------------------------------------------- merge


def union_resolve(vault: str, path: str) -> bool:
    """Resolve one conflicted path by line-union of both sides. True on success."""
    stages = {}
    for n in (1, 2, 3):
        proc = git(vault, "show", f":{n}:{path}", check=False)
        stages[n] = proc.stdout if proc.returncode == 0 else None
    if stages[2] is None or stages[3] is None:
        return False  # delete/modify — not union-able
    tmpdir = os.path.join(git_common_dir(vault), "cortex-ingest-tmp")
    os.makedirs(tmpdir, exist_ok=True)
    files = {}
    for n in (1, 2, 3):
        p = os.path.join(tmpdir, f"stage{n}")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(stages[n] or "")
        files[n] = p
    proc = subprocess.run(["git", "merge-file", "-p", "--union", files[2], files[1], files[3]],
                          cwd=vault, capture_output=True, text=True)
    shutil.rmtree(tmpdir, ignore_errors=True)
    if proc.returncode < 0:
        return False
    with open(os.path.join(vault, path), "w", encoding="utf-8") as fh:
        fh.write(proc.stdout)
    git(vault, "add", "--", path)
    return True


def merge_ref(vault: str, ref: str, msg: str) -> dict:
    """Merge `ref` into the checked-out branch. Returns
    {ok, fast_forward, conflicts_auto_resolved, conflicts}. On leftover
    conflicts the merge is left in progress for the caller to hand over."""
    before = rev_parse(vault, "HEAD")
    target_sha = rev_parse(vault, ref)
    proc = git(vault, "merge", "--no-edit", "-m", msg, ref, check=False)
    result = {"ok": True, "fast_forward": False, "conflicts_auto_resolved": [], "conflicts": []}
    if proc.returncode == 0:
        result["fast_forward"] = rev_parse(vault, "HEAD") == target_sha and before != target_sha
        return result
    conflicts = unmerged_paths(vault)
    if not conflicts:
        raise GitError(["merge", ref], vault, (proc.stdout or "") + (proc.stderr or ""))
    leftover = []
    for path in conflicts:
        if path in UNION_PATHS and union_resolve(vault, path):
            result["conflicts_auto_resolved"].append(path)
        else:
            leftover.append(path)
    if leftover:
        result["ok"] = False
        result["conflicts"] = leftover
        return result
    git(vault, "commit", "-q", "--no-edit")
    return result


def commit_pending_merge(vault: str) -> None:
    git(vault, "commit", "-q", "--no-edit")


# ------------------------------------------------------------------ commands


def cmd_resolve(a) -> None:
    emit(resolve_report(a.target_dir, a.cwd, a.vault or "", a.data_dir))


def cmd_prepare(a) -> None:
    rep = resolve_report(a.target_dir, a.cwd, a.vault or "", a.data_dir)
    if not rep["is_git"]:
        emit({"mode": "legacy", "reason": rep["git_reason"], "vault": rep["vault"],
              "vault_source": rep["source"], "warnings": []})
    vault, target, remote = rep["vault"], rep["target_branch"], rep["remote"]
    anomaly_gate(vault)
    warnings: list[str] = []
    slug = cycle_slug(a.target_dir)
    lock = Lock(vault, "prepare", "", a.lock_timeout)
    lock.acquire()
    try:
        git(vault, "worktree", "prune")
        ensure_local_config(vault)
        baseline = commit_all(vault, baseline_message()) if dirty_paths(vault) else ""
        sync = fetch_and_ff(vault, remote, target, warnings)
        if sync["diverged"]:
            warnings.append(f"diverged: local {target} and {remote}/{target} have both moved — reconciled at land")
        stamp = datetime.now().strftime("%y%m%d-%H%M")
        base_name = f"{stamp}-{slug}"
        name, n = base_name, 1
        while branch_exists(vault, f"ingest/{name}") or os.path.exists(os.path.join(vault, WORKTREES_DIR, f"ingest-{name}")):
            n += 1
            name = f"{base_name}-{n}"
        branch = f"ingest/{name}"
        wt = os.path.join(vault, WORKTREES_DIR, f"ingest-{name}")
        os.makedirs(os.path.join(vault, WORKTREES_DIR), exist_ok=True)
        git(vault, "worktree", "add", "-b", branch, wt, target)
        base_sha = rev_parse(vault, f"refs/heads/{target}")
    except GitError as e:
        die(str(e), 3, status="git-failed")
    finally:
        lock.release()
    emit({"mode": "worktree", "vault": vault, "vault_source": rep["source"], "target_branch": target,
          "remote": remote, "branch": branch, "worktree": wt, "base_sha": base_sha,
          "baseline_commit": baseline or None, "fetched": sync["fetched"],
          "fast_forwarded": sync["fast_forwarded"], "cycle_slug": slug, "warnings": warnings})


def _validate_land_args(a) -> tuple[str, str, str, str]:
    vault = os.path.realpath(a.vault)
    if not is_git_toplevel(vault):
        die(f"{vault} is not the top level of a git repository", 2)
    if not branch_exists(vault, a.branch):
        die(f"branch {a.branch} does not exist in {vault}", 2)
    wt = os.path.realpath(a.worktree)
    wt_present = wt in registered_worktrees(vault)
    if not wt_present and os.path.exists(wt):
        die(f"{wt} exists but is not a registered worktree of {vault}", 2)
    return vault, a.branch, wt, ("present" if wt_present else "missing")


def finish_landing(vault: str, target: str, remote: str, branch: str, wt: str, wt_state: str,
                   a, result: dict, lock: Lock, warnings: list[str]) -> None:
    """Push (with one reconcile-and-retry), clean up, release the lock."""
    result["pushed"] = False
    if remote:
        hook = os.environ.get("CORTEX_INGEST_TEST_HOOK_BEFORE_PUSH")
        if hook:  # test-only: an executable simulating a remote writer between fetch and push
            subprocess.run([hook], cwd=vault, check=False)
        for attempt in (1, 2):
            proc = git(vault, "push", remote, f"{target}:{target}", check=False)
            if proc.returncode == 0:
                result["pushed"] = True
                break
            err = (proc.stderr or "") + (proc.stdout or "")
            rejected = any(k in err for k in ("rejected", "non-fast-forward", "fetch first", "stale info"))
            if not rejected or attempt == 2:
                warnings.append(f"push-failed: {err.strip()}")
                break
            fetch = git(vault, "fetch", remote, target, check=False)
            if fetch.returncode != 0:
                warnings.append(f"push-rejected and fetch-failed: {(fetch.stderr or '').strip()}")
                break
            mr = merge_ref(vault, f"refs/remotes/{remote}/{target}",
                           message(f":chore: reconcile {target} before landing ingest {cycle_slug(a.target_dir)}",
                                   f"Remote {remote}/{target} advanced while the ingest was landing.", a.target_dir))
            result["conflicts_auto_resolved"] += mr["conflicts_auto_resolved"]
            if not mr["ok"]:
                lock.write_owner("lead-resolving")
                emit({"status": "conflicts", "phase": "reconcile", "mode": result["mode"],
                      "conflicts": mr["conflicts"], "conflicts_auto_resolved": result["conflicts_auto_resolved"],
                      "branch": branch, "worktree": wt, "target_branch": target, "lock": lock.path,
                      "warnings": warnings}, 4)
    else:
        warnings.append("no-remote: push skipped")
    result["merge_commit"] = rev_parse(vault, f"refs/heads/{target}")
    result["cleaned_up"] = False
    if not a.no_cleanup:
        if wt_state == "present":
            git(vault, "worktree", "remove", "--force", wt)
        if branch_exists(vault, branch):
            proc = git(vault, "branch", "-d", branch, check=False)
            if proc.returncode != 0:
                warnings.append(f"branch-not-deleted: {(proc.stderr or '').strip()}")
        git(vault, "worktree", "prune")
        result["cleaned_up"] = True
    lock.release()
    result.update({"status": "landed", "target_branch": target, "branch": branch, "worktree": wt,
                   "remote": remote, "warnings": warnings})
    emit(result)


def cmd_land(a) -> None:
    vault, branch, wt, wt_state = _validate_land_args(a)
    anomaly_gate(vault)
    target, remote = current_branch(vault), remote_of(vault)
    slug = cycle_slug(a.target_dir)
    warnings: list[str] = []
    result = {"mode": "worktree", "ingest_commit": None, "merge_commit": None, "fast_forward": False,
              "baseline_commit": None, "conflicts_auto_resolved": [], "conflicts_lead_resolved": []}
    try:
        wt_dirty = dirty_paths(wt) if wt_state == "present" else []
        ahead = git_out(vault, "rev-list", "--count", f"refs/heads/{target}..refs/heads/{branch}") != "0"
        main_dirty = dirty_paths(vault)
        wt_changed = bool(wt_dirty) or ahead
        if not wt_changed and not main_dirty:
            emit({"status": "nothing-written", "branch": branch, "worktree": wt, "vault": vault}, 5)
        if wt_changed and wt_dirty:
            result["ingest_commit"] = commit_all(
                wt, message(f":add: ingest {slug}", f"Ingested SESSION-SUMMARY.md from SDD cycle {slug}.", a.target_dir))
        elif wt_changed:
            result["ingest_commit"] = rev_parse(vault, f"refs/heads/{branch}")
        lock = Lock(vault, "landing", branch, a.lock_timeout)
        lock.acquire()
        try:
            anomaly_gate(vault)
            if main_dirty and wt_changed:
                result["baseline_commit"] = commit_all(vault, baseline_message()) or None
                warnings.append("main checkout was dirty at land — baselined")
            elif main_dirty:
                result["mode"] = "in-place-fallback"
                result["ingest_commit"] = commit_all(
                    vault, message(f":add: ingest {slug}",
                                   f"Ingested SESSION-SUMMARY.md from SDD cycle {slug} (written in place).", a.target_dir))
                warnings.append("ingestor wrote into the main checkout — committed in place, no merge")
            sync = fetch_and_ff(vault, remote, target, warnings)
            if sync["diverged"]:
                mr = merge_ref(vault, f"refs/remotes/{remote}/{target}",
                               message(f":chore: reconcile {target} before landing ingest {slug}",
                                       f"Local {target} and {remote}/{target} had both moved.", a.target_dir))
                result["conflicts_auto_resolved"] += mr["conflicts_auto_resolved"]
                if not mr["ok"]:
                    lock.write_owner("lead-resolving")
                    emit({"status": "conflicts", "phase": "reconcile", "mode": result["mode"],
                          "conflicts": mr["conflicts"], "conflicts_auto_resolved": result["conflicts_auto_resolved"],
                          "branch": branch, "worktree": wt, "target_branch": target, "lock": lock.path,
                          "warnings": warnings}, 4)
            if result["mode"] == "worktree":
                mr = merge_ref(vault, f"refs/heads/{branch}",
                               message(f":chore: land ingest {slug}", f"Merged {branch} into {target}.", a.target_dir))
                result["fast_forward"] = mr["fast_forward"]
                result["conflicts_auto_resolved"] += mr["conflicts_auto_resolved"]
                if not mr["ok"]:
                    lock.write_owner("lead-resolving")
                    emit({"status": "conflicts", "phase": "land", "mode": "worktree",
                          "conflicts": mr["conflicts"], "conflicts_auto_resolved": result["conflicts_auto_resolved"],
                          "branch": branch, "worktree": wt, "target_branch": target, "lock": lock.path,
                          "warnings": warnings}, 4)
            finish_landing(vault, target, remote, branch, wt, wt_state, a, result, lock, warnings)
        finally:
            if lock.held and lock.stage != "lead-resolving":
                lock.release()
    except GitError as e:
        die(str(e), 3, status="git-failed", branch=branch, worktree=wt)


def cmd_continue(a) -> None:
    vault, branch, wt, wt_state = _validate_land_args(a)
    if merge_in_progress(vault) != "merge":
        die("no merge in progress in the vault — nothing to continue (run `land` instead)", 3,
            status="no-merge-in-progress")
    stage = lock_stage(vault)
    lock = Lock(vault, "lead-resolving", branch, a.lock_timeout)
    if stage == "lead-resolving":
        lock.held = True  # we inherit the lock `land` left for us
    elif stage:
        die(f"vault lock held at stage {stage!r} by another ingest", 3, status="lock-busy")
    else:
        lock.acquire()
    target, remote = current_branch(vault), remote_of(vault)
    warnings: list[str] = []
    try:
        for p in a.resolved:
            git(vault, "add", "--", p)
        leftovers = unmerged_paths(vault)
        markers = []
        for p in a.resolved:
            try:
                with open(os.path.join(vault, p), encoding="utf-8") as fh:
                    if CONFLICT_MARKER_RE.search(fh.read()):
                        markers.append(p)
            except OSError:
                pass
        if leftovers or markers:
            emit({"status": "conflicts", "phase": "continue", "conflicts": sorted(set(leftovers + markers)),
                  "markers_left_in": markers, "branch": branch, "worktree": wt, "target_branch": target,
                  "lock": lock.path}, 4)
        commit_pending_merge(vault)
        result = {"mode": "worktree", "ingest_commit": rev_parse(vault, f"refs/heads/{branch}"),
                  "merge_commit": None, "fast_forward": False, "baseline_commit": None,
                  "conflicts_auto_resolved": [], "conflicts_lead_resolved": list(a.resolved)}
        finish_landing(vault, target, remote, branch, wt, wt_state, a, result, lock, warnings)
    except GitError as e:
        die(str(e), 3, status="git-failed", branch=branch, worktree=wt)


def cmd_abort(a) -> None:
    vault, branch, wt, _ = _validate_land_args(a)
    aborted = False
    if merge_in_progress(vault) == "merge":
        git(vault, "merge", "--abort")
        aborted = True
    release_lock_path(vault)
    script = os.path.realpath(__file__)
    emit({"status": "aborted", "merge_aborted": aborted, "reason": a.reason or "", "branch": branch,
          "worktree": wt, "vault": vault,
          "resume": f'python3 "{script}" land --vault "{vault}" --branch "{branch}" '
                    f'--worktree "{wt}" --target-dir "{os.path.realpath(a.target_dir)}"'})


# ----------------------------------------------------------------------- main


def abs_no_dotdot(p: str) -> str:
    if ".." in Path(p).parts:
        die(f"path must not contain '..': {p}", 2)
    return os.path.realpath(os.path.expanduser(p))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cortex_ingest_git.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_cwd=False):
        p.add_argument("--target-dir", required=True, help="cycle folder (SESSION-SUMMARY.md lives here)")
        if with_cwd:
            p.add_argument("--cwd", default=os.getcwd())
            p.add_argument("--vault", default="", help="explicit vault root (wins over the stamps)")
            p.add_argument("--data-dir", default="", help="plugin data dir holding config.json (last resort)")
        p.add_argument("--lock-timeout", type=float, default=600.0)

    p = sub.add_parser("resolve"); add_common(p, with_cwd=True)
    p = sub.add_parser("prepare"); add_common(p, with_cwd=True)

    def add_land_like(p):
        p.add_argument("--vault", required=True)
        p.add_argument("--branch", required=True)
        p.add_argument("--worktree", required=True)
        p.add_argument("--no-cleanup", action="store_true")
        add_common(p)

    p = sub.add_parser("land"); add_land_like(p)
    p = sub.add_parser("continue"); add_land_like(p)
    p.add_argument("--resolved", action="append", default=[], help="a path the lead resolved (repeatable)")
    p = sub.add_parser("abort"); add_land_like(p)
    p.add_argument("--reason", default="")

    a = ap.parse_args(argv)
    a.target_dir = abs_no_dotdot(a.target_dir)
    if getattr(a, "cwd", None):
        a.cwd = abs_no_dotdot(a.cwd)
    if getattr(a, "vault", None):
        a.vault = abs_no_dotdot(a.vault)
    if getattr(a, "worktree", None):
        a.worktree = abs_no_dotdot(a.worktree)
    if a.cmd == "continue" and not a.resolved:
        die("continue requires at least one --resolved <path>", 2)
    try:
        {"resolve": cmd_resolve, "prepare": cmd_prepare, "land": cmd_land,
         "continue": cmd_continue, "abort": cmd_abort}[a.cmd](a)
    except GitError as e:
        die(str(e), 3, status="git-failed")


if __name__ == "__main__":
    main()
