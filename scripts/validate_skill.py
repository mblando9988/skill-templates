#!/usr/bin/env python3
"""
validate_skill.py - Check Claude Code skills, commands, subagents and plugins
the way Claude Code loads them.

Part of the skill-templates tooling.

Responsibilities:
- Parse frontmatter with Claude Code's real parsers (Bun.YAML for native
  builds, eemeli/yaml for npm builds) and flag anything either build loads
  differently, or not at all.
- Check fields: names, descriptions, hooks, allowed-tools / tools, model,
  booleans, and near-miss field names that Claude Code silently ignores.
- Check bodies and directories: leftover template placeholders, broken
  links, missing scripts, hook scripts that cannot start or never block.
- Check plugins: manifest, .mcp.json, hooks/hooks.json, fields Claude Code
  ignores in plugin agents, and plugin MCP tool names.

Usage:
    python3 validate_skill.py PATH [PATH ...] [--kind auto|skill|command|agent]
                              [--target claude-code|portable] [--strict]
                              [--allow-placeholders] [--json] [--quiet]

Examples:
    python3 validate_skill.py .claude/skills/deploy
    python3 validate_skill.py .claude/                 # every skill, command, agent
    python3 validate_skill.py my-plugin/ --strict      # CI: warnings fail too
    python3 validate_skill.py my-skill --target portable   # before a claude.ai upload
    python3 validate_skill.py templates/skill-md-template.md --kind skill --allow-placeholders

Exit Codes:
    0  - No errors (warnings allowed unless --strict)
    1  - General failure (unreadable input, parser bridge failed)
    2  - Invalid arguments
    3  - Path not found
    4  - Required tool missing (bun, node, or the pinned yaml package)
    10 - Validation failed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_spec as spec  # noqa: E402
import hook_safety  # noqa: E402
from claude_frontmatter import (  # noqa: E402
    EXIT_DEPENDENCY, EngineError, EngineUnavailable, Engines, Finding, Frontmatter,
    claude_bool, iter_files, load_frontmatters, split_tool_rules,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ARGS = 2
EXIT_NOT_FOUND = 3
EXIT_VALIDATION = 10
MAX_FILE_BYTES = 1024 * 1024


# ===========================================================================
# RESULT TYPE (script template)
# ===========================================================================

@dataclass
class Result:
    """Standard result object: success, message, data, errors, warnings."""
    success: bool
    message: str
    data: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ===========================================================================
# DISCOVERY
# ===========================================================================

@dataclass
class PluginInfo:
    root: Path
    name: str
    manifest: Optional[dict] = None
    mcp_servers: Dict[str, Any] = field(default_factory=dict)
    hooks_files: List[Path] = field(default_factory=list)


@dataclass
class Target:
    path: Path
    kind: str                         # skill | command | agent
    plugin: Optional[PluginInfo] = None
    plugin_root_skill: bool = False   # a SKILL.md at a plugin's root
    text: str = ""
    fm: Optional[Frontmatter] = None
    findings: List[Finding] = field(default_factory=list)


@dataclass
class Options:
    target: str = "claude-code"
    strict: bool = False
    allow_placeholders: bool = False
    kind: str = "auto"


def display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def is_plugin_root(path: Path) -> bool:
    return (path / ".claude-plugin" / "plugin.json").is_file()


def enclosing_plugin(path: Path) -> Optional[Path]:
    """The plugin whose components include path (a file or its directory), if any.

    Only files a plugin actually loads count: its root SKILL.md and anything
    under its skills/, agents/, commands/ or hooks/ directories.
    """
    path = path.resolve()
    for parent in [path] + list(path.parents):
        if parent.is_dir() and is_plugin_root(parent):
            rel = path.relative_to(parent).parts
            if not rel or rel[0] in ("skills", "agents", "commands", "hooks") or rel == ("SKILL.md",):
                return parent
            return None
    return None


def infer_kind(path: Path) -> Optional[str]:
    if path.name.lower() == "skill.md":
        return "skill"
    parts = [p.lower() for p in path.parent.parts]
    if "agents" in parts:
        return "agent"
    if "commands" in parts:
        return "command"
    return None


def discover(paths: List[Path], opts: Options) -> Tuple[List[Target], Dict[Path, PluginInfo], List[Finding]]:
    targets: Dict[Path, Target] = {}
    plugins: Dict[Path, PluginInfo] = {}
    problems: List[Finding] = []

    def plugin_for(root: Optional[Path]) -> Optional[PluginInfo]:
        if root is None:
            return None
        root = root.resolve()
        if root not in plugins:
            plugins[root] = PluginInfo(root=root, name=root.name)
        return plugins[root]

    def add(path: Path, kind: str) -> None:
        resolved = path.resolve()
        if resolved in targets:
            return
        root = enclosing_plugin(resolved)
        info = plugin_for(root)
        at_root = info is not None and kind == "skill" and resolved.parent == info.root
        targets[resolved] = Target(path=path, kind=kind, plugin=info, plugin_root_skill=at_root)

    for raw in paths:
        if raw.is_file():
            if raw.name == "plugin.json" and raw.parent.name == ".claude-plugin":
                plugin_for(raw.parent.parent)
                _collect_plugin(raw.parent.parent, add)
                continue
            kind = opts.kind if opts.kind != "auto" else infer_kind(raw)
            if raw.suffix.lower() != ".md":
                problems.append(Finding("error", "input.unsupported",
                                        f"{display(raw)} is not a markdown file or plugin.json"))
                continue
            if kind is None:
                problems.append(Finding("error", "input.kind",
                                        f"cannot tell whether {display(raw)} is a skill, command or agent",
                                        hint="pass --kind skill|command|agent"))
                continue
            add(raw, kind)
        elif raw.is_dir():
            if opts.kind == "skill" and (raw / "SKILL.md").is_file():
                add(raw / "SKILL.md", "skill")
                continue
            if is_plugin_root(raw):
                plugin_for(raw)
                _collect_plugin(raw, add)
            else:
                _collect_dir(raw, add, plugin_for)
    return list(targets.values()), plugins, problems


def _collect_plugin(root: Path, add) -> None:
    """Collect only what Claude Code loads from a plugin: its component folders."""
    if (root / "SKILL.md").is_file() and not (root / "skills").is_dir():
        add(root / "SKILL.md", "skill")
    for folder, kind in (("skills", "skill"), ("agents", "agent"), ("commands", "command")):
        base = root / folder
        if not base.is_dir():
            continue
        for path in iter_files([base]):
            if path.suffix.lower() != ".md":
                continue
            if kind == "skill":
                if path.name.lower() == "skill.md":
                    add(path, "skill")
            else:
                add(path, kind)


def _collect_dir(root: Path, add, plugin_for) -> None:
    for path in iter_files([root]):
        rel_parts = [p.lower() for p in path.relative_to(root).parts[:-1]]
        if path.name == "plugin.json" and path.parent.name == ".claude-plugin":
            plugin_for(path.parent.parent)
            continue
        if path.suffix.lower() != ".md":
            continue
        if path.name.lower() == "skill.md":
            add(path, "skill")
        elif "agents" in rel_parts and "references" not in rel_parts:
            add(path, "agent")
        elif "commands" in rel_parts and "references" not in rel_parts:
            add(path, "command")


# ===========================================================================
# CHECK CONTEXT
# ===========================================================================

class Checker:
    """Collects findings for one file."""

    def __init__(self, target: Target, opts: Options, index: "Index"):
        self.t = target
        self.opts = opts
        self.index = index
        self.fm = target.fm
        self.rel = display(target.path)
        self.base_dir = target.path.resolve().parent
        self.findings = target.findings

    @property
    def portable(self) -> bool:
        return self.opts.target == "portable"

    def add(self, severity: str, code: str, message: str, line: Optional[int] = None,
            hint: Optional[str] = None, col: Optional[int] = None) -> None:
        self.findings.append(Finding(severity, code, message, line=line, col=col, hint=hint, path=self.rel))

    def at(self, *path: Any) -> Optional[int]:
        return self.fm.line_of(*path) if self.fm else None


@dataclass
class Index:
    """Names found in this run, for cross-file checks."""
    skills: Dict[str, Target] = field(default_factory=dict)
    agents: Dict[str, List[Target]] = field(default_factory=dict)


# ===========================================================================
# SHARED FIELD CHECKS
# ===========================================================================

def check_unknown_fields(chk: Checker, data: Dict[str, Any], known: Dict[str, spec.FieldSpec],
                         other: Dict[str, spec.FieldSpec], kind: str) -> None:
    for key in data:
        if key in known or (kind == "agent" and key == "cacheTtl"):
            continue
        if kind in ("skill", "command") and key in spec.SKILL_LEGACY_FIELDS:
            chk.add("warning", "field.legacy", f"'{key}' is {spec.SKILL_LEGACY_FIELDS[key]}", chk.at(key))
            continue
        if kind == "command" and key in spec.COMMAND_EXCLUDED:
            chk.add("warning", "field.ignored-in-command",
                    f"'{key}' is ignored in .claude/commands files (it works only in SKILL.md)", chk.at(key),
                    hint="delete it, or turn the command into a skill directory")
            continue
        suggestion, cross = spec.suggest_field(key, known, other)
        if suggestion and cross:
            other_kind = "subagent" if kind in ("skill", "command") else "skill"
            chk.add("error", "field.wrong-kind",
                    f"'{key}' is a {other_kind} field; Claude Code silently ignores it in {_article(kind)} {kind} file",
                    chk.at(key), hint=f"use '{suggestion}'" if suggestion in known else "remove it")
        elif suggestion:
            chk.add("error", "field.misspelled",
                    f"unknown field '{key}': Claude Code silently ignores it; did you mean '{suggestion}'?",
                    chk.at(key), hint=f"rename it to '{suggestion}'")
        else:
            chk.add("warning", "field.unknown",
                    f"unknown field '{key}': Claude Code ignores it",
                    chk.at(key), hint="put your own data under metadata" if kind != "agent" else "remove it")


def _article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"


def check_types(chk: Checker, data: Dict[str, Any], known: Dict[str, spec.FieldSpec]) -> Dict[str, bool]:
    """Type-check known fields. Returns {field: usable} for later checks."""
    usable: Dict[str, bool] = {}
    for key, value in data.items():
        fspec = known.get(key)
        if fspec is None:
            continue
        line = chk.at(key)
        ok = True
        if value is None:
            chk.add("warning", "field.empty", f"'{key}' has no value; Claude Code ignores it", line,
                    hint="fill it in or delete the line")
            usable[key] = False
            continue
        if fspec.kind == "string":
            if not isinstance(value, str):
                ok = False
                if key == "argument-hint" and isinstance(value, list):
                    chk.add("warning", "field.hint-list",
                            f"YAML reads argument-hint as a list {json.dumps(value)}, not the text you typed",
                            line, hint=f"quote it: argument-hint: \"{_as_hint_text(value)}\"")
                else:
                    chk.add("error", "field.type", f"'{key}' must be a string, not {_type_name(value)}", line,
                            hint="quote the value")
        elif fspec.kind == "boolean":
            meaning = claude_bool(value)
            if isinstance(value, bool):
                pass
            elif meaning is None:
                ok = False
                chk.add("error", "field.boolean",
                        f"'{key}: {value}' is not a boolean Claude Code understands", line,
                        hint="use true or false")
            else:
                chk.add("warning", "field.boolean-spelling",
                        f"'{key}: {value}' means {str(meaning).lower()} only in Claude Code 2.1.218 and "
                        "later; earlier versions read it as false", line,
                        hint=f"write {str(meaning).lower()} (format_skill.py does this)")
        elif fspec.kind == "string-list":
            if isinstance(value, list):
                bad = [item for item in value if not isinstance(item, str)]
                if bad:
                    chk.add("error", "field.list-item",
                            f"'{key}' has non-text items {json.dumps(bad)}; Claude Code drops them", line,
                            hint="quote each item")
            elif not isinstance(value, str):
                ok = False
                chk.add("error", "field.type",
                        f"'{key}' must be a string or a list of strings, not {_type_name(value)}", line)
        elif fspec.kind == "mapping":
            if not isinstance(value, dict):
                ok = False
                chk.add("error", "field.type",
                        f"'{key}' must be a mapping (indented key: value lines), not {_type_name(value)}; "
                        "Claude Code drops it", line)
        elif fspec.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                ok = False
                chk.add("error", "field.type", f"'{key}' must be a positive whole number", line)
        if ok and fspec.values and isinstance(value, str) and value not in fspec.values:
            severity = "warning" if key == "effort" or key == "color" else "error"
            chk.add(severity, "field.value",
                    f"'{key}: {value}' is not one of: {', '.join(fspec.values)}", line)
            ok = False
        usable[key] = ok
    return usable


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__


def _as_hint_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(f"[{item}]" for item in value)
    return str(value)


def check_model(chk: Checker, value: Any, key: str = "model") -> None:
    if not isinstance(value, str):
        return
    line = chk.at(key)
    lowered = value.strip().lower()
    base = lowered[: -len(spec.ONE_M_SUFFIX)] if lowered.endswith(spec.ONE_M_SUFFIX) else lowered
    if lowered in spec.MODEL_SPECIAL or base in spec.MODEL_ALIASES:
        if value != value.strip().lower():
            chk.add("info", "model.case", f"write the model alias in lowercase: '{lowered}'", line)
        return
    if spec.DATED_MODEL_RE.match(value.strip()):
        chk.add("error", "model.dated",
                f"'{value}' pins a dated model snapshot that will be retired and break the {chk.t.kind}",
                line, hint="use a family alias (sonnet, opus, haiku, fable) or inherit")
    elif spec.FULL_MODEL_RE.match(value.strip()):
        chk.add("warning", "model.pinned",
                f"'{value}' pins one model version; the {chk.t.kind} will not follow new releases",
                line, hint="prefer an alias (sonnet, opus, haiku, fable) unless the pin is deliberate")
    else:
        chk.add("warning", "model.unknown",
                f"'{value}' is not a known model alias or Claude model ID; Claude Code passes it through "
                "and the request fails if the model does not exist", line,
                hint=f"use one of: {', '.join(spec.MODEL_ALIASES + spec.MODEL_SPECIAL)}")


def check_tool_rules(chk: Checker, key: str, value: Any, role: str) -> List[str]:
    """Check an allowed-tools / tools / disallowed-tools value. Returns the parsed rules.

    role: "allow" (skill allowed-tools), "deny" (skill disallowed-tools),
          "agent-allow" (subagent tools), "agent-deny" (subagent disallowedTools)
    """
    rules = split_tool_rules(value)
    if rules is None:
        return []
    line = chk.at(key)
    if not rules:
        chk.add("warning", "tools.empty", f"'{key}' is empty and has no effect", line)
        return []
    if chk.portable and key == "allowed-tools" and isinstance(value, list):
        chk.add("warning", "portable.allowed-tools-list",
                "the Agent Skills spec defines allowed-tools as one space-separated string", line,
                hint="export_skill.py converts it")
    seen = set()
    plugin = chk.t.plugin
    for rule in rules:
        if chk.opts.allow_placeholders and "{{" in rule:
            continue
        if rule in seen:
            chk.add("info", "tools.duplicate", f"'{rule}' is listed twice in '{key}'", line)
            continue
        seen.add(rule)
        if rule == "*":
            chk.add("warning", "tools.wildcard", f"'*' in '{key}' matches every tool", line)
            continue
        match = spec.TOOL_RULE_RE.match(rule)
        if not match or rule.count("(") != rule.count(")"):
            chk.add("error", "tools.syntax",
                    f"'{rule}' in '{key}' is not 'Tool' or 'Tool(specifier)'; Claude Code splits the list "
                    "on commas and spaces outside parentheses, so check for a stray space or bracket",
                    line, hint="write rules like Bash(git status *) or Read(./docs/**)")
            continue
        tool, spec_text = match.group("tool"), match.group("spec")
        if tool.startswith("mcp__"):
            if not spec.MCP_TOOL_RE.match(tool):
                chk.add("error", "tools.mcp-syntax", f"'{tool}' is not a valid MCP tool name", line,
                        hint="use mcp__<server>, mcp__<server>__* or mcp__<server>__<tool>")
            elif plugin is not None:
                _check_plugin_mcp_name(chk, tool, plugin, line, key)
            continue
        if tool in spec.LEGACY_TOOLS:
            chk.add("warning", "tools.legacy", f"'{tool}' was renamed to '{spec.LEGACY_TOOLS[tool]}'", line,
                    hint=f"use {spec.LEGACY_TOOLS[tool]}")
        elif tool not in spec.BUILTIN_TOOLS:
            near = _nearest(tool, spec.BUILTIN_TOOLS)
            if tool[:1].islower() and tool[:1].upper() + tool[1:] in spec.BUILTIN_TOOLS:
                chk.add("error", "tools.case", f"'{tool}' matches no tool; tool names start uppercase",
                        line, hint=f"use {tool[:1].upper() + tool[1:]}")
            elif near:
                chk.add("error", "tools.misspelled", f"'{tool}' is not a Claude Code tool; did you mean '{near}'?",
                        line, hint=f"use {near}")
            else:
                chk.add("warning", "tools.unknown",
                        f"'{tool}' is not a built-in tool (fine if it is new or comes from an extension)", line)
        if spec_text is not None:
            _check_rule_specifier(chk, key, tool, spec_text, line)
        elif role == "allow" and tool in spec.SHELL_TOOLS:
            chk.add("warning", "tools.unscoped-shell",
                    f"bare '{tool}' in allowed-tools pre-approves every {tool} command for the turn",
                    line, hint=f"scope it, e.g. {tool}(git status *)")
        if role == "allow" and spec_text is not None and tool in spec.SHELL_TOOLS and spec_text.strip() in ("*", ":*"):
            chk.add("warning", "tools.unscoped-shell",
                    f"'{rule}' pre-approves every {tool} command for the turn", line)
        if role == "agent-deny" and spec_text is not None:
            chk.add("warning", "tools.deny-specifier",
                    f"'{rule}' in disallowedTools removes the whole {tool} tool, not just matching commands",
                    line, hint=f"to block only some commands, add a permissions.deny rule '{rule}' in settings")
        if role == "agent-allow" and tool == "Skill":
            chk.add("info", "tools.skill", "to preload skills into the subagent, list them under 'skills:'",
                    line)
    return rules


def _check_rule_specifier(chk: Checker, key: str, tool: str, spec_text: str, line: Optional[int]) -> None:
    if tool in spec.SHELL_TOOLS:
        if spec_text.strip() == ":*":
            chk.add("error", "tools.empty-prefix", f"'{tool}(:*)' has an empty command prefix", line,
                    hint=f"write {tool}(git:*) or {tool}(git *)")
        elif ":*" in spec_text and not spec_text.endswith(":*"):
            chk.add("error", "tools.prefix-position",
                    f"in '{tool}({spec_text})' the ':*' is not at the end, so Claude Code treats the colon "
                    "literally and the rule matches nothing useful", line,
                    hint="put :* at the end, or use a space and * (git push *)")
        for placeholder in re.findall(r"\$\{(CLAUDE_SKILL_DIR|CLAUDE_PLUGIN_ROOT)\}(/[^\s)*]+)", spec_text):
            _check_placeholder_path(chk, placeholder[0], placeholder[1], line, f"'{key}' rule")
    else:
        if ":*" in spec_text:
            chk.add("error", "tools.prefix-non-shell",
                    f"'{tool}({spec_text})': the ':*' prefix syntax only works for Bash and PowerShell",
                    line, hint="use glob patterns such as * or ** for paths")
        if tool in spec.UNCONSULTED_PATH_TOOLS:
            chk.add("warning", "tools.path-rule-ignored",
                    f"'{tool}({spec_text})': Claude Code accepts path rules for {tool} but never checks them; "
                    "only Read(path) and Edit(path) are consulted", line, hint=f"use Edit({spec_text})")
        if tool == "WebFetch" and not spec_text.startswith("domain:"):
            chk.add("warning", "tools.webfetch-spec",
                    f"'WebFetch({spec_text})': WebFetch rules take the form WebFetch(domain:example.com)", line)


def _check_placeholder_path(chk: Checker, name: str, rest: str, line: Optional[int], where: str) -> None:
    if chk.opts.allow_placeholders:
        return                     # templates point at files that do not exist yet
    root = chk.base_dir if name == "CLAUDE_SKILL_DIR" else (chk.t.plugin.root if chk.t.plugin else None)
    if root is None:
        return
    target = (root / rest.lstrip("/")).resolve()
    if "*" in rest or "$" in rest:
        return
    if not target.exists():
        chk.add("error", "path.missing",
                f"{where} points at ${{{name}}}{rest}, which does not exist in this {'skill' if name == 'CLAUDE_SKILL_DIR' else 'plugin'}",
                line, hint="fix the path or add the file")


def _check_plugin_mcp_name(chk: Checker, tool: str, plugin: PluginInfo, line: Optional[int], key: str) -> None:
    server = tool[len("mcp__"):].split("__")[0]
    if server in plugin.mcp_servers:
        prefix = spec.plugin_mcp_prefix(plugin.name, server)
        chk.add("error", "tools.plugin-mcp-name",
                f"'{tool}' uses the bare server key '{server}', but tools from a plugin-bundled server are "
                f"named {prefix}<tool>; this {key} entry matches nothing", line,
                hint=f"use {prefix}{tool.split('__', 2)[2] if tool.count('__') >= 2 else '*'}")


def _nearest(name: str, candidates: Iterable[str]) -> Optional[str]:
    best, best_distance = None, 3
    for candidate in candidates:
        distance = spec._edit_distance(name.lower(), candidate.lower())
        if distance < best_distance:
            best, best_distance = candidate, distance
    limit = 1 if len(name) <= 4 else 2
    return best if best is not None and best_distance <= limit else None


# ===========================================================================
# HOOKS
# ===========================================================================

def check_hooks(chk: Checker, hooks: Any, owner: str, prefix: Tuple[Any, ...] = ("hooks",),
                line_of=None) -> None:
    """Schema and safety checks for a hooks mapping.

    owner: "skill" | "command" | "agent" | "plugin-agent" | "plugin-hooks"
    """
    at = line_of or (lambda *path: chk.at(*path))
    if owner == "plugin-agent":
        chk.add("error", "agent.plugin-ignored",
                f"'hooks' is {spec.PLUGIN_AGENT_IGNORED['hooks']}", at(*prefix),
                hint="copy the agent into .claude/agents/ to use hooks, or move them to the plugin's "
                     "hooks/hooks.json")
        return
    if not isinstance(hooks, dict):
        chk.add("error", "hooks.type", "'hooks' must map event names to lists of matcher groups",
                at(*prefix))
        return
    lowered_events = {name.lower(): name for name in spec.HOOK_EVENTS}
    ctx = hook_safety.HookContext(
        kind="plugin-hooks" if owner == "plugin-hooks" else owner,
        base_dir=chk.base_dir,
        project_dir=hook_safety.guess_project_dir(chk.t.path),
        plugin_root=chk.t.plugin.root if chk.t.plugin else None,
        skill_dirs={name: t.path.resolve().parent for name, t in chk.index.skills.items()})
    if owner in ("skill", "command"):
        _check_session_scope(chk, hooks, at(*prefix))
    for event, groups in hooks.items():
        path = prefix + (event,)
        line = at(*path)
        if event not in spec.HOOK_EVENTS:
            proper = lowered_events.get(str(event).lower().replace("_", "").replace("-", ""))
            proper = proper or _nearest(str(event), spec.HOOK_EVENTS)
            if proper:
                chk.add("error", "hooks.event-misspelled",
                        f"'{event}' is not a hook event; did you mean '{proper}'? Claude Code 2.1.x rejects "
                        "every hook in the file when one event name is unknown", line, hint=f"use {proper}")
            else:
                chk.add("warning", "hooks.event-unknown",
                        f"'{event}' is not a hook event this toolkit knows; older Claude Code versions reject "
                        "every hook in the file when one event name is unknown", line)
        if not isinstance(groups, list):
            chk.add("error", "hooks.groups",
                    f"'{event}' must be a list of '- matcher: ... hooks: [...]' groups", line)
            continue
        if owner in ("agent",) and event == "Stop":
            chk.add("info", "hooks.subagent-stop", "in a subagent, Stop hooks run as SubagentStop", line)
        for gi, group in enumerate(groups):
            gpath = path + (gi,)
            gline = at(*gpath)
            if not isinstance(group, dict):
                chk.add("error", "hooks.group", f"{event}[{gi}] must be a mapping with 'hooks:' (and "
                        "optionally 'matcher:')", gline)
                continue
            if "hooks" not in group and ("type" in group or "command" in group):
                chk.add("error", "hooks.missing-inner",
                        f"{event}[{gi}] puts the handler directly in the group; Claude Code needs it inside "
                        "a nested 'hooks:' list", gline,
                        hint="write: - matcher: \"Bash\"\\n  hooks:\\n    - type: command\\n      command: ...")
                continue
            for extra in sorted(set(group) - {"matcher", "hooks"}):
                chk.add("warning", "hooks.group-field", f"'{extra}' in a hook group is ignored", at(*gpath, extra))
            matcher = group.get("matcher")
            if "matcher" in group:
                _check_matcher(chk, event, matcher, at(*gpath, "matcher"))
            handlers = group.get("hooks")
            if not isinstance(handlers, list) or not handlers:
                chk.add("error", "hooks.handlers", f"{event}[{gi}].hooks must be a non-empty list of handlers",
                        at(*gpath, "hooks"))
                continue
            for hi, handler in enumerate(handlers):
                hpath = gpath + ("hooks", hi)
                _check_handler(chk, event, handler, owner, hpath, at, ctx)


def _check_session_scope(chk: Checker, hooks: Dict[str, Any], line: Optional[int]) -> None:
    """Skill hooks outlive the task: they stay on the main thread for the whole session."""
    lasting = sorted({event for event, _gi, _hi, handler in hook_safety.iter_handlers(hooks)
                      if handler.get("once") is not True})
    if lasting:
        chk.add("warning", "hooks.session-scope",
                f"hooks in {chk.t.kind} frontmatter stay registered on the main conversation for the rest of "
                f"the session once it runs, so {', '.join(lasting)} keeps firing on every later matching "
                "call, long after this task", line,
                hint="move the hooks into a subagent's frontmatter (they run only while it runs) and invoke "
                     "it with context: fork + agent: <subagent>; or set once: true for a one-time hook")


def _check_matcher(chk: Checker, event: str, matcher: Any, line: Optional[int]) -> None:
    if matcher is None:
        return
    if not isinstance(matcher, str):
        chk.add("error", "hooks.matcher-type",
                f"matcher must be a string such as \"Edit|Write\", not {_type_name(matcher)}", line)
        return
    if event in spec.HOOK_EVENTS and not spec.HOOK_EVENTS[event] and matcher not in ("", "*"):
        chk.add("warning", "hooks.matcher-ignored", f"{event} does not support matchers; this one is ignored",
                line)
    if re.match(r"^[A-Z][A-Za-z]*\(.*\)$", matcher):
        chk.add("error", "hooks.matcher-rule",
                f"matcher '{matcher}' is permission-rule syntax; matchers are tool names or regexes, so this "
                "never matches", line,
                hint=f"use matcher: \"{matcher.split('(')[0]}\" plus if: \"{matcher}\" on the handler")
        return
    if re.search(r"[^A-Za-z0-9_\-, |]", matcher):
        try:
            re.compile(matcher)
        except re.error as exc:
            chk.add("error", "hooks.matcher-regex", f"matcher '{matcher}' is not a valid regular expression "
                    f"({exc})", line)
    if chk.t.plugin and matcher.startswith("mcp__"):
        for alt in matcher.split("|"):
            server = alt[len("mcp__"):].split("__")[0] if alt.startswith("mcp__") else None
            if server and server in chk.t.plugin.mcp_servers:
                chk.add("error", "hooks.plugin-mcp-matcher",
                        f"matcher '{alt}' uses the bare server key; tools from this plugin's server are named "
                        f"{spec.plugin_mcp_prefix(chk.t.plugin.name, server)}<tool>, so it never fires", line)


def _check_handler(chk: Checker, event: str, handler: Any, owner: str, hpath: Tuple[Any, ...], at,
                   ctx: hook_safety.HookContext) -> None:
    line = at(*hpath)
    if not isinstance(handler, dict):
        chk.add("error", "hooks.handler", f"handler {'.'.join(map(str, hpath))} must be a mapping", line)
        return
    kind = handler.get("type")
    if kind not in spec.HOOK_HANDLERS:
        chk.add("error", "hooks.handler-type",
                f"hook type '{kind}' is not one of: {', '.join(spec.HOOK_HANDLERS)}; Claude Code rejects every "
                "hook in the file", line)
        return
    if kind in spec.HOOK_TYPES_SINCE:
        chk.add("info", "hooks.handler-new", f"'{kind}' hooks need {spec.HOOK_TYPES_SINCE[kind]}; Claude Code "
                "2.1.x rejected every hook in a file that used them", line)
    allowed = spec.HOOK_COMMON_FIELDS | spec.HOOK_HANDLERS[kind]["required"] | spec.HOOK_HANDLERS[kind]["optional"]
    for req in sorted(spec.HOOK_HANDLERS[kind]["required"]):
        value = handler.get(req)
        if not isinstance(value, str) or not value.strip():
            chk.add("error", "hooks.handler-field", f"{kind} hook needs a non-empty '{req}' string",
                    at(*hpath, req) if req in handler else line)
    for extra in sorted(set(handler) - allowed):
        chk.add("warning", "hooks.handler-extra", f"'{extra}' is not a {kind} hook field and is ignored",
                at(*hpath, extra))
    timeout = handler.get("timeout")
    if "timeout" in handler and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        chk.add("error", "hooks.timeout",
                "timeout must be a positive number of seconds (a quoted \"30\" makes Claude Code 2.1.x reject "
                "every hook in the file)", at(*hpath, "timeout"))
    for flag in ("once", "async", "asyncRewake"):
        if flag in handler and not isinstance(handler[flag], bool):
            chk.add("error", "hooks.flag", f"'{flag}' must be true or false", at(*hpath, flag))
    if handler.get("once") is True and owner not in ("skill",):
        chk.add("warning", "hooks.once-ignored", "'once' is honored only in skill frontmatter; here it is ignored",
                at(*hpath, "once"))
    if "shell" in handler and handler["shell"] not in spec.SHELLS:
        chk.add("error", "hooks.shell", f"shell must be one of: {', '.join(spec.SHELLS)}", at(*hpath, "shell"))
    if "args" in handler and (not isinstance(handler["args"], list)
                              or not all(isinstance(a, str) for a in handler["args"])):
        chk.add("error", "hooks.args", "'args' must be a list of strings", at(*hpath, "args"))
    condition = handler.get("if")
    if "if" in handler:
        if not isinstance(condition, str):
            chk.add("error", "hooks.if", "'if' must be one permission rule string such as \"Bash(git *)\"",
                    at(*hpath, "if"))
        elif event not in spec.TOOL_EVENTS:
            chk.add("error", "hooks.if-never-runs",
                    f"'if' only applies to tool events; on {event} a hook with 'if' never runs", at(*hpath, "if"))
    for issue in hook_safety.check_handler(handler, event, ctx):
        severity, code, message, hint = issue
        chk.add(severity, code, message, at(*hpath, "command") if "command" in handler else line, hint)


# ===========================================================================
# SKILLS AND COMMANDS
# ===========================================================================

def check_skill_like(chk: Checker) -> None:
    t, fm, kind = chk.t, chk.fm, chk.t.kind
    known = spec.SKILL_FIELDS if kind == "skill" else spec.COMMAND_FIELDS
    if kind == "skill" and t.path.name != "SKILL.md" and not chk.opts.allow_placeholders:
        chk.add("error", "skill.filename",
                f"skill file is named '{t.path.name}'; Claude Code loads 'SKILL.md' (case-sensitive on Linux)",
                1, hint="rename it to SKILL.md")
    if not fm.split.has_frontmatter:
        if not any(f.code.startswith("frontmatter.") for f in fm.findings):
            chk.add("warning" if kind == "command" and not chk.portable else "error", "frontmatter.missing",
                    f"no frontmatter: Claude Code loads this {kind} with no description, so it cannot decide "
                    "when to use it", 1, hint="start the file with ---, name, description, ---")
        check_body(chk, fm.split.body, 1, {})
        return
    data = fm.data if isinstance(fm.data, dict) else {}
    if chk.portable:
        for key in data:
            if key not in spec.PORTABLE_FIELDS:
                chk.add("error", "portable.field",
                        f"'{key}' is not an Agent Skills spec field; claude.ai upload and the Skills API reject "
                        "the skill (\"Unexpected key(s) in SKILL.md frontmatter\")", chk.at(key),
                        hint="run export_skill.py: it folds when_to_use into description and drops "
                             "Claude Code-only fields")
    check_unknown_fields(chk, data, known, spec.AGENT_FIELDS, kind)
    usable = check_types(chk, data, known)
    ok = lambda key: key in data and usable.get(key, False)

    # name
    if kind == "skill":
        name = data.get("name")
        if "name" not in data:
            if t.plugin_root_skill:
                chk.add("warning", "name.plugin-root",
                        "a plugin-root SKILL.md without 'name' is named after the install directory, which is a "
                        "version string for cached plugins", chk.at(), hint="add name:")
            else:
                chk.add("error" if chk.portable else "info", "name.missing",
                        "no 'name': Claude Code uses the directory name"
                        + ("; the Agent Skills spec requires name" if chk.portable else ""), fm.split.yaml_line)
        elif ok("name"):
            _check_skill_name(chk, name)

    # description + when_to_use
    description = data.get("description")
    if "description" not in data:
        chk.add("error" if (kind == "skill" or chk.portable) else "warning", "description.missing",
                "no 'description': Claude sees only the first line of the body when deciding whether to use "
                f"this {kind}", fm.split.yaml_line, hint="add description: \"Use when ...\"")
    elif ok("description"):
        _check_description(chk, description)
    when = data.get("when_to_use")
    if ok("when_to_use") and ok("description"):
        combined = len(description) + 1 + len(when)
        if combined > spec.LISTING_MAX:
            chk.add("warning", "listing.truncated",
                    f"description + when_to_use is {combined} characters; Claude Code truncates it at "
                    f"{spec.LISTING_MAX} in the skill listing", chk.at("when_to_use"),
                    hint="put the key use case first and trim the rest")
    if ok("when_to_use"):
        _check_placeholders_in(chk, "when_to_use", when)

    # invocation
    if claude_bool(data.get("disable-model-invocation")) is True and claude_bool(data.get("user-invocable")) is False:
        chk.add("error", "invocation.unreachable",
                "disable-model-invocation: true and user-invocable: false together mean nobody can invoke this",
                chk.at("user-invocable"))
    if ok("arguments"):
        _check_arguments(chk, data["arguments"], fm.split.body)
    for key, role in (("allowed-tools", "allow"), ("disallowed-tools", "deny")):
        if ok(key):
            check_tool_rules(chk, key, data[key], role)
    if ok("model"):
        check_model(chk, data["model"])
    fork = data.get("context") == "fork"
    if "agent" in data and ok("agent"):
        if not fork:
            chk.add("warning", "agent.without-fork", "'agent' only applies with context: fork; it is ignored",
                    chk.at("agent"), hint="add context: fork, or delete agent")
        elif data["agent"] not in spec.BUILTIN_AGENT_TYPES and data["agent"] not in chk.index.agents:
            chk.add("info", "agent.unknown",
                    f"agent '{data['agent']}' is not built in (Explore, Plan, general-purpose) and was not found "
                    "in the files checked; make sure a subagent with that name exists", chk.at("agent"))
    if "background" in data and not fork:
        chk.add("warning", "background.without-fork", "'background' only applies with context: fork",
                chk.at("background"))
    if ok("paths"):
        patterns = [p.strip() for p in (data["paths"].split(",") if isinstance(data["paths"], str)
                                        else data["paths"]) if isinstance(p, str) and p.strip()]
        if not patterns or all(p in ("**", "**/*") for p in patterns):
            chk.add("warning", "paths.noop", "'paths' matches everything, so it limits nothing", chk.at("paths"))
    if ok("metadata"):
        for mkey, mvalue in data["metadata"].items():
            if mkey in spec.SKILL_FIELDS:
                chk.add("warning", "metadata.shadow",
                        f"metadata.{mkey} reuses a frontmatter field name; Claude Code docs say not to",
                        chk.at("metadata", mkey))
            if chk.portable and not isinstance(mvalue, str):
                chk.add("error", "portable.metadata",
                        f"metadata.{mkey} must be a string in the Agent Skills spec", chk.at("metadata", mkey),
                        hint="quote the value")
    if ok("compatibility") and len(data["compatibility"]) > spec.COMPATIBILITY_MAX:
        chk.add("error", "compatibility.length",
                f"compatibility is {len(data['compatibility'])} characters (max {spec.COMPATIBILITY_MAX})",
                chk.at("compatibility"))
    if "hooks" in data and ok("hooks"):
        check_hooks(chk, data["hooks"], kind)
    body_empty = check_body(chk, fm.split.body, fm.split.body_line, data)
    if fork and body_empty:
        chk.add("error", "fork.empty",
                "context: fork runs the body as the subagent's task, and the body is empty", chk.at("context"))


def _check_skill_name(chk: Checker, name: str) -> None:
    line = chk.at("name")
    if chk.opts.allow_placeholders and spec.PLACEHOLDER_RE.search(name):
        return
    if spec.PLACEHOLDER_RE.search(name):
        chk.add("error", "placeholder", f"name still contains a template placeholder: {name}", line)
        return
    if len(name) > spec.NAME_MAX:
        chk.add("error", "name.length", f"name is {len(name)} characters (max {spec.NAME_MAX})", line)
    if not spec.SKILL_NAME_RE.match(name):
        chk.add("error", "name.format",
                f"name '{name}' must be lowercase letters, digits and single hyphens (no leading/trailing hyphen)",
                line, hint=f"try '{_kebab(name)}'")
    for word in spec.RESERVED_NAME_WORDS:
        if word in name.lower():
            chk.add("error" if chk.portable else "warning", "name.reserved",
                    f"name contains the reserved word '{word}'; the Skills API and claude.ai reject it", line)
    directory = chk.t.path.resolve().parent.name
    if chk.t.plugin_root_skill:
        return
    if name != directory:
        chk.add("error", "name.directory",
                f"name '{name}' differs from its directory '{directory}': the command is /{directory}, while "
                "listings show the name, and the Agent Skills spec requires them to match", line,
                hint=f"rename the directory or set name: {directory}")


def _kebab(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-") or "my-skill"


def _check_placeholders_in(chk: Checker, key: str, value: str) -> None:
    if not chk.opts.allow_placeholders and spec.PLACEHOLDER_RE.search(value):
        chk.add("error", "placeholder", f"{key} still contains a template placeholder", chk.at(key),
                hint="fill in every {{...}} before shipping")


def _check_description(chk: Checker, text: str) -> None:
    line = chk.at("description")
    if not text.strip():
        chk.add("error", "description.empty", "description is empty", line)
        return
    _check_placeholders_in(chk, "description", text)
    if len(text) > spec.DESCRIPTION_MAX:
        chk.add("error", "description.length",
                f"description is {len(text)} characters (max {spec.DESCRIPTION_MAX}: the Agent Skills spec, "
                "Skills API and claude.ai upload limit)", line, hint="move detail into when_to_use or the body")
    if spec.XML_TAG_RE.search(text):
        chk.add("error" if chk.portable else "warning", "description.xml",
                "description contains an XML/HTML tag; the Skills API rejects it and Claude Code escapes it",
                line)
    if "\n" in text.strip():
        chk.add("warning", "description.multiline", "description spans several lines; keep it on one line", line)
    lowered = text.lower()
    if chk.t.kind == "skill" and not any(marker in lowered for marker in spec.TRIGGER_MARKERS) \
            and not spec.PLACEHOLDER_RE.search(text):
        chk.add("warning", "description.trigger",
                "description has no trigger wording (e.g. 'Use when ...'); it is the only text Claude sees "
                "when deciding to load the skill", line, hint="start with: Use when <situations, phrasings>")
    if re.match(r"^\s*(I|We|You)\b", text) or re.search(r"\b(I can|I will|I'll|you can|we can)\b", text, re.I):
        chk.add("warning", "description.person", "write the description in the third person", line)


def _check_arguments(chk: Checker, value: Any, body: str) -> None:
    names = value.split() if isinstance(value, str) else [v for v in value if isinstance(v, str)]
    line = chk.at("arguments")
    seen = set()
    for name in names:
        if name.isdigit():
            chk.add("warning", "arguments.numeric", f"argument name '{name}' is numeric; Claude Code drops it",
                    line)
        elif not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            chk.add("warning", "arguments.name", f"argument name '{name}' cannot be used as ${name}", line)
        elif name in seen:
            chk.add("warning", "arguments.duplicate", f"argument '{name}' is declared twice", line)
        elif not re.search(r"\$" + re.escape(name) + r"(?![\w\[])", body):
            chk.add("info", "arguments.unused", f"argument '{name}' is declared but ${name} never appears", line)
        seen.add(name)


# ===========================================================================
# BODY
# ===========================================================================

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REF_LINK_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
_SCRIPT_RE = re.compile(
    r"(?:^|[\s`(!])(?P<interp>python3?|bash|sh|node|bun|ruby|perl|deno\s+run|uv\s+run)\s+"
    r"(?:-\S+\s+)*[\"']?(?P<path>(?:\$\{CLAUDE_(?:SKILL_DIR|PLUGIN_ROOT)\}/)?[\w@%+=:,.~/-]+"
    r"\.(?:py|sh|js|mjs|cjs|ts|rb|pl))")
_PLACEHOLDER_PATH_RE = re.compile(r"\$\{(CLAUDE_SKILL_DIR|CLAUDE_PLUGIN_ROOT)\}(/[\w@%+=:,.~/-]+)")


def scan_body(body: str) -> List[Tuple[int, str, bool]]:
    """Return (line offset, text, inside fenced code) for each body line."""
    out, fence = [], None
    for offset, line in enumerate(body.split("\n")):
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
            out.append((offset, line, True))
            continue
        if fence is not None:
            out.append((offset, line, True))
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                    and not line.strip()[len(match.group(1)):].strip():
                fence = None
            continue
        out.append((offset, line, False))
    return out


def check_body(chk: Checker, body: str, first_line: int, data: Dict[str, Any]) -> bool:
    """Check the markdown body. Returns True when the body is empty."""
    lines = scan_body(body)
    text_lines = [ln for _o, ln, code in lines]
    if not "".join(text_lines).strip():
        if chk.t.kind == "agent":
            chk.add("warning", "body.empty", "the body is the subagent's system prompt, and it is empty",
                    first_line)
        else:
            chk.add("warning", "body.empty", f"the {chk.t.kind} has no instructions after the frontmatter",
                    first_line)
        return True
    words = sum(len(ln.split()) for ln in text_lines)
    if len(text_lines) > spec.BODY_LINES_WARN:
        chk.add("warning", "body.lines",
                f"body is {len(text_lines)} lines (keep SKILL.md under {spec.BODY_LINES_WARN}); it loads on "
                "every invocation", first_line, hint="move depth into references/ files and link them")
    elif words > spec.BODY_WORDS_WARN and chk.t.kind != "agent":
        chk.add("warning", "body.words",
                f"body is {words} words (template budget {spec.BODY_WORDS_WARN})", first_line,
                hint="move depth into references/ files and link them")
    has_h1 = False
    for offset, line, in_code in lines:
        number = first_line + offset
        if not in_code:
            prose = re.sub(r"`[^`]*`", "", line)
            if re.match(r"^#\s+\S", line):
                has_h1 = True
            if not chk.opts.allow_placeholders and spec.PLACEHOLDER_RE.search(prose):
                chk.add("error", "placeholder", "body still contains a template placeholder "
                        f"{spec.PLACEHOLDER_RE.search(prose).group(0)}", number)
            if re.search(r"<details\b", prose, re.I):
                chk.add("warning", "body.details",
                        "<details> blocks save no tokens for an agent; use references/ files", number)
            if re.match(r"^#{1,6}\s+Triggers\s*$", line, re.I) and chk.t.kind != "agent":
                chk.add("warning", "body.triggers",
                        "a body 'Triggers' section does nothing: Claude only sees the description before "
                        "loading the skill", number, hint="move trigger phrases into description or when_to_use")
            if "Template notes (delete before shipping)" in line and not chk.opts.allow_placeholders:
                chk.add("warning", "body.template-notes", "template notes are still in the file", number)
            for match in list(_LINK_RE.finditer(prose)) + list(_REF_LINK_RE.finditer(prose)):
                _check_link(chk, match.group(1), number)
        for match in _SCRIPT_RE.finditer(line):
            _check_script_reference(chk, match.group("path"), number)
        for match in _PLACEHOLDER_PATH_RE.finditer(line):
            _check_placeholder_path(chk, match.group(1), match.group(2), number, "the body")
    if not has_h1 and chk.t.kind == "skill":
        chk.add("info", "body.title", "the body has no '# Title' heading", first_line)
    return False


def _check_link(chk: Checker, target: str, line: int) -> None:
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "$")) or "{{" in target:
        return
    clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not clean:
        return
    if clean.startswith("/"):
        chk.add("warning", "link.absolute", f"link '{target}' is an absolute path; it breaks on other machines",
                line)
        return
    resolved = (chk.base_dir / clean).resolve()
    if not resolved.exists():
        chk.add("error", "link.missing", f"link '{target}' points at a file that does not exist", line)
        return
    skill_root = chk.base_dir if chk.t.kind == "skill" else None
    if skill_root is not None and skill_root != resolved and skill_root not in resolved.parents:
        chk.add("warning", "link.outside", f"link '{target}' leaves the skill directory; it breaks when the "
                "skill is installed or uploaded on its own", line)


def _check_script_reference(chk: Checker, path_text: str, line: int) -> None:
    if path_text.startswith("${CLAUDE_"):
        return  # handled by the placeholder-path check
    if path_text.startswith(("/", "~", "$")):
        return
    local = (chk.base_dir / path_text).resolve()
    if chk.t.kind == "skill" and local.exists() and local.is_file():
        chk.add("warning", "script.relative",
                f"'{path_text}' is bundled with the skill but the command runs from the session's working "
                "directory, not the skill directory", line,
                hint=f"write ${{CLAUDE_SKILL_DIR}}/{path_text}")


# ===========================================================================
# SUBAGENTS
# ===========================================================================

def check_agent(chk: Checker) -> None:
    fm = chk.fm
    plugin_agent = chk.t.plugin is not None
    if not fm.split.has_frontmatter:
        if not any(f.code.startswith("frontmatter.") for f in fm.findings):
            chk.add("error", "frontmatter.missing",
                    "no frontmatter: Claude Code treats this agents/ file as documentation and skips it", 1)
        return
    data = fm.data if isinstance(fm.data, dict) else {}
    check_unknown_fields(chk, data, spec.AGENT_FIELDS, spec.SKILL_FIELDS, "agent")
    if "cacheTtl" in data:
        chk.add("error", "agent.cache-ttl", "cacheTtl belongs inside the experimental map", chk.at("cacheTtl"),
                hint="write experimental:\\n  cacheTtl: 1h")
    usable = check_types(chk, data, spec.AGENT_FIELDS)
    ok = lambda key: key in data and usable.get(key, False)
    name = data.get("name")
    if "name" not in data:
        chk.add("warning" if plugin_agent else "error", "agent.name-missing",
                "no 'name': " + ("the plugin loads it under its file name" if plugin_agent else
                                  "Claude Code treats the file as documentation and skips it"),
                fm.split.yaml_line)
    elif ok("name"):
        if name.startswith("-") or ":" in name:
            chk.add("error", "agent.name-invalid",
                    f"agent name '{name}' starts with '-' or contains ':'; Claude Code skips the file",
                    chk.at("name"))
        elif not spec.SKILL_NAME_RE.match(name) and not (chk.opts.allow_placeholders
                                                          and spec.PLACEHOLDER_RE.search(name)):
            chk.add("warning", "agent.name-format", f"agent name '{name}' is not lowercase-with-hyphens",
                    chk.at("name"))
        dupes = [t for t in chk.index.agents.get(name, []) if t is not chk.t and t.plugin == chk.t.plugin]
        if dupes:
            chk.add("error", "agent.name-duplicate",
                    f"agent name '{name}' is also used by {display(dupes[0].path)}; one shadows the other",
                    chk.at("name"))
    if "description" not in data:
        chk.add("warning" if plugin_agent else "error", "agent.description-missing",
                "no 'description': Claude Code skips the agent (or, in a plugin, gives it a generic one), "
                "so Claude never knows when to delegate", fm.split.yaml_line)
    elif ok("description"):
        text = data["description"]
        if not text.strip():
            chk.add("error", "description.empty", "description is empty", chk.at("description"))
        _check_placeholders_in(chk, "description", text)
    rules: List[str] = []
    if ok("tools"):
        rules = check_tool_rules(chk, "tools", data["tools"], "agent-allow")
        resolvable = [r for r in rules if r.split("(")[0] in spec.BUILTIN_TOOLS or r.startswith("mcp__")
                      or r.split("(")[0] in spec.LEGACY_TOOLS]
        if rules and not resolvable:
            chk.add("error", "agent.zero-tools",
                    "no entry in 'tools' names a real tool; Claude Code refuses to launch the subagent",
                    chk.at("tools"))
    if ok("disallowedTools"):
        check_tool_rules(chk, "disallowedTools", data["disallowedTools"], "agent-deny")
    if ok("model"):
        check_model(chk, data["model"])
    for key, note in spec.PLUGIN_AGENT_IGNORED.items():
        if plugin_agent and key in data and key != "hooks":
            chk.add("error", "agent.plugin-ignored", f"'{key}' is {note}", chk.at(key),
                    hint="copy the agent into .claude/agents/ to use it")
    if ok("permissionMode") and data["permissionMode"] == "bypassPermissions":
        chk.add("warning", "agent.bypass",
                "permissionMode: bypassPermissions skips permission prompts (Claude Code applies it only when "
                "the main session also bypasses)", chk.at("permissionMode"))
    if ok("skills"):
        _check_agent_skills(chk, data["skills"])
    if ok("mcpServers") and not plugin_agent:
        _check_mcp_servers_field(chk, data["mcpServers"])
    if "hooks" in data and ok("hooks"):
        check_hooks(chk, data["hooks"], "plugin-agent" if plugin_agent else "agent")
    if ok("experimental"):
        ttl = data["experimental"].get("cacheTtl")
        if ttl is not None and ttl not in spec.CACHE_TTLS:
            chk.add("warning", "agent.cache-ttl-value", f"experimental.cacheTtl must be one of "
                    f"{', '.join(spec.CACHE_TTLS)}; Claude Code ignores '{ttl}'", chk.at("experimental", "cacheTtl"))
    check_body(chk, fm.split.body, fm.split.body_line, data)


def _check_agent_skills(chk: Checker, value: Any) -> None:
    names = value if isinstance(value, list) else [s.strip() for s in value.split(",")]
    if isinstance(value, str):
        chk.add("warning", "agent.skills-string", "write 'skills' as a YAML list", chk.at("skills"))
    for index, name in enumerate(names):
        if not isinstance(name, str):
            continue
        line = chk.at("skills", index)
        target = chk.index.skills.get(name) or chk.index.skills.get(name.split(":")[-1])
        if target is None:
            chk.add("info", "agent.skill-unknown",
                    f"skill '{name}' was not found in the files checked (fine if it is a user or plugin skill; "
                    "a missing skill is skipped with only a debug-log warning)", line)
            continue
        if target.fm and target.fm.data.get("disable-model-invocation") is True:
            chk.add("error", "agent.skill-not-preloadable",
                    f"skill '{name}' sets disable-model-invocation: true, so it cannot be preloaded", line)


def _check_mcp_servers_field(chk: Checker, value: Any) -> None:
    if not isinstance(value, list):
        chk.add("error", "agent.mcp-type", "mcpServers must be a list of server names or {name: config} entries",
                chk.at("mcpServers"))
        return
    for index, entry in enumerate(value):
        line = chk.at("mcpServers", index)
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict) or len(entry) != 1:
            chk.add("error", "agent.mcp-entry",
                    "each mcpServers entry is a server name or a single '<name>: {config}' mapping", line)
            continue
        (server, config), = entry.items()
        for severity, code, message in check_mcp_config(server, config):
            chk.add(severity, code, message, line)


def check_mcp_config(server: str, config: Any) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    if not isinstance(config, dict):
        return [("error", "mcp.config", f"MCP server '{server}' must be a mapping")]
    transport = config.get("type", "stdio" if "command" in config else None)
    if transport not in spec.MCP_TRANSPORTS:
        out.append(("error", "mcp.type", f"MCP server '{server}' needs type stdio, http, sse or ws "
                    "(or a command for stdio)"))
        return out
    if transport == "stdio":
        if not isinstance(config.get("command"), str) or not config["command"].strip():
            out.append(("error", "mcp.command", f"stdio MCP server '{server}' needs a command"))
        if "args" in config and not (isinstance(config["args"], list) and all(isinstance(a, str) for a in config["args"])):
            out.append(("error", "mcp.args", f"MCP server '{server}': args must be a list of strings"))
        env = config.get("env", {})
        if isinstance(env, dict):
            for name, val in env.items():
                if isinstance(val, str) and hook_safety.looks_like_secret(name, val):
                    out.append(("warning", "mcp.literal-secret",
                                f"MCP server '{server}': env {name} holds a literal credential; use ${{{name}}}"))
    else:
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            out.append(("error", "mcp.url", f"{transport} MCP server '{server}' needs a url"))
        headers = config.get("headers", {})
        if isinstance(headers, dict):
            for name, val in headers.items():
                if isinstance(val, str) and hook_safety.looks_like_secret(name, val):
                    out.append(("warning", "mcp.literal-secret",
                                f"MCP server '{server}': header {name} holds a literal credential; use ${{VAR}}"))
    return out


# ===========================================================================
# PLUGINS
# ===========================================================================

def check_plugin(info: PluginInfo, engines: Engines, index: Index, opts: Options) -> List[Finding]:
    findings: List[Finding] = []
    manifest_path = info.root / ".claude-plugin" / "plugin.json"
    rel = display(manifest_path)

    def add(severity: str, code: str, message: str, hint: Optional[str] = None, path: str = rel) -> None:
        findings.append(Finding(severity, code, message, hint=hint, path=path))

    manifest = _load_json(manifest_path, add)
    if isinstance(manifest, dict):
        info.manifest = manifest
        name = manifest.get("name")
        if not isinstance(name, str) or not name:
            add("error", "plugin.name", "plugin.json needs a 'name'")
        else:
            info.name = name
            if opts.allow_placeholders and spec.PLACEHOLDER_RE.search(name):
                pass
            elif not spec.PLUGIN_NAME_RE.match(name):
                add("error", "plugin.name-format", f"plugin name '{name}' must be kebab-case (no spaces or capitals)")
        for key, value in manifest.items():
            if key not in spec.PLUGIN_MANIFEST_FIELDS:
                near = _nearest(key, spec.PLUGIN_MANIFEST_FIELDS)
                add("warning", "plugin.field", f"unknown plugin.json field '{key}' is ignored"
                    + (f"; did you mean '{near}'?" if near else ""))
        if "keywords" in manifest and not (isinstance(manifest["keywords"], list)
                                           and all(isinstance(k, str) for k in manifest["keywords"])):
            add("error", "plugin.keywords", "keywords must be a list of strings; anything else fails the plugin load")
        if "version" in manifest and not (isinstance(manifest["version"], str)
                                          and re.match(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$", manifest["version"])):
            add("warning", "plugin.version", "version should be a semantic version string such as \"1.2.0\"")
        for key in ("skills", "commands", "agents", "hooks", "mcpServers", "outputStyles", "lspServers", "workflows"):
            value = manifest.get(key)
            for ref in ([value] if isinstance(value, str) else value if isinstance(value, list) else []):
                if not isinstance(ref, str):
                    continue
                if ".." in Path(ref).parts or ref.startswith("/"):
                    add("error", "plugin.path-traversal", f"{key} path '{ref}' must stay inside the plugin root")
                elif not (info.root / ref).exists():
                    add("error", "plugin.path-missing", f"{key} path '{ref}' does not exist")
        inline = manifest.get("mcpServers")
        if isinstance(inline, dict):
            servers = inline.get("mcpServers", inline)
            if isinstance(servers, dict):
                info.mcp_servers.update(servers)
    for component in spec.PLUGIN_COMPONENT_DIRS:
        if (info.root / ".claude-plugin" / component).exists():
            add("error", "plugin.layout", f".claude-plugin/{component} is never loaded; component directories "
                "belong at the plugin root", hint=f"move it to {component}/")
    if (info.root / "CLAUDE.md").is_file():
        add("info", "plugin.claude-md", "a plugin's CLAUDE.md is not loaded as context; put instructions in a skill",
            path=display(info.root / "CLAUDE.md"))
    mcp_path = info.root / ".mcp.json"
    if mcp_path.is_file():
        mcp = _load_json(mcp_path, lambda s, c, m, h=None: findings.append(Finding(s, c, m, hint=h, path=display(mcp_path))))
        servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
        if not isinstance(servers, dict):
            findings.append(Finding("error", "mcp.file", ".mcp.json needs an 'mcpServers' object",
                                    path=display(mcp_path)))
        else:
            info.mcp_servers.update(servers)
            for server, config in servers.items():
                for severity, code, message in check_mcp_config(server, config):
                    findings.append(Finding(severity, code, message, path=display(mcp_path)))
    hooks_path = info.root / "hooks" / "hooks.json"
    if hooks_path.is_file():
        hooks_doc = _load_json(hooks_path, lambda s, c, m, h=None: findings.append(
            Finding(s, c, m, hint=h, path=display(hooks_path))))
        if isinstance(hooks_doc, dict):
            dummy = Target(path=hooks_path, kind="plugin-hooks", plugin=info)
            chk = Checker(dummy, opts, index)
            chk.fm = None
            chk.base_dir = info.root
            check_hooks(chk, hooks_doc.get("hooks"), "plugin-hooks", prefix=("hooks",),
                        line_of=lambda *path: None)
            findings.extend(chk.findings)
    return findings


def _load_json(path: Path, add) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        add("error", "json.encoding", f"{path.name} is not UTF-8")
    except ValueError as exc:
        add("error", "json.syntax", f"{path.name} is not valid JSON: {exc}")
    except OSError as exc:
        add("error", "json.read", f"cannot read {path.name}: {exc}")
    return None


# ===========================================================================
# DRIVER
# ===========================================================================

def validate(paths: List[Path], opts: Options, engines: Engines) -> Result:
    targets, plugins, problems = discover(list(paths), opts)
    index = Index()
    read_ok: List[Target] = []
    for target in targets:
        try:
            raw = target.path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                target.findings.append(Finding("error", "file.size", f"file is larger than {MAX_FILE_BYTES} bytes",
                                               path=display(target.path)))
                continue
            target.text = raw.decode("utf-8")
            read_ok.append(target)
        except UnicodeDecodeError:
            target.findings.append(Finding("error", "file.encoding", "file is not valid UTF-8; Claude Code reads "
                                           "skills as UTF-8", path=display(target.path)))
        except OSError as exc:
            target.findings.append(Finding("error", "file.read", f"cannot read file: {exc}", path=display(target.path)))
    for target, fm in zip(read_ok, load_frontmatters([t.text for t in read_ok], engines)):
        target.fm = fm
        for finding in fm.findings:
            finding.path = display(target.path)
        target.findings.extend(fm.findings)
        if target.kind == "skill":
            key = target.path.resolve().parent.name
            if target.plugin_root_skill and isinstance(fm.data.get("name"), str):
                key = fm.data["name"]
            index.skills.setdefault(key, target)
        elif target.kind == "agent" and isinstance(fm.data.get("name"), str):
            index.agents.setdefault(fm.data["name"], []).append(target)
    plugin_findings: List[Finding] = []
    for info in plugins.values():
        plugin_findings.extend(check_plugin(info, engines, index, opts))
    for target in read_ok:
        chk = Checker(target, opts, index)
        if target.kind == "agent":
            check_agent(chk)
        else:
            check_skill_like(chk)

    all_findings = problems + plugin_findings + [f for t in targets for f in t.findings]
    errors = [f for f in all_findings if f.severity == "error"]
    warnings = [f for f in all_findings if f.severity == "warning"]
    failed = bool(errors) or (opts.strict and bool(warnings))
    files = [{
        "path": display(t.path), "kind": t.kind,
        "plugin": t.plugin.name if t.plugin else None,
        "errors": sum(f.severity == "error" for f in t.findings),
        "warnings": sum(f.severity == "warning" for f in t.findings),
        "findings": [f.to_dict() for f in t.findings],
    } for t in targets]
    message = (f"{len(targets)} file(s), {len(plugins)} plugin(s): {len(errors)} error(s), "
               f"{len(warnings)} warning(s)")
    return Result(
        success=not failed and bool(targets or plugins),
        message=message if (targets or plugins) else "no skills, commands, agents or plugins found",
        data={"files": files, "plugins": [display(p.root) for p in plugins.values()],
              "other": [f.to_dict() for f in problems + plugin_findings],
              "parsers": engines.versions(), "target": opts.target, "strict": opts.strict},
        errors=[f.format() for f in errors],
        warnings=[f.format() for f in warnings])


def print_report(result: Result, all_findings: List[Finding], quiet: bool) -> None:
    by_path: Dict[str, List[Finding]] = {}
    for finding in all_findings:
        if quiet and finding.severity != "error":
            continue
        by_path.setdefault(finding.path or "(input)", []).append(finding)
    for path in sorted(by_path):
        print(path)
        for finding in sorted(by_path[path], key=lambda f: (f.line or 0, f.col or 0)):
            where = f"  {finding.line}:" if finding.line else "  -:"
            print(f"{where} {finding.severity}: {finding.message} [{finding.code}]")
            if finding.hint:
                print(f"      fix: {finding.hint}")
    parsers = result.data.get("parsers", {})
    print(f"{'OK' if result.success else 'FAILED'}: {result.message} "
          f"(parsers: {parsers.get('native', '?')}; {parsers.get('npm', '?')})")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Claude Code skills, commands, subagents and plugins the way Claude Code loads them",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s .claude/skills/deploy
  %(prog)s .claude/ --strict
  %(prog)s my-skill --target portable
  %(prog)s templates/skill-md-template.md --kind skill --allow-placeholders
        """)
    parser.add_argument("paths", nargs="+", type=Path, help="skill directories, .md files, plugin roots or trees")
    parser.add_argument("--kind", choices=("auto", "skill", "command", "agent"), default="auto",
                        help="file kind when it cannot be inferred from the path (default: auto)")
    parser.add_argument("--target", choices=("claude-code", "portable"), default="claude-code",
                        help="portable = also enforce the Agent Skills spec (claude.ai upload, Skills API)")
    parser.add_argument("--strict", action="store_true", help="fail on warnings too")
    parser.add_argument("--allow-placeholders", action="store_true",
                        help="accept {{placeholders}} (for checking templates)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    parser.add_argument("--quiet", "-q", action="store_true", help="print errors only")
    args = parser.parse_args(argv)

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"error: path not found: {path}", file=sys.stderr)
        return EXIT_NOT_FOUND
    opts = Options(target=args.target, strict=args.strict, allow_placeholders=args.allow_placeholders,
                   kind=args.kind)
    try:
        engines = Engines.locate()
        result = validate(list(args.paths), opts, engines)
    except EngineUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return 130

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        findings = [Finding(**{k: v for k, v in f.items()}) for f in result.data["other"]]
        for file_entry in result.data["files"]:
            findings.extend(Finding(**f) for f in file_entry["findings"])
        print_report(result, findings, args.quiet)
    if not result.data["files"] and not result.data["plugins"]:
        return EXIT_NOT_FOUND
    return EXIT_OK if result.success else EXIT_VALIDATION


if __name__ == "__main__":
    sys.exit(main())
