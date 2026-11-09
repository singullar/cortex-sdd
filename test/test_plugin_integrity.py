"""The shipped content must be self-consistent and carry nothing private."""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SHIPPED_DIRS = ("skills", "agents", "scripts", "hooks", ".claude/rules", ".claude-plugin")
ROOT_DOCS = ("README.md", "CONTRIBUTING.md", "CLAUDE.md")

SKILL_REF = re.compile(r"/cortex:([a-z][a-z-]*)")
SKILL_CALL = re.compile(r"Skill\(\s*(?:skill:\s*)?[\"']?cortex:([a-z][a-z-]*)")
SCRIPT_REF = re.compile(r"\bcortex_[a-z_]+\.py\b")
REFERENCE_REF = re.compile(r"\bskills/([a-z-]+)/references/([a-z-]+\.md)\b")
LOCAL_REFERENCE_REF = re.compile(r"(?<![/\w-])references/([a-z-]+\.md)\b")
TEMPLATE_REF = re.compile(r"\b([a-z-]+\.md\.tmpl)\b")

RESIDUE = re.compile(
    r"nexus|\bdots\b|\bsagas?\b|handoff-final|decision block|D<n>|autonomy|full-auto|fast-exec|specialist"
    r"|issue\.md|gh project|workspace-projects|design\.md|prototype\.md|cortexContext|cortexProduct"
    r"|cortexSaga|cortexAutonomy|user memory|\[\[[a-z][a-z0-9/_-]*\]\]",
    re.I,
)
# Allows only the exact public repository paths.
PRIVATE = re.compile(r"ssdd|singullar(?!/cortex-(?:sdd|brain)\b)|notion|cmux|/Users/|/home/[a-z]", re.I)

def shipped_files():
    for name in ROOT_DOCS:
        yield REPO_ROOT / name
    for top in SHIPPED_DIRS:
        for path in sorted((REPO_ROOT / top).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


def all_tracked_files():
    this_file = Path(__file__).resolve()
    workflow = REPO_ROOT / ".github/workflows/ci.yml"
    yield from (p for p in shipped_files() if p != this_file)
    for path in sorted((REPO_ROOT / "test").glob("*.py")):
        if path != this_file:
            yield path
    yield workflow


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.skills = {p.parent.name for p in (REPO_ROOT / "skills").glob("*/SKILL.md")}
        self.scripts = {p.name for p in (REPO_ROOT / "scripts").glob("cortex_*.py")}
        self.templates = {p.name for p in (REPO_ROOT / "scripts").glob("*.tmpl")}
        self.agents = {p.stem for p in (REPO_ROOT / "agents").glob("*.md")}

    def texts(self):
        for path in shipped_files():
            yield path, path.read_text(encoding="utf-8", errors="replace")

    def test_the_expected_skills_ship(self):
        self.assertEqual(self.skills, {
            "init", "explore", "plan", "exec", "review", "end", "ingest", "status", "handoff",
            "commit", "commit-push-pr", "new-worktree", "install", "doctor",
        })

    def test_skill_names_match_their_directories(self):
        for path in (REPO_ROOT / "skills").glob("*/SKILL.md"):
            with self.subTest(skill=path.parent.name):
                match = re.search(r"^name:\s*(\S+)", path.read_text(), re.M)
                self.assertEqual(match.group(1), path.parent.name)

    def test_every_skill_reference_resolves(self):
        for path, text in self.texts():
            for name in SKILL_REF.findall(text) + SKILL_CALL.findall(text):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), ref=name):
                    self.assertIn(name, self.skills)

    def test_every_script_reference_resolves(self):
        for path, text in self.texts():
            for name in SCRIPT_REF.findall(text):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), ref=name):
                    self.assertIn(name, self.scripts)

    def test_every_reference_file_resolves(self):
        for path, text in self.texts():
            rel = path.relative_to(REPO_ROOT)
            for skill, name in REFERENCE_REF.findall(text):
                with self.subTest(file=str(rel), ref=f"{skill}/{name}"):
                    self.assertTrue((REPO_ROOT / "skills" / skill / "references" / name).is_file())
            if rel.parts[0] == "skills":
                for name in LOCAL_REFERENCE_REF.findall(text):
                    with self.subTest(file=str(rel), ref=name):
                        self.assertTrue((REPO_ROOT / rel.parts[0] / rel.parts[1] / "references" / name).is_file())

    def test_every_template_reference_resolves(self):
        for path, text in self.texts():
            for name in TEMPLATE_REF.findall(text):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), ref=name):
                    self.assertIn(name, self.templates)

    def test_agent_spawns_resolve(self):
        for path, text in self.texts():
            for name in re.findall(r'subagent_type:\s*"cortex:([a-z-]+)"', text):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), ref=name):
                    self.assertIn(name, self.agents)

    def test_references_never_use_a_substituted_variable(self):
        for path in (REPO_ROOT / "skills").glob("*/references/*.md"):
            with self.subTest(file=path.name):
                self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", path.read_text())

    def test_a_skill_with_plugin_root_in_references_defines_it(self):
        for refs in (REPO_ROOT / "skills").glob("*/references"):
            if any("<PLUGIN_ROOT>" in p.read_text() for p in refs.glob("*.md")):
                with self.subTest(skill=refs.parent.name):
                    self.assertIn("<PLUGIN_ROOT>", (refs.parent / "SKILL.md").read_text())

    def test_no_removed_concept_remains(self):
        for path, text in self.texts():
            if path.suffix == ".py" and path.name.startswith("test_"):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), line=number):
                    self.assertIsNone(RESIDUE.search(line), line[:160])

    def test_nothing_private_remains(self):
        for path in all_tracked_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), line=number):
                    self.assertIsNone(PRIVATE.search(line), line[:160])

    def test_manifests_are_neutral_and_licensed(self):
        plugin = json.loads((REPO_ROOT / ".claude-plugin/plugin.json").read_text())
        market = json.loads((REPO_ROOT / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(plugin["name"], "cortex")
        self.assertEqual(plugin["license"], "MIT")
        self.assertEqual(market["name"], "cortex-sdd")
        self.assertEqual(market["owner"]["name"], "cortex-sdd maintainers")
        self.assertTrue((REPO_ROOT / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
