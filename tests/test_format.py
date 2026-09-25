"""format_skill.py end to end: repairs, idempotence, refusal, atomic writes."""

import os
import stat
import unittest

from helpers import ENGINES, TempDirTest, needs_parsers, run, run_json
from claude_frontmatter import split_frontmatter


@needs_parsers
class FormatTest(TempDirTest):
    def fmt(self, *args):
        return run("format_skill.py", *args)

    def test_repairs_and_canonical_layout(self):
        path = self.write(".claude/skills/demo/SKILL.md",
                          "﻿---\r\nuser-invocable: yes\r\ndescription: Use when you need to wait...\r\n"
                          "argument-hint: [pr] [priority]\r\nallowed-tools: Read,Grep\r\nname: demo   # keep me\r\n"
                          "---\r\n\r\n\r\n# Demo  \r\n\r\n\r\n```\r\nkeep   \r\n```\r\n", mode=0o640)
        code, _out, err = self.fmt(path)
        self.assertEqual(code, 0, err)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: demo # keep me\ndescription: \"Use when you need to wait...\"\n"))
        self.assertIn('argument-hint: "[pr] [priority]"', text)
        self.assertIn("user-invocable: true", text)
        self.assertIn("allowed-tools: Read, Grep", text)
        self.assertIn("```\nkeep   \n```\n", text)            # code blocks untouched
        self.assertNotIn("\r", text)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        # Both parsers now read the same mapping.
        (npm, native), = ENGINES.parse_many([split_frontmatter(text).yaml_text], positions=False)
        self.assertEqual(npm.value, native.value)
        # Idempotent.
        self.assertEqual(self.fmt(path, "--check")[0], 0)

    def test_check_and_diff_do_not_write(self):
        path = self.write("a/agents/r.md", "---\ndescription: Use when x\nname: r\n---\nYou review.\n")
        before = path.read_text()
        self.assertEqual(self.fmt(path, "--check")[0], 10)
        code, out, _err = self.fmt(path, "--diff")
        self.assertEqual(code, 10)
        self.assertIn('+description: "Use when x"', out)
        self.assertEqual(path.read_text(), before)

    def test_refuses_what_it_cannot_fix(self):
        path = self.write(".claude/skills/demo/SKILL.md", "---\nname: a\nname: b\n---\nx\n")
        before = path.read_bytes()
        code, result = run_json("format_skill.py", path)
        self.assertEqual(code, 1)
        self.assertEqual(result["data"]["files"][0]["status"], "error")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
