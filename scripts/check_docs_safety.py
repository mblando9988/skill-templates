#!/usr/bin/env python3
"""
check_docs_safety.py - Find unsafe or broken Claude Code hook commands in
skills, subagents, docs and hook configuration files.

Part of the skill-templates tooling.

Responsibilities:
- Check hooks declared in SKILL.md / command / subagent frontmatter.
- Check hook examples inside markdown code blocks (YAML frontmatter
  snippets and JSON settings snippets), so docs never teach a bad pattern.
- Check hooks/hooks.json and .claude/settings*.json files.
- Flag, per hook_safety.py: nonexistent $TOOL_INPUT-style variables, tool
  payload spliced into shell text, eval / pipe-to-shell, cwd-relative script
  paths, unquoted path placeholders, scripts that cannot start, and gate
  scripts that can never block (exit 1 does not block; only exit 2 or a
  JSON deny decision does, and PermissionRequest honors only the JSON).

Usage:
    python3 check_docs_safety.py PATH [PATH ...] [--strict] [--json] [--quiet]

Examples:
    python3 check_docs_safety.py .claude/skills/guarded-shell
    python3 check_docs_safety.py README.md references/
    python3 check_docs_safety.py my-plugin/ --strict

Exit Codes:
    0  - No unsafe hook commands (warnings allowed unless --strict)
    1  - General failure (unreadable input, parser bridge failed)
    2  - Invalid arguments
    3  - Path not found
    4  - Required tool missing (bun, node, or the pinned yaml package)
    10 - Unsafe hook commands found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_spec as spec  # noqa: E402
import hook_safety  # noqa: E402
from claude_frontmatter import (  # noqa: E402
    EXIT_DEPENDENCY, EngineError, EngineUnavailable, Engines, Finding, iter_files, load_frontmatters,
)
from validate_skill import display, enclosing_plugin, infer_kind, scan_body  # noqa: E402

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ARGS = 2
EXIT_NOT_FOUND = 3
EXIT_UNSAFE = 10
MAX_FILE_BYTES = 1024 * 1024
_CODE_LANGS = {"", "yaml", "yml", "json", "jsonc", "json5", "markdown", "md"}
_LINE_COMMAND_RE = re.compile(r"[\"']?command[\"']?\s*:\s*(?P<cmd>.+)$")


@dataclass
class Result:
    success: bool
    message: str
    data: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"success": self.success, "message": self.message, "data": self.data,
                "errors": self.errors, "warnings": self.warnings,
                "timestamp": datetime.now(timezone.utc).isoformat()}


@dataclass
class Snippet:
    """A hooks-bearing text found in a file."""
    path: Path
    line: int                  # file line where the snippet starts
    kind: str                  # frontmatter | yaml-block | json-block | json-file
    text: str
    ctx: hook_safety.HookContext


# ===========================================================================
# COLLECTION
# ===========================================================================

def collect(paths: Iterable[Path]) -> Tuple[List[Path], List[Path]]:
    markdown, json_files = [], []
    for path in iter_files(paths):
        if path.suffix.lower() == ".md":
            markdown.append(path)
        elif path.name == "hooks.json" or re.match(r"settings(\.local)?\.json$", path.name):
            json_files.append(path)
    return markdown, json_files


def context_for(path: Path, kind: str) -> hook_safety.HookContext:
    plugin = enclosing_plugin(path.resolve())
    return hook_safety.HookContext(kind=kind, base_dir=path.resolve().parent,
                                   project_dir=hook_safety.guess_project_dir(path), plugin_root=plugin)


def snippets_from_markdown(path: Path, text: str) -> Tuple[List[Snippet], List[Tuple[int, str]]]:
    """Return hook-bearing code blocks and loose `command:` lines."""
    snippets: List[Snippet] = []
    loose: List[Tuple[int, str]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    body_start = 0
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if close is not None:
            body_start = close + 1
    body = "\n".join(lines[body_start:])
    scanned = scan_body(body)
    block: List[str] = []
    lang = None
    start = 0
    docs_ctx = context_for(path, "docs")
    for offset, line, in_code in scanned:
        number = body_start + offset + 1
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)", line)
        if in_code and fence and lang is None:
            lang, start, block = fence.group(2).lower(), number + 1, []
            continue
        if in_code and fence and lang is not None and not line.strip()[len(fence.group(1)):].strip():
            if lang in _CODE_LANGS and "hooks" in "\n".join(block):
                content = "\n".join(block) + "\n"
                kind = "json-block" if lang.startswith("json") or content.lstrip().startswith("{") else "yaml-block"
                snippets.append(Snippet(path, start, kind, content, docs_ctx))
            elif block and "command" in "\n".join(block):
                for index, code_line in enumerate(block):
                    loose.append((start + index, code_line))
            lang, block = None, []
            continue
        if lang is not None:
            block.append(line)
        elif "command" in line:
            loose.append((number, line))
    return snippets, loose


# ===========================================================================
# ANALYSIS
# ===========================================================================

def find_handlers(data: Any, path: Tuple[Any, ...] = ()) -> Iterable[Tuple[str, Tuple[Any, ...], Dict[str, Any]]]:
    """Yield (event, path, handler) for every hook handler anywhere in data."""
    if isinstance(data, dict):
        if data.get("type") in spec.HOOK_HANDLERS and ("command" in data or "prompt" in data or "url" in data):
            event = next((str(p) for p in reversed(path) if str(p) in spec.HOOK_EVENTS), "PreToolUse")
            yield event, path, data
            return
        for key, value in data.items():
            yield from find_handlers(value, path + (key,))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from find_handlers(item, path + (index,))


def strip_frontmatter_fences(text: str) -> str:
    """A YAML example may itself be a whole '---' frontmatter block."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        return "\n".join(lines[1:close]) + "\n"
    return text


def analyze(paths: List[Path], engines: Engines) -> Tuple[List[Finding], int]:
    markdown, json_files = collect(paths)
    findings: List[Finding] = []
    texts: Dict[Path, str] = {}
    for path in markdown + json_files:
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                findings.append(Finding("error", "file.size", "file is larger than 1 MiB", path=display(path)))
                continue
            texts[path] = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(Finding("error", "file.read", f"cannot read file: {exc}", path=display(path)))

    # 1. Frontmatter hooks in skills, commands and agents.
    component_files = [p for p in markdown if p in texts and infer_kind(p) is not None]
    for path, fm in zip(component_files, load_frontmatters([texts[p] for p in component_files], engines)):
        hooks = fm.data.get("hooks") if isinstance(fm.data, dict) else None
        if isinstance(hooks, dict):
            kind = infer_kind(path) or "skill"
            ctx = context_for(path, kind)
            for event, hpath, handler in find_handlers(hooks):
                key = "command" if "command" in handler else ("prompt" if "prompt" in handler else None)
                line = fm.line_of("hooks", *hpath, *([key] if key else [])) or fm.split.yaml_line
                _report(findings, path, line, hook_safety.check_handler(handler, event, ctx))

    # 2. Hook examples in markdown code blocks.
    yaml_snippets: List[Snippet] = []
    for path in markdown:
        if path not in texts:
            continue
        snippets, loose = snippets_from_markdown(path, texts[path])
        for snippet in snippets:
            if snippet.kind == "json-block":
                try:
                    data = json.loads(re.sub(r"^\s*//.*$", "", snippet.text, flags=re.M))
                except ValueError:
                    loose.extend((snippet.line + i, ln) for i, ln in enumerate(snippet.text.split("\n")))
                    continue
                for event, _p, handler in find_handlers(data):
                    _report(findings, path, snippet.line, hook_safety.check_handler(handler, event, snippet.ctx))
            else:
                yaml_snippets.append(snippet)
        for number, line in loose:
            _check_loose_line(findings, path, number, line)
    if yaml_snippets:
        parsed = engines.parse_many([strip_frontmatter_fences(s.text) for s in yaml_snippets], positions=False)
        for snippet, (npm, _native) in zip(yaml_snippets, parsed):
            if not npm.ok:
                for index, line in enumerate(snippet.text.split("\n")):
                    _check_loose_line(findings, snippet.path, snippet.line + index, line)
                continue
            for event, _p, handler in find_handlers(npm.value):
                _report(findings, snippet.path, snippet.line,
                        hook_safety.check_handler(handler, event, snippet.ctx))

    # 3. hooks.json and settings files.
    for path in json_files:
        if path not in texts:
            continue
        try:
            data = json.loads(texts[path])
        except ValueError as exc:
            findings.append(Finding("error", "json.syntax", f"not valid JSON: {exc}", path=display(path)))
            continue
        kind = "plugin-hooks" if path.name == "hooks.json" else "settings"
        ctx = context_for(path, kind)
        if kind == "plugin-hooks" and ctx.plugin_root is None:
            ctx.plugin_root = path.resolve().parent.parent
        hooks = data.get("hooks") if isinstance(data, dict) else None
        for event, _p, handler in find_handlers(hooks or {}):
            _report(findings, path, None, hook_safety.check_handler(handler, event, ctx))
    return _dedupe(findings), len(texts)


def _check_loose_line(findings: List[Finding], path: Path, number: int, line: str) -> None:
    """Line-level fallback for examples that are not parseable YAML/JSON."""
    match = _LINE_COMMAND_RE.search(line)
    if not match:
        return
    command = match.group("cmd").strip().rstrip(",").strip()
    if len(command) >= 2 and command[0] == command[-1] and command[0] in "\"'":
        command = command[1:-1].replace('\\"', '"')
    ctx = hook_safety.HookContext(kind="docs")
    issues = [i for i in hook_safety.check_command(command, None, "PreToolUse", ctx)
              if i[1] in ("hook.payload-var", "hook.payload-interpolation", "hook.exec-payload")]
    _report(findings, path, number, issues)


def _report(findings: List[Finding], path: Path, line: Optional[int], issues) -> None:
    for severity, code, message, hint in issues:
        findings.append(Finding(severity, code, message, line=line, hint=hint, path=display(path)))


def _dedupe(findings: List[Finding]) -> List[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.path, f.line, f.code, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find unsafe or broken Claude Code hook commands in skills, agents, docs and hook files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s .claude/skills/guarded-shell
  %(prog)s README.md references/
  %(prog)s my-plugin/ --strict
        """)
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories to scan")
    parser.add_argument("--strict", action="store_true", help="fail on warnings too")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    parser.add_argument("--quiet", "-q", action="store_true", help="print errors only")
    args = parser.parse_args(argv)

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"error: path not found: {path}", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        engines = Engines.locate()
        findings, scanned = analyze(list(args.paths), engines)
    except EngineUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    unreadable = [f for f in errors if f.code in ("file.read", "file.size")]
    failed = bool(errors) or (args.strict and bool(warnings))
    result = Result(
        success=not failed,
        message=f"{scanned} file(s) scanned: {len(errors)} error(s), {len(warnings)} warning(s)",
        data={"findings": [f.to_dict() for f in findings], "strict": args.strict},
        errors=[f.format() for f in errors], warnings=[f.format() for f in warnings])
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for finding in sorted(findings, key=lambda f: (f.path or "", f.line or 0)):
            if args.quiet and finding.severity != "error":
                continue
            print(finding.format())
        print(("OK: " if result.success else "FAILED: ") + result.message)
    if unreadable and len(unreadable) == len(errors):
        return EXIT_FAILURE
    return EXIT_OK if result.success else EXIT_UNSAFE


if __name__ == "__main__":
    sys.exit(main())
