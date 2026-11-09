"""Tests for scripts/cortex_vault_lint.py against a real temp vault."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cortex_vault_lint.py"


def make_vault(root: Path) -> Path:
    files = {
        "CLAUDE.md": "# vault\n",
        "index.md": "- [[wiki/sources/a-source]]\n- [[wiki/concepts/a-concept]]\n- [[wiki/entities/a-tool]]\n",
        "log.md": "| 2026-01-01 | article | A | [[wiki/sources/a-source]] |\n",
        ".rules/ingestion.md": "# rules\n",
        "raw/clippings/a-source.md": "---\ntitle: A\nsource: manual\ntype: article\ndate: 2026-01-01\n---\nbody\n",
        "wiki/sources/a-source.md": "---\ntitle: A\nsource: manual\ntype: article\ndate: 2026-01-01\n---\n[[a-concept]] [[a-tool]] [[raw/clippings/a-source]]\n",
        "wiki/concepts/a-concept.md": "---\ntitle: C\n---\n## Sources\n- [[wiki/sources/a-source]]\n",
        "wiki/entities/a-tool.md": "---\ntitle: T\ntype: tool\n---\n## Sources\n- [[wiki/sources/a-source]]\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class LintTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vault = make_vault(Path(tmp.name).resolve())

    def lint(self, *extra):
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.vault), *extra], capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)

    def status(self, report, check_id):
        return next(c for c in report["checks"] if c["id"] == check_id)

    def test_a_healthy_vault_passes_and_writes_the_report(self):
        rc, report = self.lint()
        self.assertEqual((rc, report["result"]), (0, "PASS"))
        self.assertIn("Result: PASS", (self.vault / ".lint-report.md").read_text())

    def test_no_report_writes_nothing(self):
        self.lint("--no-report")
        self.assertFalse((self.vault / ".lint-report.md").exists())

    def test_broken_link_fails(self):
        with (self.vault / "wiki/concepts/a-concept.md").open("a") as fh:
            fh.write("[[missing-thing]]\n")
        rc, report = self.lint()
        self.assertEqual(rc, 1)
        self.assertEqual(self.status(report, "broken_links")["count"], 1)

    def test_orphan_on_disk_fails(self):
        (self.vault / "wiki/concepts/lonely.md").write_text("---\ntitle: L\n---\n## Sources\n")
        rc, report = self.lint()
        self.assertEqual(rc, 1)
        self.assertEqual(self.status(report, "orphans_on_disk")["findings"], ["wiki/concepts/lonely.md"])

    def test_missing_frontmatter_field_fails(self):
        (self.vault / "wiki/entities/a-tool.md").write_text("---\ntitle: T\n---\n")
        _, report = self.lint()
        self.assertIn("missing type", self.status(report, "frontmatter")["findings"][0])

    def test_log_row_without_file_fails(self):
        with (self.vault / "log.md").open("a") as fh:
            fh.write("| 2026-01-02 | web | B | [[wiki/sources/gone]] |\n")
        _, report = self.lint()
        self.assertEqual(self.status(report, "log_drift")["count"], 1)

    def test_extra_root_markdown_fails_but_contributing_is_allowed(self):
        (self.vault / "CONTRIBUTING.md").write_text("x\n")
        (self.vault / "notes.md").write_text("x\n")
        _, report = self.lint()
        self.assertEqual(self.status(report, "outside_canon")["findings"], ["root: notes.md"])

    def test_concept_without_sources_fails(self):
        (self.vault / "wiki/concepts/a-concept.md").write_text("---\ntitle: C\n---\n")
        _, report = self.lint()
        self.assertEqual(self.status(report, "concept_without_sources")["count"], 1)

    def test_not_a_directory_exits_2(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.vault / "nope")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
