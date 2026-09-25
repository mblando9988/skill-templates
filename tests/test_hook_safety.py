"""hook_safety.py: static checks on hook commands."""

import os
import unittest

from helpers import TempDirTest
from hook_safety import HookContext, check_command, check_handler


def codes(issues):
    return [code for _severity, code, _message, _hint in issues]


class CommandTest(TempDirTest):
    def ctx(self, **kw):
        return HookContext(kind="skill", **kw)

    def test_payload_variables_do_not_exist(self):
        self.assertIn("hook.payload-var", codes(check_command("./check.sh $TOOL_INPUT", None, "PreToolUse", self.ctx())))
        self.assertIn("hook.payload-var", codes(check_command('x "${TOOL_INPUT}"', None, "PreToolUse", self.ctx())))

    def test_interpolation_and_exec(self):
        self.assertIn("hook.payload-interpolation",
                      codes(check_command("prettier --write ${tool_input.file_path}", None, "PostToolUse", self.ctx())))
        for command in ("jq -r .tool_input.command | sh", "eval \"$(cat)\"", 'bash -c "$(jq -r .x)"'):
            self.assertIn("hook.exec-payload", codes(check_command(command, None, "PreToolUse", self.ctx())), command)

    def test_paths(self):
        self.assertIn("hook.relative-path", codes(check_command("./scripts/x.sh", None, "Stop", self.ctx())))
        self.assertIn("hook.skill-dir", codes(check_command('python3 "${CLAUDE_SKILL_DIR}/x.py"', None, "Stop", self.ctx())))
        self.assertIn("hook.unquoted-placeholder",
                      codes(check_command("python3 ${CLAUDE_PROJECT_DIR}/x.py", None, "Stop", self.ctx())))
        self.assertNotIn("hook.unquoted-placeholder",
                         codes(check_command('python3 "${CLAUDE_PROJECT_DIR}/x.py"', None, "Stop", self.ctx())))

    def test_missing_and_non_executable_scripts(self):
        project = self.tmp
        (project / ".claude").mkdir()
        ctx = self.ctx(project_dir=project, base_dir=project / ".claude" / "skills" / "s")
        self.assertIn("hook.script-missing",
                      codes(check_command('python3 "${CLAUDE_PROJECT_DIR}/nope.py"', None, "Stop", ctx)))
        script = self.write("run.sh", "#!/bin/sh\nexit 0\n", mode=0o644)
        issues = codes(check_command('"${CLAUDE_PROJECT_DIR}/run.sh"', None, "Stop", ctx))
        if os.name != "nt":
            self.assertIn("hook.script-not-executable", issues)
        self.assertTrue(script.exists())

    def test_gate_script_that_cannot_block(self):
        self.write("g.py", "import sys\nsys.exit(1)\n")
        ctx = self.ctx(project_dir=self.tmp)
        self.assertIn("hook.cannot-block", codes(check_command('python3 "${CLAUDE_PROJECT_DIR}/g.py"', None, "PreToolUse", ctx)))
        self.write("h.py", "import sys\nsys.exit(2)\n")
        self.assertNotIn("hook.cannot-block", codes(check_command('python3 "${CLAUDE_PROJECT_DIR}/h.py"', None, "PreToolUse", ctx)))

    def test_installed_skill_path_resolves_to_known_skill(self):
        skill = self.tmp / "skills" / "guard"
        self.write("skills/guard/scripts/g.py", "import sys\nsys.exit(2)\n")
        ctx = HookContext(kind="agent", base_dir=self.tmp / "agents", skill_dirs={"guard": skill})
        self.assertEqual(codes(check_command('python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/guard/scripts/g.py"',
                                             None, "PreToolUse", ctx)), [])
        self.assertIn("hook.script-missing", codes(check_command(
            'python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/guard/scripts/missing.py"', None, "PreToolUse", ctx)))

    def test_exec_form_and_async(self):
        self.assertEqual(codes(check_command("python3", ["${CLAUDE_PLUGIN_ROOT}/x.py"], "PostToolUse",
                                             HookContext(kind="docs"))), [])
        self.assertIn("hook.async-gate",
                      codes(check_handler({"type": "command", "command": "true", "async": True}, "PreToolUse",
                                          HookContext(kind="docs"))))


if __name__ == "__main__":
    unittest.main()
