#!/usr/bin/env python3
"""
hook_safety.py - Static checks for Claude Code hook handlers.

Part of the skill-templates tooling: validate_skill.py checks the hooks in
the files it validates, and check_docs_safety.py also checks hook examples
inside markdown and hooks.json files. Library module, no CLI.

What goes wrong with hooks, and what each check catches:

    hook.payload-var            $TOOL_INPUT and friends do not exist. Hooks get
                                the event as JSON on stdin; the variable is
                                empty, or attacker-controlled if something sets it.
    hook.payload-interpolation  ${tool_input.file_path} is never substituted in
                                command hooks (only mcp_tool `input` does that).
    hook.exec-payload           eval, `| sh`, `sh -c "$(...)"`, `xargs sh -c`
                                execute text that came from the tool call.
    hook.relative-path          hooks run in the session's current directory,
                                which moves when Claude runs cd.
    hook.skill-dir              ${CLAUDE_SKILL_DIR} is substituted in skill
                                content and allowed-tools, not in hook commands.
    hook.unquoted-placeholder   an unquoted ${CLAUDE_PROJECT_DIR} path breaks on
                                spaces in shell form.
    hook.script-missing         a hook whose script cannot start exits 127 - a
                                non-blocking error, so the gate is silently off.
    hook.cannot-block           a gate script that never exits 2 or prints a
                                deny decision cannot block anything (exit 1
                                does not block).
    hook.async-gate             async hooks cannot block.

Every check returns plain (severity, code, message, hint) tuples; callers
attach file paths and line numbers.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from claude_spec import GATE_EVENTS
except ImportError:  # pragma: no cover - imported from another directory
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from claude_spec import GATE_EVENTS

Issue = Tuple[str, str, str, Optional[str]]   # (severity, code, message, hint)

_PAYLOAD_VARS_RE = re.compile(
    r"\$\{?(TOOL_INPUT|TOOL_OUTPUT|TOOL_NAME|TOOL_RESULT|TOOL_RESPONSE|HOOK_INPUT|HOOK_EVENT|"
    r"CLAUDE_TOOL_INPUT|CLAUDE_TOOL_OUTPUT|CLAUDE_TOOL_NAME|CLAUDE_FILE_PATHS|CLAUDE_FILE_PATH|"
    r"ARGUMENTS)\b\}?")
_INTERPOLATION_RE = re.compile(
    r"(\$\{\s*(tool_input|tool_response|tool_name|prompt|cwd|session_id)[^}]*\}"
    r"|\{\{\s*(tool_input|tool_response|tool_name)[^}]*\}\})")
_EVAL_RE = re.compile(r"(^|[;&|(\s])eval(\s|$)")
_PIPE_SHELL_RE = re.compile(r"\|\s*(sudo\s+)?(sh|bash|zsh|dash|ksh|source|\.)(\s|$)")
_SHELL_C_SUBST_RE = re.compile(r"\b(sh|bash|zsh|dash)\s+-c\s+[\"']?[^\"']*\$\(")
_XARGS_SHELL_RE = re.compile(r"\bxargs\b[^|;&]*\b(sh|bash|zsh|dash)\s+-c\b")
_PLACEHOLDER_RE = re.compile(r"\$\{?(CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA|CLAUDE_SKILL_DIR)\}?")
_INTERPRETERS = {"python", "python3", "node", "bun", "deno", "bash", "sh", "zsh", "dash", "ruby",
                 "perl", "pwsh", "powershell", "php", "tsx", "ts-node"}
_SCRIPT_EXTENSIONS = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".php")
_MAX_SCRIPT_BYTES = 512 * 1024


@dataclass
class HookContext:
    """Where a hook lives, so script paths can be resolved.

    kind:        "skill" | "command" | "agent" | "settings" | "plugin-hooks" | "docs"
    base_dir:    directory of the file declaring the hook (a skill's directory)
    project_dir: best guess of ${CLAUDE_PROJECT_DIR} (None when unknown)
    plugin_root: plugin root when the hook ships in a plugin
    """
    kind: str
    base_dir: Optional[Path] = None
    project_dir: Optional[Path] = None
    plugin_root: Optional[Path] = None
    skill_dirs: Dict[str, Path] = field(default_factory=dict)   # known skills, for .claude/skills/<name>/ paths


def guess_project_dir(file_path: Path) -> Optional[Path]:
    """The directory that contains `.claude/`, when the file lives under one."""
    parts = file_path.resolve().parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == ".claude":
            return Path(*parts[:index]) if index else None
    return None


def check_handler(handler: Dict[str, Any], event: str, ctx: HookContext) -> List[Issue]:
    """Safety checks for one hook handler dict (schema checks live in validate_skill.py)."""
    issues: List[Issue] = []
    kind = handler.get("type")
    if handler.get("async") is True and event in GATE_EVENTS:
        issues.append(("warning", "hook.async-gate",
                       f"async {event} hook: Claude Code does not wait for it, so it cannot block",
                       "drop async: true, or move the check to a non-gating event"))
    if kind == "command":
        command = handler.get("command")
        args = handler.get("args")
        if isinstance(command, str):
            issues.extend(check_command(command, args if isinstance(args, list) else None, event, ctx))
    elif kind in ("prompt", "agent"):
        prompt = handler.get("prompt")
        if isinstance(prompt, str) and _PAYLOAD_VARS_RE.search(prompt.replace("$ARGUMENTS", "")):
            issues.append(("warning", "hook.payload-var",
                           "prompt hooks receive the event through $ARGUMENTS only; other $TOOL_* "
                           "variables stay literal", "use $ARGUMENTS"))
    elif kind == "http":
        url = handler.get("url")
        if isinstance(url, str) and url.startswith("http://") and not re.match(
                r"http://(localhost|127\.0\.0\.1|\[::1\])([:/]|$)", url):
            issues.append(("warning", "hook.http-plaintext",
                           "http hook sends every event payload over plain HTTP", "use an https:// URL"))
        headers = handler.get("headers")
        if isinstance(headers, dict):
            for name, value in headers.items():
                if isinstance(value, str) and _looks_like_secret(str(name), value):
                    issues.append(("warning", "hook.literal-secret",
                                   f"header '{name}' holds a literal credential",
                                   "use $VAR interpolation and list the variable in allowedEnvVars"))
    return issues


def check_command(command: str, args: Optional[Sequence[Any]], event: str, ctx: HookContext) -> List[Issue]:
    """Check a command hook's `command` (shell form) or `command` + `args` (exec form)."""
    issues: List[Issue] = []
    text = command if args is None else " ".join([command] + [str(a) for a in args])

    match = _PAYLOAD_VARS_RE.search(text)
    if match:
        issues.append(("error", "hook.payload-var",
                       f"${match.group(1)} is not set for command hooks: Claude Code passes the event "
                       "as JSON on stdin, so this expands to nothing (or to whatever a caller sets)",
                       "read stdin in the script, e.g. json.load(sys.stdin)['tool_input']"))
    match = _INTERPOLATION_RE.search(text)
    if match:
        issues.append(("error", "hook.payload-interpolation",
                       f"'{match.group(0)}' is never substituted in command hooks, and splicing tool "
                       "input into a shell command would let the tool call inject shell code",
                       "parse the JSON on stdin inside the script instead"))
    if args is None:
        for pattern, what in ((_EVAL_RE, "eval"), (_PIPE_SHELL_RE, "a pipe into a shell"),
                              (_SHELL_C_SUBST_RE, "sh -c with command substitution"),
                              (_XARGS_SHELL_RE, "xargs ... sh -c")):
            if pattern.search(command):
                issues.append(("error", "hook.exec-payload",
                               f"command uses {what}, which executes text that can come from the "
                               "tool call", "parse stdin in a script and pass values as arguments"))
                break

    words, parse_error = _words(command, args)
    if parse_error:
        issues.append(("error", "hook.command-syntax",
                       f"shell cannot parse this command ({parse_error}); the hook fails to start",
                       "fix the quoting"))
        return issues

    if "CLAUDE_SKILL_DIR" in text:
        issues.append(("warning", "hook.skill-dir",
                       "${CLAUDE_SKILL_DIR} is substituted in skill content and allowed-tools, not in "
                       "hook commands; if it stays unset the script is not found, the hook fails to "
                       "start, and the gate is silently off",
                       "use \"${CLAUDE_PROJECT_DIR}/.claude/skills/<name>/scripts/...\" (project "
                       "skills) or \"${CLAUDE_PLUGIN_ROOT}/...\" (plugins)"))
    if args is None:
        for m in _PLACEHOLDER_RE.finditer(command):
            if not _inside_double_quotes(command, m.start()):
                issues.append(("warning", "hook.unquoted-placeholder",
                               f"{m.group(0)} is not in double quotes; a path with spaces splits into "
                               "several arguments", f"write \"{m.group(0)}/...\" or use exec form (args)"))
                break

    script, direct = _script_token(words)
    if script is None:
        return issues
    if _is_relative(script):
        issues.append(("warning", "hook.relative-path",
                       f"'{script}' is relative: hooks run in the session's current directory, which "
                       "moves when Claude runs cd",
                       "anchor it: \"${CLAUDE_PROJECT_DIR}/...\" or, in a plugin, \"${CLAUDE_PLUGIN_ROOT}/...\""))
    resolved = _resolve(script, ctx)
    if resolved is None:
        return issues
    path, base_note = resolved
    if not path.exists():
        in_skill = ctx.base_dir is not None and _is_relative(script) and (ctx.base_dir / script).exists()
        issues.append(("error", "hook.script-missing",
                       f"hook script '{script}' does not exist{base_note}; the hook fails to start "
                       "(a non-blocking error), so it silently stops guarding"
                       + (". The file is in the skill directory, but hooks do not run from there"
                          if in_skill else ""),
                       "point the command at the real path"))
        return issues
    if direct and path.is_file() and os.name != "nt" and not os.access(path, os.X_OK):
        issues.append(("error", "hook.script-not-executable",
                       f"'{script}' is run directly but is not executable; the hook fails to start",
                       f"chmod +x {path.name}, or run it through its interpreter (python3 {path.name})"))
    if event in GATE_EVENTS and path.is_file():
        verdict = _script_can_block(path, event)
        if verdict is False and event == "PermissionRequest":
            issues.append(("warning", "hook.cannot-block",
                           f"PermissionRequest hook script '{path.name}' never prints a decision "
                           "object, so it cannot deny anything: this event ignores exit 2 and "
                           "permissionDecision",
                           "print {\"hookSpecificOutput\": {\"hookEventName\": \"PermissionRequest\", "
                           "\"decision\": {\"behavior\": \"deny\", \"message\": ...}}} and exit 0"))
        elif verdict is False:
            issues.append(("warning", "hook.cannot-block",
                           f"{event} hook script '{path.name}' never exits 2 or prints a permission "
                           "decision, so it cannot block anything; exit 1 is a non-blocking error "
                           "and the action proceeds",
                           "exit 2 with the reason on stderr, or print {\"hookSpecificOutput\": "
                           "{\"hookEventName\": ..., \"permissionDecision\": \"deny\", ...}}"))
    return issues


def _words(command: str, args: Optional[Sequence[Any]]) -> Tuple[List[str], Optional[str]]:
    if args is not None:
        return [command] + [str(a) for a in args], None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer), None
    except ValueError as exc:
        return [], str(exc)


def _script_token(words: List[str]) -> Tuple[Optional[str], bool]:
    """Return (script path token, run directly?) for the first simple command."""
    index = 0
    while index < len(words) and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[index])
                                  or words[index] in ("exec", "env", "command", "nohup")):
        index += 1
    if index >= len(words):
        return None, False
    first = words[index]
    name = Path(first).name
    base = re.sub(r"[0-9.]+$", "", name)
    if base in _INTERPRETERS:
        for token in words[index + 1:]:
            if token in ("&&", "||", ";", "|", "&"):
                return None, False
            if token.startswith("-"):
                if token in ("-c", "-e", "-m"):
                    return None, False
                continue
            return (token, False) if _path_like(token) else (None, False)
        return None, False
    if name == "uv" and words[index + 1: index + 2] == ["run"]:
        rest = [w for w in words[index + 2:] if not w.startswith("-")]
        return (rest[0], False) if rest and _path_like(rest[0]) else (None, False)
    return (first, True) if _path_like(first) else (None, False)


def _path_like(token: str) -> bool:
    return "/" in token or token.endswith(_SCRIPT_EXTENSIONS)


def _is_relative(token: str) -> bool:
    return not (token.startswith(("/", "~", "$")) or re.match(r"^[A-Za-z]:[\\/]", token))


_INSTALLED_SKILL_RE = re.compile(
    r"^(?:\$\{?CLAUDE_PROJECT_DIR\}?|\$\{?HOME\}?|~)/\.claude/skills/([^/]+)/(.+)$")


def _resolve(token: str, ctx: HookContext) -> Optional[Tuple[Path, str]]:
    """Map a script token to a local path, or None when it cannot be known."""
    installed = _INSTALLED_SKILL_RE.match(token)
    if installed:
        # A skill's install path: check the file inside that skill's directory
        # (this skill, or a sibling skill an agent borrows a script from).
        name = installed.group(1)
        skill_dir = ctx.base_dir if ctx.base_dir is not None and ctx.base_dir.name == name \
            else ctx.skill_dirs.get(name)
        if skill_dir is not None:
            return skill_dir / installed.group(2), f" in the {name} skill"

    def sub(name: str, value: Optional[Path]) -> Optional[str]:
        return None if value is None else str(value)
    replacements = {
        "CLAUDE_PROJECT_DIR": sub("CLAUDE_PROJECT_DIR", ctx.project_dir),
        "CLAUDE_PLUGIN_ROOT": sub("CLAUDE_PLUGIN_ROOT", ctx.plugin_root),
        "CLAUDE_SKILL_DIR": sub("CLAUDE_SKILL_DIR", ctx.base_dir),
        "CLAUDE_PLUGIN_DATA": None,
    }
    unknown = False

    def repl(match: "re.Match[str]") -> str:
        nonlocal unknown
        value = replacements.get(match.group(1))
        if value is None:
            unknown = True
            return match.group(0)
        return value

    expanded = _PLACEHOLDER_RE.sub(repl, token)
    if unknown or "$" in expanded or expanded.startswith("~"):
        return None
    path = Path(expanded)
    if path.is_absolute():
        return path, ""
    if ctx.kind == "docs":
        return None
    root = ctx.project_dir or (ctx.plugin_root if ctx.kind == "plugin-hooks" else None)
    if root is None:
        return None
    return root / path, " relative to the project root" if root == ctx.project_dir else " relative to the plugin root"


def _inside_double_quotes(text: str, pos: int) -> bool:
    inside = False
    escaped = False
    single = False
    for index, ch in enumerate(text[:pos]):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not single:
            escaped = True
        elif ch == "'" and not inside:
            single = not single
        elif ch == '"' and not single:
            inside = not inside
    return inside


def _script_can_block(path: Path, event: str) -> Optional[bool]:
    """True/False when the script clearly can/cannot block `event`; None when unknown."""
    try:
        if path.stat().st_size > _MAX_SCRIPT_BYTES:
            return None
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if event == "PermissionRequest":
        # Exit 2 and permissionDecision do nothing here; only decision.behavior denies.
        return bool(re.search(r"\bbehavior\b", source))
    blocking = re.search(
        r"(sys\.exit\(\s*2\s*\)|exit\(\s*2\s*\)|\bexit\s+2\b|process\.exit(Code)?\s*[=(]\s*2|"
        r"os\._exit\(\s*2\s*\)|permissionDecision|\"decision\"\s*:|'decision'\s*:|\bdecision\s*=|"
        r"EXIT_BLOCK|BLOCK\s*=\s*2|SystemExit\(\s*2\s*\))", source)
    return bool(blocking)


def _looks_like_secret(name: str, value: str) -> bool:
    if "${" in value or re.search(r"\$[A-Z_][A-Z0-9_]*", value):
        return False
    lowered = name.lower()
    if any(word in lowered for word in ("authorization", "token", "secret", "api-key", "apikey",
                                         "password", "x-api-key")):
        return len(value.strip()) >= 8
    return bool(re.search(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}"
                          r"|AKIA[0-9A-Z]{16})\b", value))


def looks_like_secret(name: str, value: str) -> bool:
    """Public wrapper used for MCP env/headers checks."""
    return _looks_like_secret(name, value)


def iter_handlers(hooks: Any):
    """Yield (event, group index, handler index, handler) for a well-formed hooks mapping."""
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for hi, handler in enumerate(group["hooks"]):
                if isinstance(handler, dict):
                    yield str(event), gi, hi, handler
