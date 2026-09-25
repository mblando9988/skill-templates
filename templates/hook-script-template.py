#!/usr/bin/env python3
"""
{{SCRIPT_NAME}}.py - {{EVENT}} hook: {{what it enforces}}

Part of the {{SKILL_OR_PLUGIN_NAME}} {{skill|plugin|agent}}.

Wire it up in a subagent's frontmatter, so the rule holds only while that
agent works (a skill runs in it with context: fork + agent: <name>). For
plugins use hooks/hooks.json and "${CLAUDE_PLUGIN_ROOT}/scripts/{{SCRIPT_NAME}}.py":

    hooks:
      {{EVENT}}:
        - matcher: {{ToolName}}
          hooks:
            - type: command
              command: python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/{{skill-name}}/scripts/{{SCRIPT_NAME}}.py"
              timeout: 10

Contract (Claude Code hooks):
- Input:  the event as JSON on stdin: hook_event_name, tool_name, tool_input,
          cwd, session_id, permission_mode, ... There is NO $TOOL_INPUT variable.
- Block:  exit 2 with the reason on stderr (Claude sees it), or exit 0 and
          print a JSON decision (see DECISION below).
- Allow:  exit 0 with no output. The normal permission flow still applies.
- Exit 1 or any other code: a NON-blocking error. The action proceeds.
- PermissionRequest ignores exit 2: only a JSON "decision" object denies
  there, and respond() prints one for you.
- Hooks run in the session's working directory. Never rely on relative paths.

Hardening built in:
- Reads at most MAX_INPUT_BYTES and fails closed on missing, oversized or
  malformed input (for gate events, an unreadable request is blocked).
- Never passes payload text to a shell, eval or format string: decide()
  only inspects data.
- Stdlib only, no network, bounded work, so it cannot hang the session.

Test it:
    echo '{"hook_event_name":"{{EVENT}}","tool_name":"{{ToolName}}","tool_input":{}}' \
        | python3 {{SCRIPT_NAME}}.py; echo "exit $?"

Exit Codes:
    0 - Allow, or a JSON decision printed on stdout
    2 - Block (the reason is on stderr)
"""

import json
import sys
from typing import Any, Dict, Optional, Tuple

MAX_INPUT_BYTES = 1024 * 1024
EXIT_ALLOW = 0
EXIT_BLOCK = 2

# Use exit 2 (simple, works on every version) or a JSON decision (can also
# "ask" the user, or explain a denial). DECISION selects the style for
# PreToolUse; PermissionRequest always gets a JSON decision.
DECISION = "exit"          # "exit" | "json"


# ===========================================================================
# POLICY: edit this function
# ===========================================================================

def decide(event: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return ("allow" | "deny" | "ask", reason).

    event is the parsed stdin payload. Treat every value as untrusted text:
    inspect it, never execute it.
    """
    if event.get("tool_name") != "{{ToolName}}":
        return "allow", None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "deny", "tool call without tool_input"
    # {{Example: block writes outside the project}}
    # path = tool_input.get("file_path")
    # if isinstance(path, str) and not path.startswith(event.get("cwd", "")):
    #     return "deny", f"{path} is outside the project"
    return "allow", None


# ===========================================================================
# PLUMBING: usually no need to edit below
# ===========================================================================

def read_event() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return None, "hook input is too large"
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "hook input is not valid JSON"
    if not isinstance(event, dict):
        return None, "hook input is not a JSON object"
    return event, None


def respond(event_name: str, verdict: str, reason: Optional[str]) -> int:
    if verdict == "allow":
        return EXIT_ALLOW
    reason = reason or "blocked by {{SCRIPT_NAME}}"
    if event_name == "PermissionRequest":
        # Exit 2 is not honored here; only a decision object denies. Printing
        # nothing leaves the prompt to the user, which is what "ask" means.
        if verdict == "deny":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event_name,
                "decision": {"behavior": "deny", "message": reason},
            }}))
        return EXIT_ALLOW
    if DECISION == "json" and event_name == "PreToolUse":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": verdict,
            "permissionDecisionReason": reason,
        }}))
        return EXIT_ALLOW
    if verdict == "ask":
        return EXIT_ALLOW          # exit codes cannot ask; use DECISION = "json"
    print(f"{{SCRIPT_NAME}}: {reason}", file=sys.stderr)
    return EXIT_BLOCK


def main() -> int:
    event, problem = read_event()
    if event is None:
        # Fail closed: a guard that cannot read its input must not wave the call through.
        # (PermissionRequest ignores exit 2, so the normal permission prompt decides: also safe.)
        print(f"{{SCRIPT_NAME}}: {problem}; blocked to be safe", file=sys.stderr)
        return EXIT_BLOCK
    try:
        verdict, reason = decide(event)
    except Exception as exc:  # a bug in the policy must not silently allow
        print(f"{{SCRIPT_NAME}}: policy error ({type(exc).__name__}); blocked to be safe", file=sys.stderr)
        return EXIT_BLOCK
    return respond(str(event.get("hook_event_name", "PreToolUse")), verdict, reason)


if __name__ == "__main__":
    sys.exit(main())
