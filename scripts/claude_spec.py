#!/usr/bin/env python3
"""
claude_spec.py - What Claude Code accepts in skill, command, subagent and
plugin files: field names and types, hook events and handler fields,
built-in tools, model aliases and portability rules.

Part of the skill-templates tooling. Single source of truth for
validate_skill.py, format_skill.py and export_skill.py. Library module.

Verified September 2026 against:
    https://code.claude.com/docs/en/skills             frontmatter reference
    https://code.claude.com/docs/en/sub-agents         subagent frontmatter
    https://code.claude.com/docs/en/hooks              events, handler fields, exit codes
    https://code.claude.com/docs/en/permissions        permission rule syntax
    https://code.claude.com/docs/en/tools-reference    built-in tool names
    https://code.claude.com/docs/en/model-config       model aliases
    https://code.claude.com/docs/en/plugins-reference  plugin layout, plugin agents
    https://code.claude.com/docs/en/mcp                plugin MCP tool names
    https://agentskills.io/specification               portable (upload/API) fields
and the loader in the Claude Code 2.1.42 npm package.

Extension point: when Claude Code adds a field, hook event, tool or model
alias, add it here. The validators treat unknown names as warnings, never
errors, so a new Claude Code feature cannot break CI before this file
catches up; near-misses of known names (typos) stay errors because Claude
Code silently ignores them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

VERIFIED = "2026-09"


@dataclass(frozen=True)
class FieldSpec:
    """One frontmatter field.

    kind:   "string" | "boolean" | "string-list" (string or list of strings)
            | "mapping" | "integer" | "any"
    values: allowed string values, when the field is an enum
    note:   one-line purpose, shown in error hints
    """
    name: str
    kind: str
    note: str
    values: Optional[Tuple[str, ...]] = None


def _fields(*specs: FieldSpec) -> Dict[str, FieldSpec]:
    return {spec.name: spec for spec in specs}


# ===========================================================================
# ENUMS
# ===========================================================================

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
SHELLS = ("bash", "powershell")
CONTEXTS = ("fork",)
PERMISSION_MODES = ("default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan", "manual")
MEMORY_SCOPES = ("user", "project", "local")
ISOLATION = ("worktree",)
COLORS = ("red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan")
CACHE_TTLS = ("5m", "1h")
BUILTIN_AGENT_TYPES = ("Explore", "Plan", "general-purpose")

# Model values: family aliases track the newest model; full IDs pin a snapshot.
MODEL_ALIASES = ("default", "best", "fable", "sonnet", "opus", "haiku", "opusplan")
MODEL_SPECIAL = ("inherit",)
DATED_MODEL_RE = re.compile(r"^claude-[a-z0-9.-]*\d{8}(\[1m\])?$", re.IGNORECASE)
FULL_MODEL_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.-]*(\[1m\])?$", re.IGNORECASE)
ONE_M_SUFFIX = "[1m]"


# ===========================================================================
# SKILL AND COMMAND FIELDS (kebab-case, except when_to_use)
# ===========================================================================

SKILL_FIELDS = _fields(
    FieldSpec("name", "string", "display name; the directory name is the command"),
    FieldSpec("description", "string", "what the skill does and when to use it (the triggering text)"),
    FieldSpec("when_to_use", "string", "extra trigger phrases, appended to description in the listing"),
    FieldSpec("argument-hint", "string", "autocomplete hint such as \"[issue-number]\""),
    FieldSpec("arguments", "string-list", "named arguments for $name substitution"),
    FieldSpec("disable-model-invocation", "boolean", "true = only the user can invoke it"),
    FieldSpec("user-invocable", "boolean", "false = hidden from the / menu"),
    FieldSpec("allowed-tools", "string-list", "tools pre-approved for the invoking turn"),
    FieldSpec("disallowed-tools", "string-list", "tools removed while the skill is active"),
    FieldSpec("model", "string", "model while active: alias, full ID, or inherit"),
    FieldSpec("effort", "string", "effort level while active", EFFORT_LEVELS),
    FieldSpec("context", "string", "fork = run in a subagent", CONTEXTS),
    FieldSpec("agent", "string", "subagent type for context: fork"),
    FieldSpec("background", "boolean", "with context: fork, false = wait for the result"),
    FieldSpec("hooks", "mapping", "hooks registered when the skill is invoked"),
    FieldSpec("paths", "string-list", "globs that limit automatic activation"),
    FieldSpec("shell", "string", "shell for !`command` injection", SHELLS),
    FieldSpec("metadata", "mapping", "free-form data for your own tooling"),
    FieldSpec("license", "string", "license (Agent Skills spec field)"),
    FieldSpec("compatibility", "string", "environment requirements, up to 500 characters"),
)

# Fields older Claude Code loaders read that the current docs no longer list.
SKILL_LEGACY_FIELDS = {
    "version": "read by Claude Code 2.1.x loaders but not documented; put it under metadata.version",
}

# A command file (.claude/commands/*.md) accepts the skill fields except these.
COMMAND_EXCLUDED = frozenset({"name", "paths"})
COMMAND_FIELDS = {name: spec for name, spec in SKILL_FIELDS.items() if name not in COMMAND_EXCLUDED}

SKILL_KEY_ORDER = (
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "paths",
    "allowed-tools", "disallowed-tools",
    "model", "effort", "context", "agent", "background", "shell",
    "hooks", "license", "compatibility", "metadata", "version",
)
SKILL_PROSE = ("description", "when_to_use", "argument-hint", "compatibility")
SKILL_BOOLEANS = ("disable-model-invocation", "user-invocable", "background")
SKILL_TOOL_LISTS = ("allowed-tools", "disallowed-tools")

# claude.ai uploads, the Skills API and the Agent Skills spec accept only these
# and fail hard ("Unexpected key(s) in SKILL.md frontmatter") on anything else.
PORTABLE_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")


# ===========================================================================
# SUBAGENT FIELDS (camelCase)
# ===========================================================================

AGENT_FIELDS = _fields(
    FieldSpec("name", "string", "unique identifier; hooks see it as agent_type"),
    FieldSpec("description", "string", "when Claude should delegate to this subagent"),
    FieldSpec("tools", "string-list", "tool allowlist (inherits all when omitted)"),
    FieldSpec("disallowedTools", "string-list", "tool denylist, applied before tools"),
    FieldSpec("model", "string", "sonnet, opus, haiku, fable, a full model ID, or inherit"),
    FieldSpec("permissionMode", "string", "permission mode for the subagent", PERMISSION_MODES),
    FieldSpec("maxTurns", "integer", "maximum agentic turns"),
    FieldSpec("skills", "string-list", "skills whose full content is preloaded at startup"),
    FieldSpec("mcpServers", "any", "server names or inline server definitions"),
    FieldSpec("hooks", "mapping", "hooks active while this subagent runs"),
    FieldSpec("memory", "string", "persistent memory scope", MEMORY_SCOPES),
    FieldSpec("background", "boolean", "true = always run in the background"),
    FieldSpec("omitClaudeMd", "boolean", "true = launch without CLAUDE.md files"),
    FieldSpec("effort", "string", "effort level while active", EFFORT_LEVELS),
    FieldSpec("isolation", "string", "worktree = run in a temporary git worktree", ISOLATION),
    FieldSpec("color", "string", "display color", COLORS),
    FieldSpec("initialPrompt", "string", "first user turn when run as the main session agent"),
    FieldSpec("experimental", "mapping", "experimental options such as cacheTtl"),
)
AGENT_KEY_ORDER = (
    "name", "description", "tools", "disallowedTools", "model", "effort",
    "permissionMode", "maxTurns", "skills", "mcpServers", "hooks", "memory",
    "background", "isolation", "omitClaudeMd", "color", "initialPrompt", "experimental",
)
AGENT_PROSE = ("description", "initialPrompt")
AGENT_BOOLEANS = ("background", "omitClaudeMd")
AGENT_TOOL_LISTS = ("tools", "disallowedTools")

# Plugin agents: Claude Code ignores these for security (or does not support them).
PLUGIN_AGENT_IGNORED = {
    "hooks": "ignored in plugin agents for security reasons",
    "mcpServers": "ignored in plugin agents for security reasons; bundle servers in the plugin's .mcp.json",
    "permissionMode": "ignored in plugin agents for security reasons",
    "initialPrompt": "not supported in plugin agents",
}


# ===========================================================================
# NEAR-MISS FIELD NAMES (Claude Code silently ignores them)
# ===========================================================================

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def suggest_field(name: str, known: Dict[str, FieldSpec], other_kind: Dict[str, FieldSpec]
                  ) -> Tuple[Optional[str], bool]:
    """Return (suggested field, is_other_kind) for an unknown field name.

    Matches ignore case, hyphens and underscores ("allowed_tools",
    "allowedTools") and allow one or two typos ("desciption").
    is_other_kind is True when the name belongs to the other file kind
    (a subagent's "tools" written in a SKILL.md, or the reverse).
    """
    target = _norm(name)
    for field_name in known:
        if _norm(field_name) == target:
            return field_name, False
    for field_name in other_kind:
        if _norm(field_name) == target:
            translated = CROSS_KIND.get(field_name)
            return translated or field_name, True
    best, best_distance = None, 3
    for field_name in known:
        distance = _edit_distance(_norm(field_name), target)
        if distance < best_distance:
            best, best_distance = field_name, distance
    if best and best_distance <= (1 if len(target) <= 5 else 2):
        return best, False
    return None, False


# A field written for the other file kind, mapped to what this kind uses.
CROSS_KIND = {
    "tools": "allowed-tools",
    "disallowedTools": "disallowed-tools",
    "allowed-tools": "tools",
    "disallowed-tools": "disallowedTools",
}


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 3
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


# ===========================================================================
# HOOKS
# ===========================================================================

# Event name -> whether a matcher filters it.
HOOK_EVENTS: Dict[str, bool] = {
    "SessionStart": True, "Setup": True, "InstructionsLoaded": True,
    "UserPromptSubmit": False, "UserPromptExpansion": True, "MessageDisplay": False,
    "PreToolUse": True, "PermissionRequest": True, "PermissionDenied": True,
    "PostToolUse": True, "PostToolUseFailure": True, "PostToolBatch": False,
    "Notification": True, "SubagentStart": True, "SubagentStop": True,
    "TaskCreated": False, "TaskCompleted": False, "Stop": False, "StopFailure": True,
    "TeammateIdle": False, "ConfigChange": True, "CwdChanged": False,
    "DirectoryAdded": True, "FileChanged": True, "WorktreeCreate": False,
    "WorktreeRemove": False, "PreCompact": True, "PostCompact": True,
    "PreModelSwitch": True, "PostModelSwitch": True, "Elicitation": True,
    "ElicitationResult": True, "SessionEnd": True,
}
# Events whose matcher is a tool name, and where a handler's `if` rule applies.
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure",
                         "PermissionRequest", "PermissionDenied"})
# Events a command hook usually exists to gate: exit 2 or a JSON decision blocks them,
# except PermissionRequest, which ignores exit 2 and denies only via decision.behavior.
GATE_EVENTS = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit",
                         "UserPromptExpansion", "Stop", "SubagentStop", "PreModelSwitch"})

HOOK_COMMON_FIELDS = frozenset({"type", "if", "timeout", "statusMessage", "once"})
HOOK_HANDLERS: Dict[str, Dict[str, FrozenSet[str]]] = {
    "command": {"required": frozenset({"command"}),
                "optional": frozenset({"args", "async", "asyncRewake", "shell"})},
    "http": {"required": frozenset({"url"}), "optional": frozenset({"headers", "allowedEnvVars"})},
    "mcp_tool": {"required": frozenset({"server", "tool"}), "optional": frozenset({"input"})},
    "prompt": {"required": frozenset({"prompt"}), "optional": frozenset({"model"})},
    "agent": {"required": frozenset({"prompt"}), "optional": frozenset({"model"})},
}
# Claude Code 2.1.x (before http and mcp_tool existed) rejected every hook in a
# file that used an unknown type.
HOOK_TYPES_SINCE = {"http": "a newer Claude Code release", "mcp_tool": "a newer Claude Code release"}

# Placeholders Claude Code substitutes in hook commands.
HOOK_PLACEHOLDERS = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA")


# ===========================================================================
# TOOLS AND PERMISSION RULES
# ===========================================================================

BUILTIN_TOOLS = frozenset({
    "Agent", "Artifact", "AskUserQuestion", "Bash", "CronCreate", "CronDelete", "CronList",
    "Edit", "EndConversation", "EnterPlanMode", "EnterWorktree", "ExitPlanMode", "ExitWorktree",
    "Glob", "Grep", "ListAgents", "ListMcpResourcesTool", "LSP", "Monitor", "NotebookEdit",
    "PowerShell", "PushNotification", "Read", "ReadMcpResourceTool", "RemoteTrigger",
    "ReportFindings", "ScheduleWakeup", "SendFeedback", "SendMessage", "SendUserFile",
    "ShareOnboardingGuide", "Skill", "SubagentHandback", "TaskCreate", "TaskGet", "TaskList",
    "TaskOutput", "TaskStop", "TaskUpdate", "TodoWrite", "ToolSearch", "WaitForMcpServers",
    "WebFetch", "WebSearch", "Workflow", "Write",
})
LEGACY_TOOLS = {"Task": "Agent", "MultiEdit": "Edit"}
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
# Only Read(path) and Edit(path) rules are consulted for file access; path
# rules on these tools are accepted and never checked.
UNCONSULTED_PATH_TOOLS = frozenset({"Write", "NotebookEdit", "Glob", "MultiEdit"})

TOOL_RULE_RE = re.compile(r"^(?P<tool>[^()\s]+)(?:\((?P<spec>.*)\))?$", re.DOTALL)
MCP_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_-]+(?:__[A-Za-z0-9_*.-]+)?$")


def plugin_mcp_prefix(plugin: str, server: str) -> str:
    """Callable-name prefix of a plugin-bundled MCP server's tools.

    Tools are named mcp__plugin_<plugin>_<server>__<tool>, with any character
    outside A-Z a-z 0-9 _ - replaced by "_".
    """
    clean = lambda text: re.sub(r"[^A-Za-z0-9_-]", "_", text)
    return f"mcp__plugin_{clean(plugin)}_{clean(server)}__"


# ===========================================================================
# NAMES, LIMITS, BODY
# ===========================================================================

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_NAME_WORDS = ("anthropic", "claude")
XML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*)?/?>")

NAME_MAX = 64
DESCRIPTION_MAX = 1024          # Agent Skills spec, Skills API, claude.ai upload
LISTING_MAX = 1536              # description + when_to_use in Claude Code's listing
COMPATIBILITY_MAX = 500
BODY_LINES_WARN = 500           # Claude Code docs and the Agent Skills spec
BODY_WORDS_WARN = 1500          # this repo's template budget

TRIGGER_MARKERS = ("use when", "use this when", "use for", "use if", "use to", "when the user",
                   "trigger", "invoke when", "applies when", "for when")
PLACEHOLDER_RE = re.compile(r"\{\{[^{}\n]{1,80}\}\}")

PLUGIN_MANIFEST_FIELDS = frozenset({
    "$schema", "name", "displayName", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "metadata", "defaultEnabled", "skills", "commands",
    "agents", "workflows", "hooks", "mcpServers", "outputStyles", "lspServers", "experimental",
    "userConfig", "channels", "dependencies", "themes", "monitors",
})
PLUGIN_COMPONENT_DIRS = ("skills", "commands", "agents", "hooks", "workflows", "output-styles",
                         "themes", "monitors")
MCP_TRANSPORTS = ("stdio", "http", "sse", "ws")
