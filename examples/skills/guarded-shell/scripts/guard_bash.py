#!/usr/bin/env python3
"""
guard_bash.py - PreToolUse hook that blocks destructive Bash commands.

Part of the guarded-shell skill (built from templates/hook-script-template.py).

Contract (Claude Code hooks):
- Input: the event as JSON on stdin, e.g.
  {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "..."}}
- Block: exit 2 with the reason on stderr. Claude sees the reason.
- Allow: exit 0 with no output. The normal permission flow still applies.
- Exit 1 does NOT block; Claude Code treats it as a non-blocking error.

Hardening:
- Never executes, evals or interpolates the command; it only matches text.
- Fails closed: missing, oversized or malformed input blocks the call.
- Stdlib only, no network, no files written.

Usage (Claude Code runs this; to test by hand):
    echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | python3 guard_bash.py

Exit Codes:
    0 - Allow (not a Bash call, or nothing dangerous found)
    2 - Block (dangerous command, or unreadable input)
"""

import json
import re
import sys
from typing import List, Optional, Tuple

MAX_INPUT_BYTES = 1024 * 1024
EXIT_ALLOW = 0
EXIT_BLOCK = 2

# (pattern, reason). Patterns match the raw command text, case-insensitively.
RULES: List[Tuple[str, str]] = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|--recursive\s+--force|--force\s+--recursive)\b[^;&|]*"
     r"(\s/(\s|$)|\s~/?(\s|$)|\s\*|\s\.\.?/?(\s|$)|\s/\*)", "recursive force delete of a root, home or wildcard path"),
    (r"\bgit\s+push\b(?![^;&|]*--force-with-lease)[^;&|]*(\s--force\b|\s-f\b|\s\+\S)",
     "force push without --force-with-lease"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard discards uncommitted work"),
    (r"\bgit\s+clean\s+-[a-z]*f[a-z]*d|\bgit\s+clean\s+-[a-z]*d[a-z]*f", "git clean -fd deletes untracked files"),
    (r"\b(drop\s+(table|database|schema)|truncate\s+table)\b", "destructive SQL"),
    (r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "piping a download into a shell"),
    (r"(^|[;&|(\s])eval\s", "eval runs unreviewed code"),
    (r"\b(ba|z|da)?sh\s+-c\s+[\"']?\$\(", "sh -c with command substitution"),
    (r"\bchmod\s+(-R\s+)?0?777\b", "world-writable permissions"),
    (r"\bmkfs(\.\w+)?\b|\bdd\b[^;&|]*\bof=/dev/", "writes to a raw device"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
]


def decide(payload: object) -> Optional[str]:
    """Return a block reason, or None to allow."""
    if not isinstance(payload, dict):
        return "hook input is not a JSON object"
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return "Bash call without a command string"
    for pattern, reason in RULES:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        print("guard_bash: hook input too large; blocked to be safe", file=sys.stderr)
        return EXIT_BLOCK
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        print("guard_bash: hook input is not valid JSON; blocked to be safe", file=sys.stderr)
        return EXIT_BLOCK
    reason = decide(payload)
    if reason:
        print(f"Blocked by guarded-shell: {reason}. Explain the risk to the user and ask how to proceed; "
              "do not rephrase the command to get past this check.", file=sys.stderr)
        return EXIT_BLOCK
    return EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
