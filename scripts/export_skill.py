#!/usr/bin/env python3
"""
export_skill.py - Export a Claude Code skill for claude.ai upload, the Skills
API and other Agent Skills runtimes.

Part of the skill-templates tooling.

Claude Code reads fields those targets reject ("Unexpected key(s) in
SKILL.md frontmatter"). This script fills that gap instead of making you
maintain two copies:
- folds when_to_use into description (the one routing field every target
  reads), keeping it within the 1,024-character limit;
- turns an allowed-tools list into the space-separated string the spec uses;
- drops Claude Code-only fields (hooks, model, context, ...) and reports what
  each loss means, or with --keep-claude-fields stores them as JSON strings
  under metadata so nothing is lost;
- copies the rest of the skill (no symlinks, no hidden files), verifies the
  new frontmatter with both YAML parsers, and validates the result with
  `validate_skill.py --target portable`.

Usage:
    python3 export_skill.py SKILL_DIR --out DIR [--zip] [--force] [--keep-claude-fields] [--json]

Examples:
    python3 export_skill.py .claude/skills/deploy --out dist/
    python3 export_skill.py .claude/skills/deploy --out dist/ --zip

Exit Codes:
    0  - Exported, and the export passes portable validation
    1  - General failure (unreadable skill, cannot write output)
    2  - Invalid arguments
    3  - Skill not found
    4  - Required tool missing (bun, node, or the pinned yaml package)
    10 - The export is not portable (see the reported errors); nothing was written
    11 - Verification failed (the new frontmatter did not read back identically)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_spec as spec  # noqa: E402
from claude_frontmatter import (  # noqa: E402
    EXIT_DEPENDENCY, EngineError, EngineUnavailable, Engines, load_frontmatters, same_value, split_tool_rules,
)
from validate_skill import Options, display, validate  # noqa: E402

EXIT_OK, EXIT_FAILURE, EXIT_ARGS, EXIT_NOT_FOUND, EXIT_NOT_PORTABLE, EXIT_VERIFY = 0, 1, 2, 3, 10, 11

# What each dropped field means outside Claude Code.
LOSSES = {
    "when_to_use": "folded into description",
    "argument-hint": "no autocomplete hint",
    "arguments": "$name placeholders reach Claude as literal text",
    "disable-model-invocation": "Claude may load the skill automatically",
    "user-invocable": "the skill is not hidden",
    "disallowed-tools": "no tools are removed while it runs",
    "model": "runs on the surface's default model",
    "effort": "runs at the default effort",
    "context": "runs inline, not in a subagent",
    "agent": "no subagent type",
    "background": "no background run",
    "hooks": "its hooks never run",
    "paths": "activation is not limited to matching files",
    "shell": "no shell override",
    "version": "move it under metadata.version",
}


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


def portable_frontmatter(data: Dict[str, Any], skill_dir: Path, keep: bool) -> tuple:
    """Return (new data, notes, errors)."""
    notes: List[str] = []
    errors: List[str] = []
    out: Dict[str, Any] = {}
    out["name"] = data.get("name") if isinstance(data.get("name"), str) else skill_dir.name
    if "name" not in data:
        notes.append(f"name: filled in from the directory name '{skill_dir.name}'")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description is missing; every target needs it to decide when to use the skill")
        description = ""
    when = data.get("when_to_use")
    if isinstance(when, str) and when.strip():
        text = description.rstrip()
        joiner = " " if not text or text[-1] in ".!?" else ". "
        description = f"{text}{joiner}{when.strip()}"
        notes.append("when_to_use: folded into description")
    if len(description) > spec.DESCRIPTION_MAX:
        errors.append(f"description + when_to_use is {len(description)} characters; the limit is "
                      f"{spec.DESCRIPTION_MAX} (trim {len(description) - spec.DESCRIPTION_MAX})")
    out["description"] = description.strip()
    for key in ("license", "compatibility"):
        if key in data:
            out[key] = data[key]
    rules = split_tool_rules(data.get("allowed-tools")) if "allowed-tools" in data else None
    if rules:
        out["allowed-tools"] = " ".join(rules)
        if any("CLAUDE_SKILL_DIR" in r or "CLAUDE_PLUGIN_ROOT" in r for r in rules):
            notes.append("allowed-tools: ${CLAUDE_*} placeholders are only substituted in Claude Code")
    metadata: Dict[str, str] = {}
    if isinstance(data.get("metadata"), dict):
        for key, value in data["metadata"].items():
            metadata[str(key)] = value if isinstance(value, str) else json.dumps(value)
    for key, value in data.items():
        if key in spec.PORTABLE_FIELDS or key == "when_to_use":
            continue
        loss = LOSSES.get(key, "not an Agent Skills field")
        if keep:
            metadata[f"claude-code-{key}"] = json.dumps(value)
            notes.append(f"{key}: kept as metadata.claude-code-{key} ({loss} on other surfaces)")
        else:
            notes.append(f"{key}: dropped ({loss})")
    if metadata:
        out["metadata"] = metadata
    return out, notes, errors


def copy_skill(src: Path, dest: Path, warnings: List[str]) -> None:
    for root, dirs, files in os.walk(src, followlinks=False):
        rel = Path(root).relative_to(src)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules"))
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            path = Path(root) / name
            if name.startswith(".") or name.endswith(".pyc") or (rel == Path(".") and name == "SKILL.md"):
                continue
            if path.is_symlink():
                warnings.append(f"skipped symlink {path.relative_to(src)} (not copied into an upload)")
                continue
            shutil.copy2(path, dest / rel / name)


def body_warnings(body: str) -> List[str]:
    out = []
    if "!`" in body or "```!" in body:
        out.append("body uses !`command` injection, which only runs in Claude Code")
    for token in ("${CLAUDE_SKILL_DIR}", "${CLAUDE_SESSION_ID}", "${CLAUDE_PROJECT_DIR}", "$ARGUMENTS"):
        if token in body:
            out.append(f"body uses {token}, which reaches Claude as literal text outside Claude Code")
    return out


def export(skill_dir: Path, out_dir: Path, engines: Engines, make_zip: bool, force: bool, keep: bool) -> tuple:
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fm = load_frontmatters([text], engines)[0]
    result = Result(False, "")
    if not fm.parsed:
        result.errors = [f.format() for f in fm.findings if f.severity == "error"] or \
                        ["frontmatter does not load the same way in both Claude Code builds"]
        result.message = "fix the frontmatter first (run validate_skill.py)"
        return result, EXIT_NOT_PORTABLE
    data, notes, errors = portable_frontmatter(fm.data, skill_dir, keep)
    warnings = body_warnings(fm.split.body)
    if errors:
        result.errors, result.message = errors, "the skill cannot be exported as is"
        return result, EXIT_NOT_PORTABLE
    emitted = engines.stringify_many([{"data": data, "doubleQuote": [["description"]]}])[0]
    if not emitted.get("ok"):
        result.errors, result.message = [str(emitted.get("error"))], "could not write the frontmatter"
        return result, EXIT_FAILURE
    yaml_text = emitted["text"]
    (npm, native), = engines.parse_many([yaml_text], positions=False)
    if not (npm.ok and native.ok and same_value(npm.value, data) and same_value(native.value, data)):
        result.errors, result.message = ["new frontmatter does not read back identically"], "verification failed"
        return result, EXIT_VERIFY
    name = data["name"]
    final = out_dir / name
    if final.exists() and not force:
        result.errors, result.message = [f"{display(final)} exists (use --force to replace it)"], "not written"
        return result, EXIT_FAILURE
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=str(out_dir)))
    try:
        stage = staging / name
        copy_skill(skill_dir, stage, warnings)
        body = fm.split.body if fm.split.body.startswith("\n") else "\n" + fm.split.body
        (stage / "SKILL.md").write_text("---\n" + yaml_text + "---\n" + body, encoding="utf-8")
        check = validate([stage], Options(target="portable"), engines)
        blocking = [e for e in check.errors if "[name.directory]" not in e]
        if blocking:
            result.errors, result.message = blocking, "the export does not pass portable validation"
            return result, EXIT_NOT_PORTABLE
        if final.exists():
            shutil.rmtree(final)
        os.replace(stage, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    archive = None
    if make_zip:
        archive = out_dir / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(final.rglob("*")):
                if path.is_file():
                    zf.write(path, Path(name) / path.relative_to(final))
    result.success = True
    result.message = f"exported {name} to {display(final)}" + (f" and {display(archive)}" if archive else "")
    result.data = {"skill": name, "path": display(final), "zip": display(archive) if archive else None,
                   "changes": notes, "frontmatter": data}
    result.warnings = warnings
    return result, EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a Claude Code skill for claude.ai upload, the Skills API and Agent Skills runtimes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s .claude/skills/deploy --out dist/
  %(prog)s .claude/skills/deploy --out dist/ --zip --keep-claude-fields
        """)
    parser.add_argument("skill", type=Path, help="skill directory containing SKILL.md")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--zip", action="store_true", help="also write <name>.zip with the skill folder inside")
    parser.add_argument("--force", action="store_true", help="replace an existing export")
    parser.add_argument("--keep-claude-fields", action="store_true",
                        help="store Claude Code-only fields under metadata instead of dropping them")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    if not (args.skill / "SKILL.md").is_file():
        print(f"error: {args.skill}/SKILL.md not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        engines = Engines.locate()
        result, code = export(args.skill.resolve(), args.out, engines, args.zip, args.force,
                              args.keep_claude_fields)
    except EngineUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except (EngineError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for note in result.data.get("changes", []):
            print(f"  {note}")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        for error in result.errors:
            print(f"  error: {error}", file=sys.stderr)
        print(("OK: " if result.success else "FAILED: ") + result.message)
    return code


if __name__ == "__main__":
    sys.exit(main())
