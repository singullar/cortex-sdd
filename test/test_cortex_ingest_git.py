#!/usr/bin/env python3
"""Unit tests for scripts/cortex_ingest_git.py — the vault git lifecycle behind
/cortex:ingest (prepare → land → continue/abort).

Contract, in order of importance: the vault's checked-out branch is the
target (never hardcoded), a dirty main checkout is baselined (never asked),
the ingest lands from a per-run worktree with Cortex-* trailers naming the
originating cycle, index.md/log.md conflicts auto-resolve by union while
other conflicts are handed to the lead (exit 4, lock held), push is
warn-and-continue (no remote / rejected → reconcile once), and anomalies
stop loudly (exit 3) instead of guessing.

Self-contained on purpose (stdlib unittest only, real temp git repos): run with
    python3 test/test_cortex_ingest_git.py
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_ingest_git.py"

SEED = {
    "CLAUDE.md": "# vault\n\n@.rules/git.md\n",
    ".rules/git.md": "# Git Rules\n- `:add:` / `:chore:`\n",
    ".rules/ingestion.md": "# rules\n",
    "index.md": "# Index\n\n## Sources\n- [[a]]\n",
    "log.md": "# Log\n\n| date | source |\n|---|---|\n| 2026-01-01 | a |\n",
    "wiki/concepts/a.md": "# A\n\n## Sources\n- [[a]]\n",
    ".gitignore": ".worktrees/\n*.canvas\n",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-ingest-")).resolve()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_GLOBAL": str(self.tmp / "home" / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x", "CORTEX_INGEST_LOCK_POLL": "0.05",
        })
        (self.tmp / "home").mkdir()
        self.env.pop("CORTEX_INGEST_TEST_HOOK_BEFORE_PUSH", None)

    # ---- git helpers -------------------------------------------------------
    def git(self, cwd, *args, check=True):
        p = subprocess.run(["git", *args], cwd=str(cwd), env=self.env, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed in {cwd}:\n{p.stdout}{p.stderr}")
        return p

    def out(self, cwd, *args):
        return self.git(cwd, *args).stdout.strip()

    def write(self, root, rel, content, append=False):
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as fh:
            fh.write(content)

    def make_vault(self, branch="main", remote=True, name="vault"):
        vault = self.tmp / name
        if remote:
            bare = self.tmp / f"{name}-remote.git"
            self.git(self.tmp, "init", "--bare", "-b", branch, str(bare))
            self.git(self.tmp, "clone", "-q", str(bare), str(vault))
            self.git(vault, "checkout", "-q", "-b", branch)
        else:
            vault.mkdir()
            self.git(vault, "init", "-q", "-b", branch)
        for rel, content in SEED.items():
            self.write(vault, rel, content)
        self.git(vault, "add", "-A")
        self.git(vault, "commit", "-q", "-m", ":chore: seed")
        if remote:
            self.git(vault, "push", "-q", "-u", "origin", branch)
        return vault

    def other_clone(self, vault_name="vault", name="other"):
        bare = self.tmp / f"{vault_name}-remote.git"
        other = self.tmp / name
        self.git(self.tmp, "clone", "-q", str(bare), str(other))
        return other

    def fake_cycle(self, name="260827-1530-widget-thing", vault=None):
        cyc = self.tmp / "projects" / name
        cyc.mkdir(parents=True)
        settings = {"cortexCycle": name}
        if vault is not None:
            settings["cortexAiBrainPath"] = str(vault)
        self.write(cyc, ".claude/settings.json", json.dumps(settings))
        return cyc

    def fake_ingest(self, wt, slug="s1", touch_concept=True):
        self.write(wt, "index.md", f"- [[{slug}]]\n", append=True)
        self.write(wt, "log.md", f"| 2026-08-27 | {slug} |\n", append=True)
        self.write(wt, f"wiki/sources/{slug}.md", f"# {slug}\n")
        if touch_concept:
            self.write(wt, "wiki/concepts/a.md", f"- [[{slug}]]\n", append=True)

    # ---- script -----------------------------------------------------------
    def run_script(self, *args, env=None):
        p = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=env or self.env,
                           capture_output=True, text=True)
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            raise AssertionError(f"non-JSON stdout (rc={p.returncode}):\n{p.stdout}\n{p.stderr}")
        return p.returncode, data, p.stderr

    def prepare(self, cyc, vault=None, **kw):
        args = ["prepare", "--target-dir", cyc, "--cwd", self.tmp]
        if vault:
            args += ["--vault", vault]
        return self.run_script(*args, **kw)

    def land(self, prep, cyc, *extra, env=None):
        return self.run_script("land", "--vault", prep["vault"], "--branch", prep["branch"],
                               "--worktree", prep["worktree"], "--target-dir", cyc, *extra, env=env)

    def prepared(self, branch="main", remote=True):
        vault = self.make_vault(branch=branch, remote=remote)
        cyc = self.fake_cycle(vault=vault)
        rc, prep, err = self.prepare(cyc)
        self.assertEqual(rc, 0, err)
        self.assertEqual(prep["mode"], "worktree")
        return vault, cyc, prep

    def lock_dir(self, vault):
        return Path(vault) / ".git" / "cortex-ingest.lock"

    def worktrees(self, vault):
        return self.out(vault, "worktree", "list", "--porcelain")


class ResolveTests(Base):
    def test_precedence_arg_target_cwd_claude_md(self):
        v1, v2, v3, v4 = [self.tmp / n for n in ("v1", "v2", "v3", "v4")]
        for v in (v1, v2, v3, v4):
            v.mkdir()
        cyc = self.fake_cycle(vault=v2)
        cwd = self.tmp / "cwd"
        cwd.mkdir()
        self.write(cwd, ".claude/settings.json", json.dumps({"cortexAiBrainPath": str(v3)}))
        self.write(cwd, "CLAUDE.md", f"### Knowledge lookup\n\n- ai-brain vault for this project: `{v4}`\n")
        rc, d, _ = self.run_script("resolve", "--target-dir", cyc, "--cwd", cwd, "--vault", v1)
        self.assertEqual((d["vault"], d["source"]), (str(v1), "arg"))
        rc, d, _ = self.run_script("resolve", "--target-dir", cyc, "--cwd", cwd)
        self.assertEqual((d["vault"], d["source"]), (str(v2), "target-settings"))
        plain = self.fake_cycle(name="260101-0000-plain")
        rc, d, _ = self.run_script("resolve", "--target-dir", plain, "--cwd", cwd)
        self.assertEqual((d["vault"], d["source"]), (str(v3), "cwd-settings"))
        os.remove(cwd / ".claude" / "settings.json")
        rc, d, _ = self.run_script("resolve", "--target-dir", plain, "--cwd", cwd)
        self.assertEqual((d["vault"], d["source"]), (str(v4), "claude-md"))
        self.assertFalse(d["is_git"])

    def test_none_and_nested_are_not_git(self):
        cyc = self.fake_cycle()
        rc, d, _ = self.run_script("resolve", "--target-dir", cyc, "--cwd", self.tmp / "nowhere")
        self.assertEqual(rc, 0)
        self.assertEqual((d["vault"], d["source"], d["is_git"]), ("", "none", False))
        vault = self.make_vault()
        nested = vault / "sub"
        nested.mkdir()
        rc, d, _ = self.run_script("resolve", "--target-dir", cyc, "--cwd", self.tmp, "--vault", nested)
        self.assertFalse(d["is_git"])
        rc, d, _ = self.run_script("resolve", "--target-dir", cyc, "--cwd", self.tmp, "--vault", vault)
        self.assertTrue(d["is_git"])
        self.assertEqual((d["target_branch"], d["remote"]), ("main", "origin"))

    def test_dotdot_rejected(self):
        rc, d, _ = self.run_script("resolve", "--target-dir", self.tmp / ".." / "x", "--cwd", self.tmp)
        self.assertEqual(rc, 2)


class ConfigFallbackTests(Base):
    def test_plugin_config_is_the_last_resort(self):
        vault = self.tmp / "vault"
        vault.mkdir()
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"vault_path": str(vault)}))
        cyc = self.fake_cycle()
        rc, res, err = self.run_script("resolve", "--target-dir", cyc, "--cwd", cyc, "--data-dir", data)
        self.assertEqual(rc, 0, err)
        self.assertEqual((res["vault"], res["source"]), (os.path.realpath(vault), "config"))

    def test_a_cycle_stamp_wins_over_the_config(self):
        stamped = self.tmp / "stamped"
        stamped.mkdir()
        other = self.tmp / "other"
        other.mkdir()
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"vault_path": str(other)}))
        cyc = self.fake_cycle(vault=stamped)
        rc, res, err = self.run_script("resolve", "--target-dir", cyc, "--cwd", cyc, "--data-dir", data)
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["vault"], os.path.realpath(stamped))

    def test_unreadable_config_resolves_to_nothing(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text("not json")
        cyc = self.fake_cycle()
        rc, res, err = self.run_script("resolve", "--target-dir", cyc, "--cwd", cyc, "--data-dir", data)
        self.assertEqual((res["vault"], res["source"]), ("", "none"))


class PrepareTests(Base):
    def test_clean_main_creates_worktree_from_target(self):
        vault, cyc, prep = self.prepared()
        self.assertIsNone(prep["baseline_commit"])
        self.assertEqual(prep["target_branch"], "main")
        self.assertTrue(prep["branch"].startswith("ingest/"))
        self.assertTrue(prep["branch"].endswith("-widget-thing"))
        self.assertEqual(prep["cycle_slug"], "widget-thing")
        self.assertTrue(Path(prep["worktree"]).is_dir())
        self.assertIn(prep["worktree"], self.worktrees(vault))
        self.assertEqual(self.out(vault, "rev-parse", prep["branch"]), prep["base_sha"])
        self.assertEqual(self.out(vault, "symbolic-ref", "--short", "HEAD"), "main")
        # worktree carries the tracked rules
        self.assertTrue((Path(prep["worktree"]) / ".rules" / "ingestion.md").exists())

    def test_master_branch_never_hardcoded(self):
        vault, cyc, prep = self.prepared(branch="master")
        self.assertEqual(prep["target_branch"], "master")
        self.assertFalse(self.out(vault, "branch", "--list", "main"))
        self.fake_ingest(prep["worktree"])
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["target_branch"], "master")
        self.assertEqual(self.out(vault, "rev-parse", "origin/master"), res["merge_commit"])

    def test_dirty_main_is_baselined_including_untracked(self):
        vault = self.make_vault()
        self.write(vault, "index.md", "- [[old]]\n", append=True)
        self.write(vault, "wiki/concepts/old.md", "# old\n")
        cyc = self.fake_cycle(vault=vault)
        rc, prep, err = self.prepare(cyc)
        self.assertEqual(rc, 0, err)
        self.assertTrue(prep["baseline_commit"])
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")
        self.assertEqual(self.out(vault, "log", "-1", "--format=%s"), ":chore: baseline pending vault changes")
        self.assertIn("wiki/concepts/old.md", self.out(vault, "show", "--name-only", "--format=", "HEAD"))
        self.assertIn("Cortex-Ingest: baseline", self.out(vault, "log", "-1", "--format=%B"))
        # the worktree sees the baselined content
        self.assertTrue((Path(prep["worktree"]) / "wiki/concepts/old.md").exists())

    def test_local_config_idempotent_and_never_tracked(self):
        vault = self.make_vault()
        self.write(vault, ".gitignore", "*.canvas\n")  # drop the .worktrees/ rule
        self.git(vault, "commit", "-q", "-am", ":chore: no ignore")
        cyc = self.fake_cycle(vault=vault)
        for _ in range(2):
            rc, prep, err = self.prepare(cyc)
            self.assertEqual(rc, 0, err)
            self.run_script("land", "--vault", vault, "--branch", prep["branch"], "--worktree",
                            prep["worktree"], "--target-dir", cyc)
        exclude = (vault / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count(".worktrees/"), 1)
        attrs = (vault / ".git" / "info" / "attributes").read_text()
        self.assertEqual(attrs.count("index.md merge=union"), 1)
        self.assertEqual(attrs.count("log.md merge=union"), 1)
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")

    def test_remote_ahead_fast_forwards(self):
        vault = self.make_vault()
        other = self.other_clone()
        self.write(other, "wiki/concepts/b.md", "# B\n")
        self.git(other, "add", "-A"); self.git(other, "commit", "-q", "-m", ":add: b"); self.git(other, "push", "-q")
        tip = self.out(other, "rev-parse", "HEAD")
        rc, prep, err = self.prepare(self.fake_cycle(vault=vault))
        self.assertEqual(rc, 0, err)
        self.assertTrue(prep["fetched"] and prep["fast_forwarded"])
        self.assertEqual(prep["base_sha"], tip)

    def test_diverged_warns_without_merging(self):
        vault = self.make_vault()
        other = self.other_clone()
        self.write(other, "wiki/concepts/b.md", "# B\n")
        self.git(other, "add", "-A"); self.git(other, "commit", "-q", "-m", ":add: b"); self.git(other, "push", "-q")
        self.write(vault, "wiki/concepts/c.md", "# C\n")
        self.git(vault, "add", "-A"); self.git(vault, "commit", "-q", "-m", ":add: c")
        local = self.out(vault, "rev-parse", "HEAD")
        rc, prep, err = self.prepare(self.fake_cycle(vault=vault))
        self.assertEqual(rc, 0, err)
        self.assertFalse(prep["fast_forwarded"])
        self.assertTrue(any(w.startswith("diverged") for w in prep["warnings"]))
        self.assertEqual(prep["base_sha"], local)

    def test_no_remote_and_fetch_failure_warn(self):
        vault = self.make_vault(remote=False)
        rc, prep, err = self.prepare(self.fake_cycle(vault=vault))
        self.assertEqual(rc, 0, err)
        self.assertEqual(prep["remote"], "")
        self.assertIn("no-remote: fetch skipped", prep["warnings"])
        vault2 = self.make_vault(name="v2")
        self.git(vault2, "remote", "set-url", "origin", str(self.tmp / "does-not-exist.git"))
        rc, prep, err = self.prepare(self.fake_cycle(name="260101-0101-x", vault=vault2))
        self.assertEqual(rc, 0, err)
        self.assertTrue(any(w.startswith("fetch-failed") for w in prep["warnings"]))
        self.assertEqual(prep["mode"], "worktree")

    def test_collision_gets_suffix(self):
        vault, cyc, p1 = self.prepared()
        rc, p2, err = self.prepare(cyc)
        self.assertEqual(rc, 0, err)
        self.assertNotEqual(p1["branch"], p2["branch"])
        self.assertTrue(p2["branch"].endswith("-2"))

    def test_legacy_when_not_git(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        rc, prep, err = self.prepare(self.fake_cycle(vault=plain))
        self.assertEqual(rc, 0, err)
        self.assertEqual(prep["mode"], "legacy")
        rc, prep, err = self.prepare(self.fake_cycle(name="260101-0101-y"))
        self.assertEqual(prep["mode"], "legacy")

    def test_detached_head_and_foreign_merge_are_exit_3(self):
        vault = self.make_vault()
        self.git(vault, "checkout", "-q", "--detach")
        rc, d, _ = self.prepare(self.fake_cycle(vault=vault))
        self.assertEqual((rc, d["status"]), (3, "detached-head"))
        self.git(vault, "checkout", "-q", "main")
        (vault / ".git" / "MERGE_HEAD").write_text(self.out(vault, "rev-parse", "HEAD") + "\n")
        rc, d, _ = self.prepare(self.fake_cycle(name="260101-0101-z", vault=vault))
        self.assertEqual((rc, d["status"]), (3, "foreign-operation"))

    def test_live_lock_times_out_and_stale_lock_breaks(self):
        vault = self.make_vault()
        lock = self.lock_dir(vault)
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "host": __import__("socket").gethostname(),
                                                     "stage": "landing", "created_at": "2099-01-01T00:00:00Z"}))
        cyc = self.fake_cycle(vault=vault)
        rc, d, _ = self.run_script("prepare", "--target-dir", cyc, "--cwd", self.tmp, "--lock-timeout", "0.3")
        self.assertEqual((rc, d["status"]), (3, "lock-timeout"))
        self.assertTrue(lock.exists())
        # dead pid → stale → broken
        (lock / "owner.json").write_text(json.dumps({"pid": 2**22 - 1, "host": __import__("socket").gethostname(),
                                                     "stage": "landing", "created_at": "2099-01-01T00:00:00Z"}))
        rc, d, err = self.prepare(cyc)
        self.assertEqual(rc, 0, err)
        self.assertFalse(lock.exists())
        # old timestamp → stale even for lead-resolving
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "host": "elsewhere",
                                                     "stage": "lead-resolving", "created_at": "2000-01-01T00:00:00Z"}))
        rc, d, err = self.prepare(self.fake_cycle(name="260101-0101-w", vault=vault))
        self.assertEqual(rc, 0, err)


class LandTests(Base):
    def test_happy_path_ff_trailers_push_cleanup(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"])
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["status"], "landed")
        self.assertEqual(res["mode"], "worktree")
        self.assertTrue(res["fast_forward"])
        self.assertTrue(res["pushed"])
        self.assertTrue(res["cleaned_up"])
        self.assertEqual(res["merge_commit"], self.out(vault, "rev-parse", "main"))
        self.assertEqual(self.out(vault, "rev-parse", "origin/main"), res["merge_commit"])
        body = self.out(vault, "log", "-1", "--format=%B")
        self.assertTrue(body.startswith(":add: ingest widget-thing"))
        self.assertIn("Cortex-Cycle: widget-thing", body)
        self.assertIn(f"Cortex-Cycle-Path: {os.path.realpath(cyc)}", body)
        self.assertNotIn("Cortex-Context", body)
        self.assertEqual(self.out(vault, "log", "-1", "--format=%(trailers:key=Cortex-Cycle,valueonly)"), "widget-thing")
        self.assertFalse(Path(prep["worktree"]).exists())
        self.assertFalse(self.out(vault, "branch", "--list", prep["branch"]))
        self.assertFalse(self.lock_dir(vault).exists())
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")
        self.assertTrue((vault / "wiki/sources/s1.md").exists())

    def test_no_remote_lands_locally(self):
        vault, cyc, prep = self.prepared(remote=False)
        self.fake_ingest(prep["worktree"])
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertFalse(res["pushed"])
        self.assertIn("no-remote: push skipped", res["warnings"])
        self.assertTrue(res["cleaned_up"])
        self.assertTrue((vault / "wiki/sources/s1.md").exists())

    def test_no_context_stamp_omits_optional_trailers(self):
        vault = self.make_vault()
        cyc = self.tmp / "projects" / "260101-0101-bare"
        cyc.mkdir(parents=True)
        rc, prep, err = self.prepare(cyc, vault=vault)
        self.assertEqual(rc, 0, err)
        self.fake_ingest(prep["worktree"])
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        body = self.out(vault, "log", "-1", "--format=%B")
        self.assertIn("Cortex-Cycle: bare", body)
        self.assertNotIn("Cortex-Context", body)
        self.assertNotIn("Cortex-Product", body)

    def test_in_place_fallback(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(vault)  # agent disobeyed and wrote into the main checkout
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["mode"], "in-place-fallback")
        self.assertTrue(res["pushed"])
        self.assertTrue(self.out(vault, "log", "-1", "--format=%s").startswith(":add: ingest widget-thing"))
        self.assertFalse(Path(prep["worktree"]).exists())
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")

    def test_nothing_written_is_exit_5_and_touches_nothing(self):
        vault, cyc, prep = self.prepared()
        head = self.out(vault, "rev-parse", "HEAD")
        rc, res, _ = self.land(prep, cyc)
        self.assertEqual((rc, res["status"]), (5, "nothing-written"))
        self.assertEqual(self.out(vault, "rev-parse", "HEAD"), head)
        self.assertTrue(Path(prep["worktree"]).exists())
        self.assertFalse(self.lock_dir(vault).exists())

    def test_dirty_main_plus_worktree_lands_both(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"])
        self.write(vault, "wiki/concepts/stray.md", "# stray\n")
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertTrue(res["baseline_commit"])
        self.assertEqual(res["mode"], "worktree")
        self.assertFalse(res["fast_forward"])
        self.assertTrue((vault / "wiki/concepts/stray.md").exists() and (vault / "wiki/sources/s1.md").exists())
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")

    def test_unrelated_remote_change_makes_merge_commit_with_trailers(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"])
        other = self.other_clone()
        self.write(other, "wiki/concepts/b.md", "# B\n")
        self.git(other, "add", "-A"); self.git(other, "commit", "-q", "-m", ":add: b"); self.git(other, "push", "-q")
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertFalse(res["fast_forward"])
        self.assertTrue(res["pushed"])
        body = self.out(vault, "log", "-1", "--format=%B")
        self.assertTrue(body.startswith(":chore: land ingest widget-thing"))
        self.assertIn("Cortex-Cycle: widget-thing", body)
        self.assertEqual(len(self.out(vault, "log", "-1", "--format=%P").split()), 2)
        self.assertTrue((vault / "wiki/concepts/b.md").exists() and (vault / "wiki/sources/s1.md").exists())

    def test_index_and_log_union_auto_resolve(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"], slug="mine")
        other = self.other_clone()
        self.write(other, "index.md", "- [[theirs]]\n", append=True)
        self.write(other, "log.md", "| 2026-08-27 | theirs |\n", append=True)
        self.git(other, "add", "-A"); self.git(other, "commit", "-q", "-m", ":add: theirs"); self.git(other, "push", "-q")
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        index = (vault / "index.md").read_text()
        log = (vault / "log.md").read_text()
        self.assertIn("[[mine]]", index); self.assertIn("[[theirs]]", index)
        self.assertIn("| mine |", log); self.assertIn("| theirs |", log)
        self.assertNotIn("<<<<<<<", index + log)
        self.assertTrue(res["pushed"])
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")

    def _conflict_setup(self):
        vault, cyc, prep = self.prepared()
        self.write(prep["worktree"], "wiki/concepts/a.md", "# A\n\n## Sources\n- [[a]]\n- [[mine]]\n")
        other = self.other_clone()
        self.write(other, "wiki/concepts/a.md", "# A\n\n## Sources\n- [[a]]\n- [[theirs]]\n")
        self.git(other, "add", "-A"); self.git(other, "commit", "-q", "-m", ":add: theirs"); self.git(other, "push", "-q")
        rc, res, err = self.land(prep, cyc)
        self.assertEqual((rc, res["status"]), (4, "conflicts"), err)
        self.assertEqual(res["conflicts"], ["wiki/concepts/a.md"])
        self.assertTrue((vault / ".git" / "MERGE_HEAD").exists())
        lock = self.lock_dir(vault)
        self.assertTrue(lock.exists())
        self.assertEqual(json.loads((lock / "owner.json").read_text())["stage"], "lead-resolving")
        return vault, cyc, prep

    def test_wiki_conflict_hands_over_then_continue_lands(self):
        vault, cyc, prep = self._conflict_setup()
        # continue with markers still there → exit 4 again
        rc, res, _ = self.run_script("continue", "--vault", vault, "--branch", prep["branch"], "--worktree",
                                     prep["worktree"], "--target-dir", cyc, "--resolved", "wiki/concepts/a.md")
        self.assertEqual((rc, res["status"]), (4, "conflicts"))
        self.assertIn("wiki/concepts/a.md", res["markers_left_in"])
        # the lead merges both sides
        self.write(vault, "wiki/concepts/a.md", "# A\n\n## Sources\n- [[a]]\n- [[theirs]]\n- [[mine]]\n")
        rc, res, err = self.run_script("continue", "--vault", vault, "--branch", prep["branch"], "--worktree",
                                       prep["worktree"], "--target-dir", cyc, "--resolved", "wiki/concepts/a.md")
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["status"], "landed")
        self.assertEqual(res["conflicts_lead_resolved"], ["wiki/concepts/a.md"])
        self.assertTrue(res["pushed"])
        self.assertFalse(self.lock_dir(vault).exists())
        self.assertFalse((vault / ".git" / "MERGE_HEAD").exists())
        self.assertFalse(Path(prep["worktree"]).exists())
        self.assertIn("Cortex-Cycle: widget-thing", self.out(vault, "log", "-1", "--format=%B"))
        self.assertIn("[[mine]]", (vault / "wiki/concepts/a.md").read_text())

    def test_abort_keeps_branch_and_worktree_then_land_resumes(self):
        vault, cyc, prep = self._conflict_setup()
        rc, res, err = self.run_script("abort", "--vault", vault, "--branch", prep["branch"], "--worktree",
                                       prep["worktree"], "--target-dir", cyc, "--reason", "cannot merge")
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["status"], "aborted")
        self.assertTrue(res["merge_aborted"])
        self.assertIn("land --vault", res["resume"])
        self.assertFalse((vault / ".git" / "MERGE_HEAD").exists())
        self.assertFalse(self.lock_dir(vault).exists())
        self.assertTrue(Path(prep["worktree"]).exists())
        self.assertTrue(self.out(vault, "branch", "--list", prep["branch"]))
        self.assertEqual(self.out(vault, "status", "--porcelain"), "")
        # a human fixed the other side to match → land resumes on the committed branch
        self.write(vault, "wiki/concepts/a.md", "# A\n\n## Sources\n- [[a]]\n- [[mine]]\n")
        self.git(vault, "commit", "-q", "-am", ":chore: align")
        # even with the worktree removed by hand, the branch still lands (resume path)
        self.git(vault, "worktree", "remove", "--force", prep["worktree"])
        rc, res, err = self.land(prep, cyc)
        self.assertEqual(rc, 0, err)
        self.assertEqual(res["status"], "landed")
        self.assertFalse(self.out(vault, "branch", "--list", prep["branch"]))

    def test_push_rejected_reconciles_and_retries(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"])
        other = self.other_clone()
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\ncd '{other}' && echo '# B' > wiki/concepts/b.md && git add -A && "
                        f"git commit -q -m ':add: b' && git push -q\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
        env = dict(self.env, CORTEX_INGEST_TEST_HOOK_BEFORE_PUSH=str(hook))
        rc, res, err = self.land(prep, cyc, env=env)
        self.assertEqual(rc, 0, err)
        self.assertTrue(res["pushed"])
        self.assertEqual(self.out(vault, "rev-parse", "origin/main"), res["merge_commit"])
        self.assertTrue((vault / "wiki/concepts/b.md").exists())
        self.assertIn(":chore: reconcile main before landing ingest widget-thing",
                      self.out(vault, "log", "-3", "--format=%s"))

    def test_parallel_lands_serialize(self):
        vault = self.make_vault()
        cyc1 = self.fake_cycle(name="260101-0101-one", vault=vault)
        cyc2 = self.fake_cycle(name="260101-0102-two", vault=vault)
        rc, p1, err = self.prepare(cyc1); self.assertEqual(rc, 0, err)
        rc, p2, err = self.prepare(cyc2); self.assertEqual(rc, 0, err)
        self.fake_ingest(p1["worktree"], slug="one", touch_concept=False)
        self.fake_ingest(p2["worktree"], slug="two", touch_concept=False)
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "land", "--vault", str(vault), "--branch", p["branch"],
                                   "--worktree", p["worktree"], "--target-dir", str(c)], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for p, c in ((p1, cyc1), (p2, cyc2))]
        outs = [pr.communicate() for pr in procs]
        for pr, (o, e) in zip(procs, outs):
            self.assertEqual(pr.returncode, 0, o + e)
        self.assertTrue((vault / "wiki/sources/one.md").exists() and (vault / "wiki/sources/two.md").exists())
        index = (vault / "index.md").read_text()
        self.assertIn("[[one]]", index); self.assertIn("[[two]]", index)
        self.assertEqual(self.out(vault, "rev-parse", "origin/main"), self.out(vault, "rev-parse", "main"))
        self.assertFalse(self.lock_dir(vault).exists())
        self.assertNotIn(".worktrees/ingest-", self.worktrees(vault))

    def test_no_cleanup_keeps_worktree(self):
        vault, cyc, prep = self.prepared()
        self.fake_ingest(prep["worktree"])
        rc, res, err = self.land(prep, cyc, "--no-cleanup")
        self.assertEqual(rc, 0, err)
        self.assertFalse(res["cleaned_up"])
        self.assertTrue(Path(prep["worktree"]).exists())

    def test_unregistered_worktree_is_exit_2(self):
        vault, cyc, prep = self.prepared()
        bogus = self.tmp / "bogus"
        bogus.mkdir()
        rc, res, _ = self.run_script("land", "--vault", vault, "--branch", prep["branch"], "--worktree", bogus,
                                     "--target-dir", cyc)
        self.assertEqual(rc, 2)
        rc, res, _ = self.run_script("land", "--vault", vault, "--branch", "ingest/nope", "--worktree",
                                     prep["worktree"], "--target-dir", cyc)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
