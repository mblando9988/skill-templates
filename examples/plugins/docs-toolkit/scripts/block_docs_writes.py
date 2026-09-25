#!/usr/bin/env python3
"""
block_docs_writes.py - PreToolUse hook: deny write tools on the docs MCP server.

Part of the docs-toolkit plugin (built from templates/hook-script-template.py).

Claude Code sends the event as JSON on stdin. This hook answers with a JSON
permission decision (exit 0), which also works when other hooks allow the
call. It fails closed: unreadable input is denied with exit 2.

Exit Codes:
    0 - Decision printed on stdout (deny for write tools, nothing otherwise)
    2 - Input unreadable; blocked to be safe
"""

import json
import sys

WRITE_TOOLS = ("write_file", "edit_file", "move_file", "create_directory")
PREFIX = "mcp__plugin_docs-toolkit_docs__"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024).decode("utf-8"))
        tool = payload["tool_name"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        print("block_docs_writes: unreadable hook input; blocked to be safe", file=sys.stderr)
        return 2
    if isinstance(tool, str) and tool.startswith(PREFIX) and tool[len(PREFIX):] in WRITE_TOOLS:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "The docs server is read-only in this plugin. Edit files with the "
                                        "normal Edit tool so the change goes through review.",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
