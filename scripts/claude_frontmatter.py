#!/usr/bin/env python3
"""
claude_frontmatter.py - Split Claude Code markdown files the way Claude Code
does, and parse their YAML frontmatter with Claude Code's real parsers.

Part of the skill-templates tooling: validate_skill.py, format_skill.py,
check_docs_safety.py and export_skill.py import it. Library module, no CLI.

Claude Code ships two YAML parsers, and a skill must load in both:

    npm builds     eemeli/yaml   run here by: node scripts/yaml_bridge.mjs
    native builds  Bun.YAML      run here by: bun --no-install scripts/yaml_bridge.mjs

The Python side never parses YAML itself. It hands frontmatter text to
yaml_bridge.mjs as JSON on stdin (no shell, nothing interpolated) and gets
data, error positions and formatted text back. When the two parsers
disagree - `description: wait...` loses its dots in native builds, and any
key after it lands in a second YAML document there - the scripts report it
instead of guessing which reading Claude Code will use.

Requirements (checked by Engines.locate; scripts exit 4 when missing):
    bun   >= 1.3   https://bun.sh
    node  >= 18    plus the pinned yaml package:  npm ci --prefix <scripts dir>
Override the executables with SKILL_TOOLS_BUN / SKILL_TOOLS_NODE.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Finding", "Split", "split_frontmatter", "claude_code_extract",
    "claude_code_fallback_quote", "EngineUnavailable", "EngineError", "Engines",
    "ParseResult", "Frontmatter", "load_frontmatters", "claude_bool",
    "split_tool_rules", "same_value", "iter_files", "EXIT_DEPENDENCY",
]

SCRIPTS_DIR = Path(__file__).resolve().parent
BRIDGE = SCRIPTS_DIR / "yaml_bridge.mjs"
EXIT_DEPENDENCY = 4


# ===========================================================================
# FINDINGS (shared issue record for every script in this toolkit)
# ===========================================================================

@dataclass
class Finding:
    """One problem found in a file.

    severity: "error" | "warning" | "info"
    code:     stable dotted identifier, e.g. "frontmatter.bom"
    line/col: 1-based position in the file, when known
    hint:     how to fix it
    """
    severity: str
    code: str
    message: str
    line: Optional[int] = None
    col: Optional[int] = None
    hint: Optional[str] = None
    path: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"severity": self.severity, "code": self.code, "message": self.message}
        for key in ("path", "line", "col", "hint"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    def format(self) -> str:
        where = self.path or ""
        if self.line is not None:
            where += f":{self.line}" + (f":{self.col}" if self.col is not None else "")
        prefix = f"{where}: " if where else ""
        text = f"{prefix}{self.severity}: {self.message} [{self.code}]"
        if self.hint:
            text += f"\n    fix: {self.hint}"
        return text


# ===========================================================================
# FRONTMATTER SPLITTING (Claude Code's rules, not YAML's)
# ===========================================================================

# The regex Claude Code uses to find frontmatter (cli.js, v2.1.x):
#   /^---\s*\n([\s\S]*?)---\s*\n?/
# Its closing match is lazy and unanchored: the frontmatter ends at the first
# "---" anywhere, even mid-line or inside a quoted value.
_CC_FRONTMATTER_RE = re.compile(r"\A---\s*\n([\s\S]*?)---\s*\n?")
_DELIM_RE = re.compile(r"---[ \t]*\Z")


@dataclass
class Split:
    """A markdown file divided into frontmatter and body.

    text is the file with any BOM removed and line endings normalized to LF.
    yaml_text is the text between the delimiter lines as a YAML parser should
    see it (trailing newline kept). Line numbers are 1-based file positions.
    """
    text: str
    has_frontmatter: bool
    yaml_text: Optional[str]
    yaml_line: int
    body: str
    body_line: int
    newline: str = "\n"
    had_bom: bool = False
    findings: List[Finding] = field(default_factory=list)


def claude_code_extract(text: str) -> Optional[str]:
    """Return the frontmatter text Claude Code itself would extract, or None."""
    match = _CC_FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def split_frontmatter(text: str) -> Split:
    """Split a file the way Claude Code does, recording every hazard.

    Claude Code reads frontmatter only when the file's first line is "---".
    A BOM, a leading blank line, or bare-CR line endings therefore silently
    turn the whole file into body text, and the file loads with no fields.
    """
    findings: List[Finding] = []
    had_bom = text.startswith("﻿")
    if had_bom:
        text = text[1:]
        findings.append(Finding(
            "error", "frontmatter.bom",
            "file starts with a UTF-8 byte order mark; Claude Code does not strip it, misses the "
            "opening '---', and loads the file with no frontmatter",
            line=1, col=1, hint="save as UTF-8 without BOM (format_skill.py does this)"))

    newline = "\n"
    if "\r\n" in text:
        newline = "\r\n"
        findings.append(Finding(
            "warning", "file.crlf",
            "file uses CRLF line endings; Claude Code copes, but other skill tooling and diffs do not",
            hint="convert to LF (format_skill.py does this)"))
    if re.search(r"\r(?!\n)", text):
        findings.append(Finding(
            "error", "file.cr",
            "file contains bare CR line breaks; Claude Code only finds frontmatter on LF lines",
            hint="convert to LF line endings (format_skill.py does this)"))
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")

    def no_frontmatter() -> Split:
        return Split(norm, False, None, 0, norm, 1, newline, had_bom, findings)

    if not _DELIM_RE.match(lines[0]):
        first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
        if first is not None and first > 0 and _DELIM_RE.match(lines[first]):
            findings.append(Finding(
                "error", "frontmatter.not-first-line",
                f"the opening '---' is on line {first + 1}; Claude Code reads frontmatter only when "
                "'---' is the first line, so this file loads with no fields",
                line=first + 1, col=1, hint="delete everything above the opening '---'"))
        elif lines[0].startswith("---"):
            findings.append(Finding(
                "error", "frontmatter.opening-line",
                "the first line starts with '---' but has other text after it; Claude Code does not "
                "treat it as a frontmatter delimiter",
                line=1, col=1, hint="put '---' alone on the first line"))
        return no_frontmatter()

    close = next((i for i in range(1, len(lines)) if _DELIM_RE.match(lines[i])), None)
    if close is None:
        findings.append(Finding(
            "error", "frontmatter.unterminated", "frontmatter has no closing '---' line",
            line=1, col=1, hint="add a line containing only '---' after the last field"))
        return no_frontmatter()

    yaml_lines = lines[1:close]
    yaml_text = "\n".join(yaml_lines) + ("\n" if yaml_lines else "")
    body = "\n".join(lines[close + 1:])

    for offset, ln in enumerate(yaml_lines):
        pos = ln.find("---")
        if pos != -1:
            findings.append(Finding(
                "error", "frontmatter.dashes-inside",
                "'---' inside the frontmatter: Claude Code ends the frontmatter at the first '---' "
                "it finds, even mid-line or inside quotes, so the fields after it are lost",
                line=offset + 2, col=pos + 1,
                hint="replace '---' with an em dash or reword; keep '---' only on the delimiter lines"))
            break

    return Split(norm, True, yaml_text, 2, body, close + 2, newline, had_bom, findings)


_CC_FALLBACK_LINE_RE = re.compile(r"\A([a-zA-Z_-]+):\s+([^\n\r  ]+)\Z")
_CC_FALLBACK_SPECIAL_RE = re.compile(r"[{}\[\]*&#!|>%@`]")


def claude_code_fallback_quote(yaml_text: str) -> str:
    """Rewrite YAML the way Claude Code v2.1.x retries after a parse failure.

    That retry wraps, in double quotes, every unquoted top-level `key: value`
    whose value contains one of {}[]*&#!|>%@` - so `argument-hint: [pr] [n]`
    loads there but fails every other YAML tool. Current docs describe no
    retry ("the skill still loads with no fields set"), so nothing may rely
    on it; format_skill.py uses it only to recover what the author meant.
    """
    out = []
    for line in yaml_text.split("\n"):
        match = _CC_FALLBACK_LINE_RE.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            quoted = (value.startswith('"') and value.endswith('"')) or \
                     (value.startswith("'") and value.endswith("'"))
            if not quoted and _CC_FALLBACK_SPECIAL_RE.search(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'{key}: "{escaped}"')
                continue
        out.append(line)
    return "\n".join(out)


# ===========================================================================
# PARSER ENGINES (the bridge to Bun.YAML and eemeli/yaml)
# ===========================================================================

class EngineUnavailable(RuntimeError):
    """bun, node, or the pinned yaml package is missing."""


class EngineError(RuntimeError):
    """The bridge ran but failed (crash, timeout, malformed reply)."""


@dataclass
class ParseResult:
    """One parser's reading of one frontmatter block (lines relative to it)."""
    ok: bool
    value: Any = None
    message: Optional[str] = None
    code: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None
    warnings: List[str] = field(default_factory=list)
    positions: Dict[Tuple[Any, ...], Tuple[int, int]] = field(default_factory=dict)


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        tag = value.get("$yaml")
        if isinstance(tag, str) and len(value) <= 2:
            if tag == "nan":
                return math.nan
            if tag == "inf":
                return math.inf
            if tag == "-inf":
                return -math.inf
            if tag in ("bigint", "date"):
                return value.get("value")
        return {key: _decode(item) for key, item in value.items()}
    return value


def _encode(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"$yaml": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    return value


class Engines:
    """Runs yaml_bridge.mjs under Bun (native builds) and Node (npm builds).

    Each call sends one JSON request covering many files, so a whole skill
    tree costs one process per runtime.
    """

    def __init__(self, bun: str, node: str, timeout: float = 120.0):
        self.bun = bun
        self.node = node
        self.timeout = timeout
        self._versions: Optional[Dict[str, str]] = None

    @classmethod
    def locate(cls, timeout: float = 120.0) -> "Engines":
        """Find bun, node and the pinned yaml package, or raise EngineUnavailable."""
        problems = []
        bun = os.environ.get("SKILL_TOOLS_BUN") or shutil.which("bun")
        if not bun:
            home_bun = Path.home() / ".bun" / "bin" / "bun"
            bun = str(home_bun) if home_bun.is_file() else None
        if not bun:
            problems.append("bun not found (install from https://bun.sh or set SKILL_TOOLS_BUN)")
        node = os.environ.get("SKILL_TOOLS_NODE") or shutil.which("node")
        if not node:
            problems.append("node not found (install Node.js 18+ or set SKILL_TOOLS_NODE)")
        pinned = cls._pinned_yaml_version()
        installed = SCRIPTS_DIR / "node_modules" / "yaml" / "package.json"
        if not installed.is_file():
            problems.append(f"the pinned yaml package is not installed; run: npm ci --prefix {SCRIPTS_DIR}")
        else:
            try:
                version = json.loads(installed.read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError):
                version = None
            if pinned and version != pinned:
                problems.append(f"yaml {version} is installed but package.json pins {pinned}; "
                                f"run: npm ci --prefix {SCRIPTS_DIR}")
        if not BRIDGE.is_file():
            problems.append(f"{BRIDGE} is missing")
        if problems:
            raise EngineUnavailable("; ".join(problems))
        return cls(bun, node, timeout)

    @staticmethod
    def _pinned_yaml_version() -> Optional[str]:
        try:
            manifest = json.loads((SCRIPTS_DIR / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return (manifest.get("dependencies") or {}).get("yaml")

    def call(self, runtime: str, request: dict) -> dict:
        """Send one request to the bridge under runtime ("bun" or "node")."""
        if runtime == "bun":
            argv = [self.bun, "--no-install", str(BRIDGE)]
        else:
            argv = [self.node, "--no-warnings", str(BRIDGE)]
        env = {key: value for key, value in os.environ.items()
               if key not in ("NODE_OPTIONS", "NODE_PATH")}
        try:
            proc = subprocess.run(
                argv, input=json.dumps(_encode(request)).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout, env=env, cwd=str(SCRIPTS_DIR), check=False)
        except FileNotFoundError as exc:
            raise EngineUnavailable(f"cannot run {argv[0]}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"{runtime} YAML bridge timed out after {self.timeout:.0f}s") from exc
        try:
            reply = json.loads(proc.stdout.decode("utf-8"))
        except ValueError as exc:
            detail = proc.stderr.decode("utf-8", "replace").strip()[:500]
            raise EngineError(f"{runtime} YAML bridge returned no JSON (exit {proc.returncode}): "
                              f"{detail}") from exc
        if not reply.get("ok"):
            raise EngineError(f"{runtime} YAML bridge failed: {str(reply.get('error'))[:500]}")
        return reply

    def versions(self) -> Dict[str, str]:
        """Parser versions, for reports: {"native": ..., "npm": ...}."""
        if self._versions is None:
            bun = self.call("bun", {"op": "version"})
            node = self.call("node", {"op": "version"})
            self._versions = {
                "native": f"{bun.get('parser')} (bun {bun.get('version')})",
                "npm": f"{node.get('parser')} (node {node.get('version')})",
            }
        return self._versions

    def parse_many(self, texts: Sequence[str], positions: bool = True
                   ) -> List[Tuple[ParseResult, ParseResult]]:
        """Parse each text with eemeli/yaml (npm) and Bun.YAML (native)."""
        if not texts:
            return []
        items = [{"id": str(index), "text": text} for index, text in enumerate(texts)]
        npm = self.call("node", {"op": "parse", "items": items, "positions": positions})["results"]
        native = self.call("bun", {"op": "parse", "items": items})["results"]
        return [(self._result(a), self._result(b)) for a, b in zip(npm, native)]

    def parse_native(self, texts: Sequence[str]) -> List[ParseResult]:
        """Parse each text with Bun.YAML only."""
        if not texts:
            return []
        items = [{"id": str(index), "text": text} for index, text in enumerate(texts)]
        return [self._result(r) for r in self.call("bun", {"op": "parse", "items": items})["results"]]

    def format_many(self, items: Sequence[dict]) -> List[dict]:
        """Canonicalize YAML texts with eemeli/yaml's Document API (comments kept)."""
        if not items:
            return []
        payload = [dict(item, id=str(index)) for index, item in enumerate(items)]
        return self.call("node", {"op": "format", "items": payload})["results"]

    def stringify_many(self, items: Sequence[dict]) -> List[dict]:
        """Emit YAML for plain data (keys in dict order)."""
        if not items:
            return []
        payload = [dict(item, id=str(index)) for index, item in enumerate(items)]
        return self.call("node", {"op": "stringify", "items": payload})["results"]

    @staticmethod
    def _result(raw: dict) -> ParseResult:
        if raw.get("ok"):
            positions = {tuple(entry[0]): (entry[1], entry[2]) for entry in raw.get("positions", [])}
            return ParseResult(True, _decode(raw.get("value")), warnings=list(raw.get("warnings", [])),
                               positions=positions)
        error = raw.get("error") or {}
        return ParseResult(False, message=error.get("message"), code=error.get("code"),
                           line=error.get("line"), col=error.get("col"))


# ===========================================================================
# LOADING: split + both parsers + disagreement analysis
# ===========================================================================

@dataclass
class Frontmatter:
    """What each Claude Code build loads from one file's frontmatter.

    data holds the npm reading when it is a mapping (else the native one),
    so field checks still run; error findings flag every place the builds
    disagree. positions maps a key path to its (file line, col).
    """
    split: Split
    npm: Optional[ParseResult] = None
    native: Optional[ParseResult] = None
    data: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    positions: Dict[Tuple[Any, ...], Tuple[int, int]] = field(default_factory=dict)
    parsed: bool = False          # both builds read the same mapping

    def line_of(self, *path: Any) -> Optional[int]:
        """File line of the key or item at path, or of its nearest ancestor."""
        path = tuple(path)
        while path:
            if path in self.positions:
                return self.positions[path][0]
            path = path[:-1]
        return self.split.yaml_line if self.split.has_frontmatter else None


def same_value(a: Any, b: Any) -> bool:
    """Structural equality: NaN == NaN, 1 == 1.0, True != 1, dict key order ignored."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _short(value: Any, limit: int = 80) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _Missing()


def load_frontmatters(texts: Sequence[str], engines: Engines) -> List[Frontmatter]:
    """Split and parse many files with both parsers in a few bridge calls."""
    loaded = [Frontmatter(split=split_frontmatter(text)) for text in texts]
    for fm in loaded:
        fm.findings.extend(fm.split.findings)
    todo = [fm for fm in loaded if fm.split.has_frontmatter]
    results = engines.parse_many([fm.split.yaml_text or "" for fm in todo])
    needs_location: List[Frontmatter] = []
    for fm, (npm, native) in zip(todo, results):
        if _analyze(fm, npm, native):
            needs_location.append(fm)
    if needs_location:
        _locate_native_failures(needs_location, engines)
    return loaded


def _analyze(fm: Frontmatter, npm: ParseResult, native: ParseResult) -> bool:
    """Record findings; return True when the native failure still needs locating."""
    base = fm.split.yaml_line
    fm.npm, fm.native = npm, native
    fm.positions = {path: (base + line - 1, col) for path, (line, col) in npm.positions.items()}

    if not npm.ok:
        line = base + npm.line - 1 if npm.line else base
        text = fm.split.yaml_text or ""
        if npm.code == "DUPLICATE_KEY":
            source_line = text.split("\n")[npm.line - 1] if npm.line else ""
            match = re.match(r"\s*(?:-\s+)?[\"']?([^:\"'#]+?)[\"']?\s*:", source_line)
            key = match.group(1) if match else "?"
            fm.findings.append(Finding(
                "error", "yaml.duplicate-key",
                f"duplicate key '{key}': npm builds of Claude Code reject the whole frontmatter and "
                "load no fields, while native builds silently keep the last value",
                line=line, col=npm.col, hint=f"keep one '{key}' entry"))
            if native.ok and isinstance(native.value, dict):
                fm.data = native.value
            return False
        recoverable = claude_code_fallback_quote(text) != text
        native_mapping = native.ok and isinstance(native.value, dict)
        fm.findings.append(Finding(
            "error", "yaml.invalid",
            f"frontmatter YAML does not parse in npm builds of Claude Code: {npm.message}"
            + ("; native builds read it anyway, so the builds behave differently" if native_mapping
               else "; native builds reject it too")
            + ". Claude Code loads a file whose YAML fails with no fields at all",
            line=line, col=npm.col,
            hint=("quote the value; format_skill.py can repair this automatically" if recoverable
                  else "fix the YAML at this line (quote values that contain ': ' or ' #' or start "
                       "with a symbol)")))
        if native_mapping:
            fm.data = native.value
        return False

    if npm.value is None:
        fm.parsed = native.ok and native.value is None
        if not fm.parsed:
            fm.findings.append(Finding("error", "yaml.parsers-disagree",
                                       "npm builds read empty frontmatter but native builds do not",
                                       line=base))
        return False
    if not isinstance(npm.value, dict):
        fm.findings.append(Finding(
            "error", "frontmatter.not-mapping",
            f"frontmatter is a YAML {type(npm.value).__name__}, not 'key: value' fields; "
            "Claude Code ignores it", line=base, hint="write the frontmatter as key: value lines"))
        return False

    fm.data = npm.value
    for warning in npm.warnings:
        fm.findings.append(Finding("warning", "yaml.warning", f"YAML warning: {warning}", line=base))

    if not native.ok:
        fm.findings.append(Finding(
            "error", "yaml.native-rejects",
            f"native builds of Claude Code (Bun.YAML) cannot parse this frontmatter ({native.message}) "
            "and load the file with no fields",
            line=base, hint="quote the value in the failing field (format_skill.py does this)"))
        return True

    if isinstance(native.value, list) and len(native.value) > 1 and isinstance(native.value[0], dict):
        culprit = _first_divergent_key(npm.value, native.value[0])
        fm.findings.append(Finding(
            "error", "yaml.native-split",
            "native builds of Claude Code (Bun.YAML) read '...' or '---' in an unquoted value as a "
            f"YAML document marker and split this frontmatter into {len(native.value)} documents, "
            "so they load none of its fields"
            + (f"; the break is in '{culprit}'" if culprit else ""),
            line=fm.line_of(culprit) if culprit else base,
            hint="quote that value (format_skill.py does this)"))
        return False

    if not isinstance(native.value, dict):
        fm.findings.append(Finding(
            "error", "yaml.native-not-mapping",
            f"native builds of Claude Code read this frontmatter as a {type(native.value).__name__}, "
            "so they load none of its fields", line=base))
        return False

    agree = True
    for key in dict.fromkeys(list(npm.value) + list(native.value)):
        a, b = npm.value.get(key, _MISSING), native.value.get(key, _MISSING)
        if a is _MISSING or b is _MISSING or not same_value(a, b):
            agree = False
            fm.findings.append(Finding(
                "error", "yaml.parsers-disagree",
                f"Claude Code's builds read '{key}' differently: npm builds (eemeli/yaml) "
                f"{_describe(a)}, native builds (Bun.YAML) {_describe(b)}",
                line=fm.line_of(key),
                hint="quote the value so both parsers agree (format_skill.py does this)"))
    fm.parsed = agree
    return False


def _describe(value: Any) -> str:
    return "do not see it" if value is _MISSING else f"read {_short(value)}"


def _first_divergent_key(expected: Dict[str, Any], got: Dict[str, Any]) -> Optional[str]:
    for key, value in expected.items():
        if key not in got or not same_value(value, got[key]):
            return key
    return None


def _locate_native_failures(failures: List[Frontmatter], engines: Engines) -> None:
    """Parse growing prefixes (one per top-level key) with Bun to find the failing field."""
    jobs = []
    for fm in failures:
        lines = (fm.split.yaml_text or "").split("\n")
        starts = sorted({line for path, (line, _col) in fm.npm.positions.items() if len(path) == 1})
        prefixes = []
        for index, start in enumerate(starts):
            end = starts[index + 1] - 1 if index + 1 < len(starts) else len(lines)
            prefixes.append("\n".join(lines[:end]).rstrip("\n") + "\n")
        jobs.append((fm, starts, prefixes))
    results = engines.parse_native([p for _fm, _s, prefixes in jobs for p in prefixes])
    cursor = 0
    for fm, starts, prefixes in jobs:
        chunk = results[cursor: cursor + len(prefixes)]
        cursor += len(prefixes)
        for start, result in zip(starts, chunk):
            if not result.ok:
                line = fm.split.yaml_line + start - 1
                for finding in fm.findings:
                    if finding.code == "yaml.native-rejects":
                        finding.line = line
                        finding.message += (f"; parsing breaks at the field on line {line} "
                                            "or the one just above it")
                break


# ===========================================================================
# CLAUDE CODE VALUE SEMANTICS
# ===========================================================================

_TRUE_WORDS = {"true", "yes", "on", "1"}
_FALSE_WORDS = {"false", "no", "off", "0"}


def claude_bool(value: Any) -> Optional[bool]:
    """Interpret a boolean field the way Claude Code v2.1.218+ does.

    Returns None for values Claude Code does not recognize as a boolean.
    Before v2.1.218 only true and false (and the string "true") counted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


def split_tool_rules(value: Any) -> Optional[List[str]]:
    """Split an allowed-tools / tools value exactly like Claude Code.

    Accepts a string or a list of strings; each string is split on commas and
    spaces outside parentheses. Claude Code tracks a single inside-parens
    flag rather than nesting depth, and so does this. Returns None for other
    types (Claude Code then ignores the field).
    """
    if value is None:
        return []
    if isinstance(value, str):
        chunks = [value]
    elif isinstance(value, list):
        chunks = [item for item in value if isinstance(item, str)]
    else:
        return None
    rules: List[str] = []
    for chunk in chunks:
        current = ""
        inside = False
        for ch in chunk:
            if ch == "(":
                inside = True
                current += ch
            elif ch == ")":
                inside = False
                current += ch
            elif ch in ", " and not inside:
                if current.strip():
                    rules.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            rules.append(current.strip())
    return rules


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    """Yield regular files under paths, never following symlinked directories."""
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            for root, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
                for name in sorted(files):
                    yield Path(root) / name
