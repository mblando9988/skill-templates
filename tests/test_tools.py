"""check_docs_safety.py and export_skill.py end to end."""

import json
import unittest
import zipfile

from helpers import ROOT, TempDirTest, needs_parsers, run, run_json


@needs_parsers
class SafetyTest(TempDirTest):
    def test_flags_unsafe_examples_in_markdown(self):
        doc = self.write("docs/guide.md", "# Guide\n\n```yaml\nhooks:\n  PreToolUse:\n    - matcher: Bash\n"
                         "      hooks:\n        - type: command\n          command: \"./check.sh $TOOL_INPUT\"\n"
                         "```\n\n```json\n{\"hooks\": {\"PostToolUse\": [{\"hooks\": [{\"type\": \"command\", "
                         "\"command\": \"jq -r .tool_input.command | sh\"}]}]}}\n```\n")
        code, result = run_json("check_docs_safety.py", doc)
        self.assertEqual(code, 10)
        found = {f["code"] for f in result["data"]["findings"]}
        self.assertTrue({"hook.payload-var", "hook.exec-payload"} <= found, found)

    def test_repository_is_clean(self):
        code, out, err = run("check_docs_safety.py", ROOT / "skills", ROOT / "examples", ROOT / "templates",
                             ROOT / "references", ROOT / "README.md", "--strict")
        self.assertEqual(code, 0, out + err)


@needs_parsers
class ExportTest(TempDirTest):
    def test_fills_the_gap_for_portable_targets(self):
        out = self.tmp / "dist"
        code, result = run_json("export_skill.py", ROOT / "examples/skills/release-notes", "--out", out, "--zip")
        self.assertEqual(code, 0, result["errors"])
        data = result["data"]["frontmatter"]
        self.assertEqual(set(data), {"name", "description", "allowed-tools"})
        self.assertIn("Examples: 'write release notes", data["description"])
        self.assertTrue(any(c.startswith("argument-hint: dropped") for c in result["data"]["changes"]))
        self.assertTrue((out / "release-notes/scripts/group_commits.py").is_file())
        with zipfile.ZipFile(out / "release-notes.zip") as zf:
            self.assertIn("release-notes/SKILL.md", zf.namelist())
        code, check = run_json("validate_skill.py", out / "release-notes", "--target", "portable")
        self.assertFalse([e for e in check["errors"] if "[name.directory]" not in e])

    def test_keep_claude_fields(self):
        code, result = run_json("export_skill.py", ROOT / "examples/skills/deep-review", "--out", self.tmp,
                                "--keep-claude-fields")
        self.assertEqual(code, 0, result["errors"])
        metadata = result["data"]["frontmatter"]["metadata"]
        self.assertEqual(json.loads(metadata["claude-code-model"]), "opus")

    def test_refuses_description_over_the_limit(self):
        skill = self.write("s/demo/SKILL.md", '---\nname: demo\ndescription: "Use when ' + "x" * 1000
                           + '"\nwhen_to_use: "' + "y" * 100 + '"\n---\n# Demo\n')
        code, result = run_json("export_skill.py", skill.parent, "--out", self.tmp / "dist")
        self.assertEqual(code, 10)
        self.assertFalse((self.tmp / "dist" / "demo").exists())


if __name__ == "__main__":
    unittest.main()
