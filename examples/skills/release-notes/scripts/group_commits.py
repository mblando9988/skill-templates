#!/usr/bin/env python3
"""
group_commits.py - Group the commits in a git range by conventional-commit type.

Part of the release-notes skill (built from templates/script-template.py).

Usage:
    python3 group_commits.py <from-ref> [<to-ref>]
    python3 group_commits.py v1.2.0 HEAD

Output (stdout, JSON):
    {"range": "v1.2.0..HEAD", "count": 12,
     "groups": {"feat": [{"sha": "...", "subject": "...", "breaking": false}], ...}}

Exit Codes:
    0 - Success
    1 - git failed
    2 - Invalid arguments (including refs that look like options)
    3 - A ref does not exist
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Dict, List

EXIT_OK, EXIT_FAILURE, EXIT_ARGS, EXIT_NOT_FOUND = 0, 1, 2, 3
SAFE_REF = re.compile(r"^[A-Za-z0-9._/@{}~^+-]{1,200}$")
TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:\s*(?P<subject>.+)$")
KNOWN = ("feat", "fix", "perf", "docs", "refactor", "test", "build", "ci", "chore", "revert")


def git(*args: str) -> subprocess.CompletedProcess:
    # Argument list, never a shell string: refs cannot inject commands.
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Group commits in a git range by conventional-commit type")
    parser.add_argument("from_ref")
    parser.add_argument("to_ref", nargs="?", default="HEAD")
    args = parser.parse_args()
    for ref in (args.from_ref, args.to_ref):
        if not SAFE_REF.match(ref) or ref.startswith("-"):
            print(json.dumps({"error": f"unsafe ref: {ref!r}"}))
            return EXIT_ARGS
        if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
            print(json.dumps({"error": f"ref not found: {ref}"}))
            return EXIT_NOT_FOUND
    log = git("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{args.from_ref}..{args.to_ref}")
    if log.returncode != 0:
        print(json.dumps({"error": log.stderr.strip()[:500]}))
        return EXIT_FAILURE
    groups: Dict[str, List[dict]] = {}
    count = 0
    for record in filter(None, (r.strip("\n") for r in log.stdout.split("\x1e"))):
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        match = TYPE_RE.match(subject)
        kind = match.group("type") if match and match.group("type") in KNOWN else "other"
        breaking = bool(match and match.group("bang")) or "BREAKING CHANGE" in body
        groups.setdefault(kind, []).append({
            "sha": sha[:12],
            "subject": match.group("subject") if match else subject,
            "breaking": breaking,
        })
        count += 1
    print(json.dumps({"range": f"{args.from_ref}..{args.to_ref}", "count": count, "groups": groups}, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
