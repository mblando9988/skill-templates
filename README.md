# skill-templates

Templates, worked examples and hardened scripts for Claude Code skills, subagents and plugins that load the same way in every Claude Code build.

Claude Code parses frontmatter with two different YAML parsers: Bun.YAML in native builds and eemeli/yaml in npm builds. Both fail silently: a skill with a bad field, a misspelled key, or a value one parser misreads just loads without it. For example, `description: Use when you need to wait...` followed by any other key loses every field in native builds. The scripts here run both real parsers and tell you exactly what each build loads.

## Contents

| Path | What it is |
|---|---|
| `templates/skill-md-template.md` | SKILL.md starter with a field-by-field fill guide, `when_to_use` included |
| `templates/agent-md-template.md` | subagent starter: tools, skills preload, MCP servers, hooks, model |
| `templates/plugin/` | plugin skeleton: manifest, `.mcp.json`, skill, agent, `hooks/hooks.json`, guard script |
| `templates/hook-script-template.py` | hook script that fails closed and blocks with exit 2 |
| `templates/script-template.py` | Python helper-script starter: atomic writes, exit codes, `--json` |
| `templates/github-workflow-skill-ci.yml` | drop-in CI: format check, validation with both parsers, hook safety |
| `examples/skills/guarded-shell/` + `examples/agents/guarded-operator.md` | **hooks** scoped to a task: the skill forks into a subagent whose PreToolUse guard blocks destructive commands, and the guard stops when the agent finishes |
| `examples/skills/release-notes/` | skill with scoped **tools**: `allowed-tools` for exactly one script |
| `examples/skills/deep-review/` | skill with a **model**: alias, effort, `context: fork` + `agent` |
| `examples/agents/release-manager.md` | subagent that preloads a skill, scopes tools and MCP, and reuses a hook |
| `examples/plugins/docs-toolkit/` | plugin with a pinned MCP server, a skill, an agent, and a read-only hook |
| `skills/new-skill/` | the creation method as a skill: this repo installs as a plugin |
| `references/claude-code-frontmatter.md` | every field, hook rule and parser difference |
| `references/condensed.md` | design, review and testing guide |
| `scripts/` | the tools below |

## Scripts

Requirements: Python 3.9+, [Bun](https://bun.sh) 1.3+, Node.js 18+, and the pinned YAML parser (`npm ci --prefix scripts`). The scripts exit with code 4 and tell you what is missing.

```bash
python3 scripts/format_skill.py .claude/            # canonical layout; --check for CI, --diff to preview
python3 scripts/validate_skill.py .claude/ --strict # skills, commands, agents, plugins
python3 scripts/check_docs_safety.py .claude/ docs/ # unsafe hook commands, in files and in examples
python3 scripts/export_skill.py .claude/skills/x --out dist/ --zip   # for claude.ai / Skills API
```

- **format_skill.py** rewrites frontmatter with eemeli/yaml's Document API, so comments are kept. It puts keys in canonical order, quotes values one parser would misread, writes `true`/`false` for boolean fields, and strips BOMs and CRLF. Before writing, both parsers must read the result exactly as intended and a second pass must change nothing; otherwise the file is left alone (exit 11). Writes are atomic.
- **validate_skill.py** reports parse failures and parser disagreements with line numbers. It also flags misspelled or wrong-kind fields that Claude Code silently ignores, bare `Bash` grants, dated model IDs, and hook mistakes: bad events, a `matcher` written as a permission rule, `if` on events where it never runs, and a quoted `timeout`. It checks hook scripts that are missing, not executable, or can never block; broken links; plugin MCP tool names; and fields plugin agents ignore. `--target portable` enforces the Agent Skills subset.
- **check_docs_safety.py** catches `$TOOL_INPUT` (it does not exist), payload spliced into shell commands, `eval` and pipes into a shell, cwd-relative script paths, and gate scripts that exit 1 (which does not block). It checks frontmatter, `hooks.json`, settings files, and code examples in markdown.
- **export_skill.py** fills the gap for surfaces that accept only the six Agent Skills fields: it folds `when_to_use` into `description` and reports what every dropped field means there.

Exit codes follow `templates/script-template.py`: 0 ok, 1 failure, 2 bad arguments, 3 not found, 4 missing tool, 10 check failed, 11 verification failed.

## Use

1. Copy a template (or an example) into `.claude/skills/<name>/`, `.claude/agents/`, or a plugin folder, and fill in every `{{placeholder}}`.
2. Run `format_skill.py`, then `validate_skill.py --strict`, then `check_docs_safety.py`.
3. Add `templates/github-workflow-skill-ci.yml` to your repository as `.github/workflows/skill-ci.yml`.

Or install this repository as a plugin (`claude --plugin-dir /path/to/skill-templates`) and ask Claude to "make a skill for ...". The `new-skill` skill walks through the same method.
