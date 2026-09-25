"""Pure-Python behavior: splitting, Claude Code value semantics, spec helpers."""

import unittest

from helpers import SCRIPTS  # noqa: F401  (sets sys.path)
import claude_spec as spec
from claude_frontmatter import (claude_bool, claude_code_extract, claude_code_fallback_quote,
                                same_value, split_frontmatter, split_tool_rules)


def codes(split):
    return [f.code for f in split.findings]


class SplitTest(unittest.TestCase):
    def test_plain_file(self):
        split = split_frontmatter("---\nname: x\n---\n# Body\n")
        self.assertTrue(split.has_frontmatter)
        self.assertEqual(split.yaml_text, "name: x\n")
        self.assertEqual(split.body, "# Body\n")
        self.assertEqual(split.body_line, 4)
        self.assertEqual(codes(split), [])

    def test_bom_is_an_error(self):
        split = split_frontmatter("﻿---\nname: x\n---\n")
        self.assertIn("frontmatter.bom", codes(split))
        self.assertTrue(split.had_bom)

    def test_blank_line_before_opening(self):
        split = split_frontmatter("\n---\nname: x\n---\n")
        self.assertFalse(split.has_frontmatter)
        self.assertIn("frontmatter.not-first-line", codes(split))

    def test_unterminated(self):
        self.assertIn("frontmatter.unterminated", codes(split_frontmatter("---\nname: x\n")))

    def test_dashes_inside_value(self):
        split = split_frontmatter('---\ndescription: "a --- b"\n---\n')
        self.assertIn("frontmatter.dashes-inside", codes(split))
        # Claude Code's own regex stops at the first '---', mid-line.
        self.assertEqual(claude_code_extract('---\ndescription: "a --- b"\n---\n'), 'description: "a ')

    def test_crlf_normalized(self):
        split = split_frontmatter("---\r\nname: x\r\n---\r\nbody\r\n")
        self.assertEqual(split.newline, "\r\n")
        self.assertEqual(split.yaml_text, "name: x\n")
        self.assertIn("file.crlf", codes(split))

    def test_fallback_quote_matches_claude_code(self):
        self.assertEqual(claude_code_fallback_quote("argument-hint: [pr] [n]"), 'argument-hint: "[pr] [n]"')
        self.assertEqual(claude_code_fallback_quote('a: "kept"'), 'a: "kept"')
        self.assertEqual(claude_code_fallback_quote("a: plain"), "a: plain")


class SemanticsTest(unittest.TestCase):
    def test_claude_bool(self):
        for value, want in ((True, True), ("yes", True), ("ON", True), (1, True),
                            ("no", False), ("off", False), (0, False), ("maybe", None), (2, None)):
            self.assertEqual(claude_bool(value), want, value)

    def test_split_tool_rules_like_claude_code(self):
        self.assertEqual(split_tool_rules("Read, Grep  Bash(git diff *)"), ["Read", "Grep", "Bash(git diff *)"])
        self.assertEqual(split_tool_rules(["Read Grep", "Bash(a, b)"]), ["Read", "Grep", "Bash(a, b)"])
        self.assertIsNone(split_tool_rules({"x": 1}))
        self.assertEqual(split_tool_rules(None), [])

    def test_same_value(self):
        self.assertTrue(same_value({"a": 1, "b": [1.0]}, {"b": [1], "a": 1}))
        self.assertFalse(same_value({"a": True}, {"a": 1}))
        self.assertFalse(same_value("1", 1))


class SpecTest(unittest.TestCase):
    def test_near_miss_fields(self):
        self.assertEqual(spec.suggest_field("allowed_tools", spec.SKILL_FIELDS, spec.AGENT_FIELDS),
                         ("allowed-tools", False))
        self.assertEqual(spec.suggest_field("desciption", spec.SKILL_FIELDS, spec.AGENT_FIELDS),
                         ("description", False))
        self.assertEqual(spec.suggest_field("tools", spec.SKILL_FIELDS, spec.AGENT_FIELDS),
                         ("allowed-tools", True))
        self.assertEqual(spec.suggest_field("allowed-tools", spec.AGENT_FIELDS, spec.SKILL_FIELDS),
                         ("tools", True))
        self.assertEqual(spec.suggest_field("x-custom", spec.SKILL_FIELDS, spec.AGENT_FIELDS), (None, False))

    def test_plugin_mcp_prefix(self):
        self.assertEqual(spec.plugin_mcp_prefix("my plugin", "db.tools"), "mcp__plugin_my_plugin_db_tools__")

    def test_models(self):
        self.assertTrue(spec.DATED_MODEL_RE.match("claude-sonnet-4-5-20250929"))
        self.assertFalse(spec.DATED_MODEL_RE.match("claude-opus-5-5"))
        self.assertTrue(spec.FULL_MODEL_RE.match("claude-opus-5-5[1m]"))


if __name__ == "__main__":
    unittest.main()
