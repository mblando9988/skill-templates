#!/usr/bin/env python3
"""
{{SCRIPT_NAME}}.py - {{BRIEF_DESCRIPTION}}

Part of the {{SKILL_NAME}} skill.

Responsibilities:
- {{RESPONSIBILITY_1}}
- {{RESPONSIBILITY_2}}

Usage:
    python3 {{SCRIPT_NAME}}.py <input> [--output FILE] [--json] [--verbose] [--no-verify]
    python3 {{SCRIPT_NAME}}.py --help

Call it from SKILL.md as:
    python3 ${CLAUDE_SKILL_DIR}/scripts/{{SCRIPT_NAME}}.py <input>
and pre-approve exactly that with:
    allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/{{SCRIPT_NAME}}.py *)

Examples:
    python3 {{SCRIPT_NAME}}.py input.json
    python3 {{SCRIPT_NAME}}.py input.json --json --output result.json

Output:
    --json: one JSON object on stdout (see Result.to_dict); logs go to stderr.
    Otherwise: human-readable text on stdout, problems on stderr.

Exit Codes:
    0  - Success
    1  - General failure (unexpected error)
    2  - Invalid arguments
    3  - Input file not found
    10 - Validation failure (the input is wrong)
    11 - Verification failure (the output did not pass its self-check)
"""

import argparse
import json
import os
import re
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_INPUT_BYTES = 10 * 1024 * 1024


class Exit(IntEnum):
    OK = 0
    FAILURE = 1
    ARGS = 2
    NOT_FOUND = 3
    VALIDATION = 10
    VERIFICATION = 11


# ===========================================================================
# RESULT TYPE
# ===========================================================================

@dataclass
class Result:
    """Outcome of one run. exit_code decides the process exit status."""
    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    exit_code: int = Exit.OK

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "errors": self.errors,
            "warnings": self.warnings,
            "exit_code": int(self.exit_code),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def fail(message: str, code: int, *errors: str) -> Result:
    return Result(success=False, message=message, errors=list(errors) or [message], exit_code=code)


# ===========================================================================
# FILE HELPERS (atomic, UTF-8, bounded)
# ===========================================================================

def read_text(path: Path) -> str:
    """Read a UTF-8 file, refusing anything larger than MAX_INPUT_BYTES."""
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} is larger than {MAX_INPUT_BYTES} bytes")
    return data.decode("utf-8")


def write_atomic(path: Path, text: str) -> None:
    """Write text so readers see the old file or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ===========================================================================
# STATE (delete this section if the script keeps no state)
# ===========================================================================

def state_path(project: str = "default") -> Path:
    """~/.cache/{{SKILL_NAME}}/<project>.json, with the name reduced to safe characters."""
    safe = re.sub(r"[^a-z0-9._-]+", "-", project.lower()).strip(".-") or "default"
    return Path.home() / ".cache" / "{{SKILL_NAME}}" / f"{safe}.json"


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load state; a corrupt file is moved aside and a fresh state returned."""
    path = path or state_path()
    fresh = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "data": {}}
    if not path.exists():
        return fresh
    try:
        state = json.loads(read_text(path))
        if not isinstance(state, dict):
            raise ValueError("state is not a JSON object")
        return state
    except (ValueError, UnicodeDecodeError):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, backup)
            fresh["recovered_from"] = str(backup)
        except OSError:
            pass
        return fresh


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_atomic(path or state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


# ===========================================================================
# CORE LOGIC
# ===========================================================================

def process(input_path: Path, options: Dict[str, Any]) -> Result:
    """Main work. Return a Result; never call sys.exit here."""
    if not input_path.is_file():
        return fail(f"input file not found: {input_path}", Exit.NOT_FOUND)
    try:
        text = read_text(input_path)
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        return fail(f"cannot read {input_path}: {exc}", Exit.VALIDATION)

    # ----- {{Implement the core logic here}} -----
    # Validate the input first and return fail(..., Exit.VALIDATION) on bad data.
    data = {"characters": len(text)}

    return Result(success=True, message="processing complete", data=data)


def verify_result(result: Result) -> Tuple[bool, str]:
    """Self-check the output before anyone relies on it."""
    if not result.success:
        return False, result.message
    # ----- {{Check invariants of the output}} -----
    if "characters" not in result.data:
        return False, "missing 'characters' in output"
    return True, "verification passed"


# ===========================================================================
# CLI
# ===========================================================================

def emit(result: Result, as_json: bool, verbose: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return
    stream = sys.stdout if result.success else sys.stderr
    print(("OK: " if result.success else "FAILED: ") + result.message, file=stream)
    if verbose:
        for key, value in result.data.items():
            print(f"  {key}: {value}")
    for error in result.errors:
        print(f"  error: {error}", file=sys.stderr)
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="{{BRIEF_DESCRIPTION}}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok, 1 failure, 2 bad arguments, 3 not found, 10 invalid input, 11 verification failed")
    parser.add_argument("input", type=Path, help="path to the input file")
    parser.add_argument("--output", "-o", type=Path, help="also write the result data to this file (atomic)")
    parser.add_argument("--json", action="store_true", help="print one JSON object on stdout")
    parser.add_argument("--verbose", "-v", action="store_true", help="print details (and tracebacks)")
    parser.add_argument("--no-verify", action="store_true", help="skip the self-check")
    args = parser.parse_args(argv)          # exits 2 on bad arguments

    try:
        result = process(args.input, {"verbose": args.verbose})
        if result.success and not args.no_verify:
            ok, note = verify_result(result)
            if not ok:
                result = fail(f"verification failed: {note}", Exit.VERIFICATION)
        if result.success and args.output:
            write_atomic(args.output, json.dumps(result.data, indent=2, default=str) + "\n")
            result.data["output"] = str(args.output)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # last resort: report instead of a bare traceback
        if args.verbose:
            traceback.print_exc()
        result = fail(f"unexpected error: {type(exc).__name__}: {exc}", Exit.FAILURE)

    emit(result, args.json, args.verbose)
    return int(result.exit_code if not result.success else Exit.OK)


if __name__ == "__main__":
    sys.exit(main())
