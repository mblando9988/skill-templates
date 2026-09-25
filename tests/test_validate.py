"""validate_skill.py end to end, with both of Claude Code's YAML parsers."""

import unittest

from helpers import ROOT, TempDirTest, codes, needs_parsers, run_json

SKILL = """---
name: demo
description: "Use when testing the validator"
{extra}---

# Demo

Body.
"""


@needs_parsers
class ValidateTest(TempDirTest):
    def skill(self, extra="", name="demo"):
        return self.write(f".claude/skills/{name}/SKILL.md", SKILL.replace("demo", name).format(extra=extra))

    def check(self, *args):
        return run_json("validate_skill.py", *args)

    def test_clean_skill_passes(self):
        self.skill()
        code, result = self.check(self.tmp / ".claude", "--strict")
        self.assertEqual(code, 0, result["errors"])

    def test_native_build_split_is_reported(self):
        self.write(".claude/skills/demo/SKILL.md",
                   "---\nname: demo\ndescription: Use when you need to wait...\nmodel: sonnet\n---\n# D\n")
        code, result = self.check(self.tmp / ".claude")
        self.assertEqual(code, 10)
        self.assertIn("yaml.native-split", codes(result))

    def test_silently_ignored_fields(self):
        self.skill("allowed_tools: Read\ntools: Read\ndisable-model-invocation: yes\n")
        _code, result = self.check(self.tmp / ".claude")
        found = codes(result)
        for code in ("field.misspelled", "field.wrong-kind", "field.boolean-spelling"):
            self.assertIn(code, found)

    def test_tools_and_models(self):
        self.skill("allowed-tools: Bash, Grepp, bash, Write(src/**), Bash(git:* push)\n"
                   "model: claude-sonnet-4-5-20250929\n")
        found = codes(self.check(self.tmp / ".claude")[1])
        for code in ("tools.unscoped-shell", "tools.misspelled", "tools.case", "tools.path-rule-ignored",
                     "tools.prefix-position", "model.dated"):
            self.assertIn(code, found)

    def test_hook_mistakes(self):
        self.skill("hooks:\n  pretooluse:\n    - matcher: Bash(git *)\n      hooks:\n"
                   "        - type: command\n          command: ./x.sh $TOOL_INPUT\n          timeout: \"30\"\n"
                   "  Stop:\n    - hooks:\n        - type: command\n          command: echo hi\n"
                   "          if: Bash(rm *)\n")
        found = codes(self.check(self.tmp / ".claude")[1])
        for code in ("hooks.event-misspelled", "hooks.matcher-rule", "hook.payload-var", "hooks.timeout",
                     "hooks.if-never-runs", "hooks.session-scope"):
            self.assertIn(code, found)

    def test_skill_hooks_with_once_are_not_session_scoped(self):
        self.skill("hooks:\n  SessionStart:\n    - hooks:\n        - type: command\n"
                   "          command: echo ready\n          once: true\n")
        self.assertNotIn("hooks.session-scope", codes(self.check(self.tmp / ".claude")[1]))

    def test_agent_rules(self):
        self.skill(name="guard")
        self.write(".claude/agents/a.md",
                   "---\nname: a\ndescription: Use when testing\nallowed-tools: Read\ntools: Readd\n"
                   "maxTurns: \"5\"\npermissionMode: yolo\nskills:\n  - guard\n---\nYou test.\n")
        found = codes(self.check(self.tmp / ".claude")[1])
        for code in ("field.wrong-kind", "tools.misspelled", "field.type", "field.value", "agent.zero-tools"):
            self.assertIn(code, found)

    def test_plugin_rules(self):
        self.write("p/.claude-plugin/plugin.json", '{"name": "p"}')
        self.write("p/.mcp.json", '{"mcpServers": {"db": {"command": "db-server"}}}')
        self.write("p/agents/r.md", "---\nname: r\ndescription: Use when testing\ntools: Read, mcp__db__query\n"
                   "mcpServers:\n  - github\n---\nYou test.\n")
        found = codes(self.check(self.tmp / "p")[1])
        self.assertIn("tools.plugin-mcp-name", found)
        self.assertIn("agent.plugin-ignored", found)

    def test_portable_target(self):
        self.skill("when_to_use: \"Examples: x\"\nmodel: sonnet\n")
        found = codes(self.check(self.tmp / ".claude/skills/demo", "--target", "portable")[1])
        self.assertIn("portable.field", found)

    def test_repository_examples_are_clean(self):
        code, result = self.check(ROOT / "examples", ROOT / "skills", ROOT, "--strict")
        self.assertEqual(code, 0, result["errors"] + result["warnings"])

    def test_templates_are_clean_apart_from_placeholders(self):
        for args in ((ROOT / "templates/skill-md-template.md", "--kind", "skill"),
                     (ROOT / "templates/agent-md-template.md", "--kind", "agent"),
                     (ROOT / "templates/plugin",)):
            code, result = self.check(*args, "--allow-placeholders", "--strict")
            self.assertEqual(code, 0, (args, result["errors"] + result["warnings"]))


if __name__ == "__main__":
    unittest.main()
