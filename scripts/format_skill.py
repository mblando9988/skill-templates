#!/usr/bin/env python3
"""
format_skill.py - Rewrite Claude Code skills, commands and subagents into one
canonical layout without changing what Claude Code loads.

Part of the skill-templates tooling.

Responsibilities:
- Frontmatter: canonical key order, 2-space indentation, double-quoted
  prose fields (description, when_to_use, ...), plain values where every
  parser agrees, true/false for boolean fields, comments kept. The YAML is
  rewritten by eemeli/yaml's Document API (the parser npm builds of Claude
  Code use) through scripts/yaml_bridge.mjs.
- Repairs: strips a BOM, converts CRLF, removes blank lines above the
  opening '---', and quotes values that only one of Claude Code's parsers
  reads correctly (e.g. `description: wait...`, which native builds split
  into two YAML documents).
- Body: trims trailing whitespace (keeping Markdown hard breaks), collapses
  runs of blank lines, ends the file with one newline. Code blocks are left
  byte-for-byte alone.
- Self-verification before any write: both parsers (Bun.YAML and eemeli/yaml)
  must read the new frontmatter as exactly the intended data, formatting the
  output again must change nothing, and the body text must be unchanged apart
  from whitespace. Otherwise the file is left untouched (exit 11).

Usage:
    python3 format_skill.py PATH [PATH ...] [--check | --diff] [--kind auto|skill|command|agent]
                            [--json] [--quiet]

Examples:
    python3 format_skill.py .claude/skills/deploy       # rewrite in place
    python3 format_skill.py .claude/ --check            # CI: fail if anything would change
    python3 format_skill.py SKILL.md --diff             # show the changes, write nothing

Exit Codes:
    0  - Nothing to change, or all changes written
    1  - General failure (a file could not be read or parsed; it was skipped)
    2  - Invalid arguments
    3  - Path not found
    4  - Required tool missing (bun, node, or the pinned yaml package)
    10 - --check / --diff: at least one file would change
    11 - Verification failed: a rewrite was refused, nothing written for that file
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_spec as spec  # noqa: E402
from claude_frontmatter import (  # noqa: E402
    EXIT_DEPENDENCY, EngineError, EngineUnavailable, Engines, ParseResult, claude_bool,
    claude_code_fallback_quote, same_value, split_frontmatter, split_tool_rules,
)
from validate_skill import Options, discover, display, scan_body  # noqa: E402

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ARGS = 2
EXIT_NOT_FOUND = 3
EXIT_CHANGES = 10
EXIT_VERIFY = 11
MAX_FILE_BYTES = 1024 * 1024


# ===========================================================================
# RESULT TYPE (script template)
# ===========================================================================

@dataclass
class Result:
    success: bool
    message: str
    data: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict:
        return {"success": self.success, "message": self.message, "data": self.data,
                "errors": self.errors, "warnings": self.warnings,
                "timestamp": datetime.now(timezone.utc).isoformat()}


@dataclass
class FileJob:
    path: Path
    kind: str
    original: str = ""
    new_text: Optional[str] = None
    status: str = "pending"        # unchanged | changed | error | refused | skipped
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # frontmatter work
    yaml_text: Optional[str] = None
    intended: Optional[Dict[str, Any]] = None
    request: Optional[dict] = None
    body: str = ""
    yaml_out: Optional[str] = None


# ===========================================================================
# POLICY (what "canonical" means per file kind)
# ===========================================================================

def policy(kind: str) -> Dict[str, Tuple[str, ...]]:
    if kind == "agent":
        return {"order": spec.AGENT_KEY_ORDER, "prose": spec.AGENT_PROSE,
                "booleans": spec.AGENT_BOOLEANS, "tools": spec.AGENT_TOOL_LISTS}
    return {"order": spec.SKILL_KEY_ORDER, "prose": spec.SKILL_PROSE,
            "booleans": spec.SKILL_BOOLEANS, "tools": spec.SKILL_TOOL_LISTS}


def semantic_edits(data: Dict[str, Any], kind: str) -> Tuple[List[dict], Dict[str, Any]]:
    """Value rewrites that keep Claude Code's meaning. Returns (edits, expected data)."""
    rules = policy(kind)
    expected = dict(data)
    edits: List[dict] = []
    for key in rules["booleans"]:
        value = data.get(key)
        meaning = claude_bool(value)
        if meaning is not None and not isinstance(value, bool):
            edits.append({"path": [key], "value": meaning})
            expected[key] = meaning
    for key in rules["tools"]:
        value = data.get(key)
        if isinstance(value, str):
            tokens = split_tool_rules(value) or []
            joiner = ", " if _top_level_comma(value) else " "
            canonical = joiner.join(tokens)
            if canonical and canonical != value:
                edits.append({"path": [key], "value": canonical})
                expected[key] = canonical
    for key in rules["prose"]:
        value = data.get(key)
        if isinstance(value, str) and value != value.strip():
            edits.append({"path": [key], "value": value.strip()})
            expected[key] = value.strip()
    return edits, expected


def _top_level_comma(text: str) -> bool:
    inside = False
    for ch in text:
        if ch == "(":
            inside = True
        elif ch == ")":
            inside = False
        elif ch == "," and not inside:
            return True
    return False


# ===========================================================================
# BODY
# ===========================================================================

def format_body(body: str) -> str:
    """Trim trailing whitespace and extra blank lines outside fenced code."""
    scanned = scan_body(body)
    out: List[Tuple[str, bool]] = []
    for index, (_offset, line, in_code) in enumerate(scanned):
        if in_code:
            out.append((line, True))
            continue
        stripped = line.rstrip(" \t")
        following = scanned[index + 1] if index + 1 < len(scanned) else None
        trailing_spaces = len(line) - len(line.rstrip(" "))
        if (stripped and trailing_spaces >= 2 and line.rstrip(" ") == stripped
                and following is not None and following[1].strip() and not following[2]):
            stripped += "  "                       # Markdown hard line break
        if not stripped and out and out[-1] == ("", False):
            continue                               # collapse blank runs
        out.append((stripped, False))
    while out and out[0] == ("", False):
        out.pop(0)
    while out and out[-1] == ("", False):
        out.pop()
    return "\n".join(line for line, _code in out) + ("\n" if out else "")


def code_blocks(body: str) -> List[str]:
    blocks, current = [], []
    for _o, line, in_code in scan_body(body):
        if in_code:
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def same_prose(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a).strip() == re.sub(r"\s+", " ", b).strip() and code_blocks(a) == code_blocks(b)


# ===========================================================================
# FRONTMATTER
# ===========================================================================

def prepare(job: FileJob, engines: Engines) -> None:
    """Split the file and work out the intended frontmatter data."""
    text = job.original
    split = split_frontmatter(text)
    if split.had_bom:
        job.notes.append("removed the byte order mark")
    if split.newline == "\r\n" or re.search(r"\r(?!\n)", text):
        job.notes.append("converted line endings to LF")
    if not split.has_frontmatter:
        repaired = _strip_leading_blank_lines(split.text)
        if repaired is not None:
            job.notes.append("removed blank lines above the opening '---'")
            split = split_frontmatter(repaired)
    if not split.has_frontmatter:
        if any(f.code in ("frontmatter.unterminated", "frontmatter.opening-line") for f in split.findings):
            job.status, job.error = "error", "frontmatter is not closed; fix it by hand first"
            return
        job.body = split.text
        return
    job.body = split.body
    job.yaml_text = split.yaml_text or ""


def _strip_leading_blank_lines(text: str) -> Optional[str]:
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first and re.match(r"---[ \t]*$", lines[first]):
        return "\n".join(lines[first:])
    return None


def resolve_intent(jobs: List[FileJob], engines: Engines) -> None:
    """Parse every frontmatter with both parsers and decide the intended data.

    YAML that npm builds reject is repaired one line at a time: the line the
    parser reports gets Claude Code's own fallback quoting (what v2.1.x does
    after a failed parse), and parsing is retried. Lines that parse keep
    their meaning, so comments stay comments.
    """
    todo = [j for j in jobs if j.status == "pending" and j.yaml_text is not None]
    repaired = set()
    for _round in range(12):
        if not todo:
            return
        results = engines.parse_many([j.yaml_text for j in todo], positions=False)
        retry: List[FileJob] = []
        for job, (npm, native) in zip(todo, results):
            if npm.ok:
                if id(job) in repaired:
                    job.notes.append("quoted values that only loaded through Claude Code's fallback parser")
                _accept(job, npm, native)
                continue
            if npm.code == "DUPLICATE_KEY":
                job.status, job.error = "error", "duplicate keys: decide which value to keep by hand"
                continue
            fixed = None
            for line_no in (npm.line, (npm.line or 0) - 1):
                fixed = _fallback_line(job.yaml_text, line_no) if line_no else None
                if fixed is not None:
                    break
            if fixed is None:
                job.status = "error"
                job.error = f"frontmatter YAML does not parse: {npm.message} (frontmatter line {npm.line})"
                continue
            job.yaml_text = fixed
            repaired.add(id(job))
            retry.append(job)
        todo = retry
    for job in todo:
        job.status, job.error = "error", "frontmatter YAML still does not parse after quoting; fix it by hand"


def _fallback_line(yaml_text: str, line_no: int) -> Optional[str]:
    """Apply Claude Code's fallback quoting to one line, or None if it changes nothing."""
    lines = yaml_text.split("\n")
    if not 1 <= line_no <= len(lines):
        return None
    fixed = claude_code_fallback_quote(lines[line_no - 1])
    if fixed == lines[line_no - 1]:
        return None
    lines[line_no - 1] = fixed
    return "\n".join(lines)


def _accept(job: FileJob, npm: ParseResult, native: ParseResult) -> None:
    if npm.value is None:
        job.intended = {}
    elif not isinstance(npm.value, dict):
        job.status, job.error = "error", "frontmatter is not a set of key: value fields"
        return
    else:
        job.intended = npm.value
    if not (native.ok and same_value(native.value if native.value is not None else {}, job.intended)):
        job.notes.append("quoted values that native Claude Code builds (Bun.YAML) misread")
    edits, expected = semantic_edits(job.intended, job.kind)
    for edit in edits:
        job.notes.append(f"normalized {edit['path'][0]}")
    job.intended = expected
    rules = policy(job.kind)
    job.request = {
        "text": job.yaml_text,
        "keyOrder": list(rules["order"]),
        "edits": edits,
        "doubleQuote": [[key] for key in rules["prose"] if isinstance(job.intended.get(key), str)],
        "quoteAll": [],
    }


def emit_frontmatter(jobs: List[FileJob], engines: Engines) -> None:
    """Format, verify with both parsers, and escalate quoting until they agree."""
    active = [j for j in jobs if j.status == "pending" and j.request is not None]
    for attempt in range(3):
        if not active:
            return
        outputs = engines.format_many([j.request for j in active])
        checks = engines.parse_many([o.get("text", "") if o.get("ok") else "" for o in outputs], positions=False)
        still: List[FileJob] = []
        for job, out, (npm, native) in zip(active, outputs, checks):
            if not out.get("ok"):
                job.status, job.error = "error", f"cannot format frontmatter: {out.get('error', {}).get('message')}"
                continue
            text = out["text"]
            npm_value = {} if npm.ok and npm.value is None else npm.value
            native_value = {} if native.ok and native.value is None else native.value
            npm_good = npm.ok and same_value(npm_value, job.intended)
            native_good = native.ok and same_value(native_value, job.intended)
            if npm_good and native_good:
                job.yaml_out = text
                continue
            if not npm_good or attempt == 2:
                job.status = "refused"
                reading = native_value if native.ok else None
                differ = [key for key in job.intended
                          if not (isinstance(reading, dict) and same_value(reading.get(key, object()),
                                                                          job.intended[key]))]
                job.error = ("verification failed: after rewriting, Claude Code's builds would still read "
                             + (f"{', '.join(repr(k) for k in differ[:5])} differently" if differ and reading
                                is not None else "the frontmatter differently")
                             + "; the file was left untouched (restructure those fields by hand)")
                continue
            # Quote the fields native builds misread (all of them if Bun failed outright).
            if native.ok and isinstance(native_value, dict) and attempt == 0:
                bad = [key for key in job.intended if not same_value(native_value.get(key, object()),
                                                                    job.intended[key])]
            else:
                bad = list(job.intended)
            job.request["quoteAll"] = [[key] for key in bad] or [[key] for key in job.intended]
            still.append(job)
        active = still


def check_idempotent(jobs: List[FileJob], engines: Engines) -> None:
    done = [j for j in jobs if j.status == "pending" and j.yaml_out is not None]
    if not done:
        return
    again = engines.format_many([dict(j.request, text=j.yaml_out, edits=[]) for j in done])
    for job, out in zip(done, again):
        if not out.get("ok") or out["text"] != job.yaml_out:
            job.status = "refused"
            job.error = "verification failed: formatting the output again changed it; the file was left untouched"


def assemble(job: FileJob) -> None:
    body = format_body(job.body)
    if not same_prose(job.body, body):
        job.status, job.error = "refused", "verification failed: body text changed; the file was left untouched"
        return
    if job.yaml_text is None:
        new_text = body
    else:
        yaml_out = job.yaml_out if job.yaml_out is not None else job.yaml_text
        new_text = "---\n" + yaml_out + "---\n" + ("\n" + body if body else "")
    split_new = split_frontmatter(new_text)
    before = {f.code for f in split_frontmatter(job.original).findings}
    introduced = {f.code for f in split_new.findings} - before
    if introduced:
        job.status, job.error = "refused", f"verification failed: output would add {sorted(introduced)}"
        return
    job.new_text = new_text
    job.status = "unchanged" if new_text == job.original else "changed"


# ===========================================================================
# WRITING
# ===========================================================================

def write_atomic(path: Path, text: str) -> None:
    """Replace path with text via a temp file in the same directory."""
    data = text.encode("utf-8")
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(paths: List[Path], kind: str, engines: Engines) -> List[FileJob]:
    targets, _plugins, problems = discover(list(paths), Options(kind=kind))
    jobs: List[FileJob] = []
    for problem in problems:
        jobs.append(FileJob(path=Path(problem.message.split(" ")[0]), kind="?", status="error",
                            error=problem.message))
    for target in targets:
        job = FileJob(path=target.path, kind=target.kind)
        jobs.append(job)
        if target.path.is_symlink():
            job.status, job.error = "skipped", "symlink (not followed, so the target is never rewritten)"
            continue
        try:
            raw = target.path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                job.status, job.error = "error", "file is larger than 1 MiB"
                continue
            job.original = raw.decode("utf-8")
        except UnicodeDecodeError:
            job.status, job.error = "error", "not valid UTF-8"
            continue
        except OSError as exc:
            job.status, job.error = "error", f"cannot read: {exc}"
            continue
        prepare(job, engines)
    resolve_intent(jobs, engines)
    emit_frontmatter(jobs, engines)
    check_idempotent(jobs, engines)
    for job in jobs:
        if job.status == "pending":
            assemble(job)
    return jobs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite Claude Code skills, commands and subagents into one canonical layout "
                    "without changing what Claude Code loads",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s .claude/skills/deploy
  %(prog)s .claude/ --check
  %(prog)s SKILL.md --diff
        """)
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="write nothing; exit 10 if a file would change")
    mode.add_argument("--diff", action="store_true", help="print a unified diff; write nothing")
    parser.add_argument("--kind", choices=("auto", "skill", "command", "agent"), default="auto",
                        help="file kind when it cannot be inferred from the path")
    parser.add_argument("--json", action="store_true", help="print the result as JSON on stdout")
    parser.add_argument("--quiet", "-q", action="store_true", help="print only changes and problems")
    args = parser.parse_args(argv)

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"error: path not found: {path}", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        engines = Engines.locate()
        jobs = run(list(args.paths), args.kind, engines)
    except EngineUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return 130

    write = not (args.check or args.diff)
    written: List[str] = []
    for job in jobs:
        if job.status == "changed" and write:
            try:
                write_atomic(job.path, job.new_text)
                written.append(display(job.path))
            except OSError as exc:
                job.status, job.error = "error", f"cannot write: {exc}"
    counts: Dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    changed = counts.get("changed", 0)
    result = Result(
        success=not (counts.get("error") or counts.get("refused")) and (write or not changed),
        message=(f"{len(jobs)} file(s): {changed} {'reformatted' if write else 'would change'}, "
                 f"{counts.get('unchanged', 0)} unchanged, {counts.get('error', 0)} failed, "
                 f"{counts.get('refused', 0)} refused, {counts.get('skipped', 0)} skipped"),
        data={"files": [{"path": display(j.path), "kind": j.kind, "status": j.status,
                         "notes": j.notes, "error": j.error} for j in jobs],
              "parsers": engines.versions(), "mode": "write" if write else ("check" if args.check else "diff")},
        errors=[f"{display(j.path)}: {j.error}" for j in jobs if j.status in ("error", "refused")],
        warnings=[f"{display(j.path)}: {j.error}" for j in jobs if j.status == "skipped"])

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for job in jobs:
            name = display(job.path)
            if job.status == "changed":
                verb = "reformatted" if write else "would reformat"
                print(f"{verb} {name}" + (f" ({'; '.join(job.notes)})" if job.notes else ""))
                if args.diff:
                    sys.stdout.writelines(difflib.unified_diff(
                        job.original.splitlines(True), job.new_text.splitlines(True),
                        fromfile=f"a/{name}", tofile=f"b/{name}"))
            elif job.status == "unchanged" and not args.quiet:
                print(f"unchanged {name}")
            elif job.status in ("error", "refused", "skipped"):
                print(f"{job.status} {name}: {job.error}", file=sys.stderr)
        only_changes = not result.errors and changed and not write
        print(("OK: " if result.success else "WOULD CHANGE: " if only_changes else "FAILED: ") + result.message)

    if counts.get("refused"):
        return EXIT_VERIFY
    if counts.get("error"):
        return EXIT_FAILURE
    if changed and not write:
        return EXIT_CHANGES
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
