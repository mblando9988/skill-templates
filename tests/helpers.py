"""Shared test helpers: import paths, CLI runner, parser availability."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from claude_frontmatter import EngineUnavailable, Engines  # noqa: E402

try:
    ENGINES = Engines.locate()
    ENGINE_PROBLEM = None
except EngineUnavailable as exc:  # pragma: no cover - depends on the machine
    ENGINES = None
    ENGINE_PROBLEM = str(exc)

needs_parsers = unittest.skipIf(ENGINES is None, f"YAML parsers unavailable: {ENGINE_PROBLEM}")


def run(script, *args, cwd=None):
    """Run a script from scripts/ and return (exit code, stdout, stderr)."""
    proc = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def run_json(script, *args, cwd=None):
    code, out, err = run(script, *args, "--json", cwd=cwd)
    try:
        return code, json.loads(out)
    except ValueError:
        raise AssertionError(f"{script} printed no JSON (exit {code}):\n{out}\n{err}")


def codes(result):
    """All finding codes in a validate_skill.py --json result."""
    found = [f["code"] for f in result["data"].get("other", [])]
    for entry in result["data"]["files"]:
        found.extend(f["code"] for f in entry["findings"])
    return found


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text, mode=None):
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        if mode is not None:
            os.chmod(path, mode)
        return path
