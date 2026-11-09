"""Tests for scripts/cortex_init.py against real temp directories."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_init.py"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.data = self.tmp / "data"
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.cwd = self.tmp / "work"
        self.cwd.mkdir()

    def run_script(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)

    def configure(self):
        return self.run_script("configure", "--data-dir", self.data, "--vault", self.vault, "--cycles-root", self.tmp / "cycles")

    def init(self, *args):
        return self.run_script("init", "--data-dir", self.data, "--cwd", self.cwd, *args)


class ConfigureTests(Base):
    def test_writes_the_config(self):
        rc, _ = self.configure()
        self.assertEqual(rc, 0)
        config = json.loads((self.data / "config.json").read_text())
        self.assertEqual(config["schemaVersion"], 1)
        self.assertEqual(config["vault_path"], str(self.vault))

    def test_refuses_a_missing_vault(self):
        rc, res = self.run_script("configure", "--data-dir", self.data, "--vault", self.tmp / "nope")
        self.assertEqual(rc, 2)
        self.assertIn("not a directory", res["error"])


class InitTests(Base):
    def test_project_creates_a_timestamped_stamped_cycle(self):
        self.configure()
        rc, res = self.init("--project", "My Project")
        self.assertEqual(rc, 0, res)
        target = Path(res["target_dir"])
        self.assertRegex(target.name, r"^\d{6}-\d{4}-my-project$")
        self.assertEqual(target.parent, self.tmp / "cycles")
        settings = json.loads((target / ".claude/settings.json").read_text())
        self.assertEqual(settings["cortexCycle"], target.name)
        self.assertEqual(settings["cortexAiBrainPath"], str(self.vault))
        self.assertIn(str(self.vault), settings["permissions"]["additionalDirectories"])
        self.assertNotIn("model", settings)

    def test_vault_line_uses_the_exact_parsed_wording(self):
        self.configure()
        _, res = self.init("--project", "x")
        text = (Path(res["target_dir"]) / "CLAUDE.md").read_text()
        self.assertIn(f"- ai-brain vault for this project: `{self.vault}`", text)
        self.assertNotIn("{{", text)

    def test_setup_is_seeded_imported_and_never_overwritten(self):
        self.configure()
        _, res = self.init("--project", "260101-1200-x")
        target = Path(res["target_dir"])
        self.assertNotIn("{{", (target / "SETUP.md").read_text())
        (target / "SETUP.md").write_text("mine\n")
        self.init("--project", "260101-1200-x")
        self.assertEqual((target / "SETUP.md").read_text(), "mine\n")
        self.assertEqual((target / "CLAUDE.md").read_text().count("@SETUP.md"), 1)

    def test_existing_timestamp_prefix_is_idempotent(self):
        self.configure()
        _, first = self.init("--project", "260101-1200-x")
        _, second = self.init("--project", "260101-1200-x")
        self.assertEqual(first["target_dir"], second["target_dir"])

    def test_rerun_keeps_content_outside_the_markers_and_other_settings(self):
        self.configure()
        _, res = self.init("--project", "260101-1200-x")
        target = Path(res["target_dir"])
        with (target / "CLAUDE.md").open("a") as fh:
            fh.write("\nmy own notes\n")
        path = target / ".claude/settings.json"
        settings = json.loads(path.read_text())
        settings["custom"] = 1
        path.write_text(json.dumps(settings))
        self.init("--project", "260101-1200-x")
        self.assertIn("my own notes", (target / "CLAUDE.md").read_text())
        self.assertEqual((target / "CLAUDE.md").read_text().count("cortex-init:begin"), 1)
        self.assertEqual(json.loads(path.read_text())["custom"], 1)

    def test_no_project_stamps_a_plain_folder(self):
        self.configure()
        rc, res = self.init()
        self.assertEqual((rc, res["target_dir"]), (0, str(self.cwd)))
        self.assertTrue((self.cwd / "CLAUDE.md").is_file())

    def test_no_project_refuses_inside_a_git_repo(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        rc, res = self.init()
        self.assertEqual(rc, 2)
        self.assertIn("git repository", res["error"])
        self.assertFalse((self.cwd / "CLAUDE.md").exists())

    def test_repos_are_mapped_and_missing_ones_warned(self):
        self.configure()
        repo = self.tmp / "repo"
        repo.mkdir()
        _, res = self.init("--project", "x", "--repos", f"{repo},{self.tmp}/missing")
        self.assertEqual(res["repos"], [str(repo)])
        self.assertTrue(any("missing" in w for w in res["warnings"]))
        self.assertIn(f"`{repo}`", (Path(res["target_dir"]) / "CLAUDE.md").read_text())

    def test_without_a_vault_it_warns_and_omits_the_block(self):
        rc, res = self.run_script("init", "--cwd", self.cwd)
        self.assertEqual(rc, 0)
        self.assertTrue(any("/cortex:install" in w for w in res["warnings"]))
        self.assertNotIn("ai-brain vault", (self.cwd / "CLAUDE.md").read_text())

    def test_project_with_a_slash_is_rejected(self):
        rc, res = self.init("--project", "a/b")
        self.assertEqual(rc, 2)
        self.assertIn("single segment", res["error"])

    def test_malformed_settings_is_refused(self):
        (self.cwd / ".claude").mkdir()
        (self.cwd / ".claude/settings.json").write_text("{not json")
        rc, res = self.init()
        self.assertEqual(rc, 2)
        self.assertIn("malformed", res["error"])


if __name__ == "__main__":
    unittest.main()
