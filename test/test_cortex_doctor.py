"""Tests for scripts/cortex_doctor.py with fake claude and gh on PATH."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cortex_vault_lint import make_vault  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_doctor.py"
PLUGINS = '[{"id":"cortex@cortex-sdd","enabled":true},{"id":"pr-review-toolkit@x","enabled":true}]'


class DoctorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.vault = make_vault(self.tmp / "vault")
        (self.data / "config.json").write_text(json.dumps({"vault_path": str(self.vault)}))
        self.fake_claude()
        self.fake("gh", "exit 0")

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def fake_claude(self, version="2.1.300", plugins=PLUGINS):
        self.fake("claude", f"""if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi
echo '{plugins}'""")

    def doctor(self, *extra):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        env.pop("CORTEX_DIR", None)
        proc = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(self.data), *extra],
                              capture_output=True, text=True, env=env)
        report = json.loads(proc.stdout)
        return proc.returncode, {c["id"]: c for c in report["checks"]}

    def test_good_setup_without_a_git_vault_warns_only(self):
        rc, checks = self.doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(checks["vault-shape"]["status"], "PASS")
        self.assertEqual(checks["vault-git"]["status"], "WARN")
        self.assertEqual(checks["vault-lint"]["status"], "PASS")

    def test_git_vault_without_remote_warns(self):
        subprocess.run(["git", "init", "-q", str(self.vault)], check=True)
        rc, checks = self.doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(checks["vault-git"]["status"], "PASS")
        self.assertEqual(checks["vault-remote"]["status"], "WARN")

    def test_broken_vault_fails_with_the_missing_files(self):
        (self.vault / "index.md").unlink()
        rc, checks = self.doctor()
        self.assertEqual(rc, 1)
        self.assertEqual(checks["vault-shape"]["status"], "FAIL")
        self.assertIn("index.md", checks["vault-shape"]["detail"])

    def test_missing_config_points_to_install(self):
        (self.data / "config.json").unlink()
        rc, checks = self.doctor()
        self.assertEqual(rc, 1)
        self.assertIn("/cortex:install", checks["config"]["fix"])

    def test_unreadable_config_fails(self):
        (self.data / "config.json").write_text("nope")
        rc, checks = self.doctor()
        self.assertEqual((rc, checks["config"]["status"]), (1, "FAIL"))

    def test_prereqs_only_ignores_the_vault(self):
        (self.data / "config.json").unlink()
        rc, checks = self.doctor("--prereqs-only")
        self.assertEqual(rc, 0)
        self.assertNotIn("config", checks)

    def test_old_claude_fails(self):
        self.fake_claude(version="2.0.1")
        rc, checks = self.doctor()
        self.assertEqual((rc, checks["claude-version"]["status"]), (1, "FAIL"))

    def test_plugin_not_installed_fails(self):
        self.fake_claude(plugins="[]")
        rc, checks = self.doctor()
        self.assertEqual((rc, checks["plugin-enabled"]["status"]), (1, "FAIL"))

    def test_missing_review_toolkit_only_warns(self):
        self.fake_claude(plugins='[{"id":"cortex@cortex-sdd","enabled":true}]')
        rc, checks = self.doctor()
        self.assertEqual((rc, checks["pr-review-toolkit"]["status"]), (0, "WARN"))

    def test_unauthenticated_gh_fails(self):
        self.fake("gh", "exit 1")
        rc, checks = self.doctor()
        self.assertEqual(rc, 1)
        self.assertEqual(checks["gh"]["fix"], "gh auth login")


if __name__ == "__main__":
    unittest.main()
