---
name: new-skill
description: "Use when the user wants to create, scaffold, or fix a Claude Code skill, slash command, subagent, or plugin, or to turn a repeated workflow into one."
when_to_use: "Examples: 'make a skill for our deploy process', 'turn this prompt into a slash command', 'create a subagent that reviews migrations', 'package these skills as a plugin', 'why doesn't my skill trigger?'"
argument-hint: "[what the skill, agent or plugin should do]"
allowed-tools: Read Grep Glob Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/format_skill.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_skill.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_docs_safety.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/export_skill.py *)
---

# New skill

Create the component the user asked for ($ARGUMENTS) from this plugin's templates. Then prove it loads correctly in both Claude Code builds: native builds parse YAML with Bun, npm builds with eemeli/yaml.

## 1. Pick the component

| The user needs | Build | Template |
|---|---|---|
| instructions or a workflow Claude follows in the main conversation | skill | `${CLAUDE_PLUGIN_ROOT}/templates/skill-md-template.md` |
| a specialist that works in its own context and returns a result | subagent | `${CLAUDE_PLUGIN_ROOT}/templates/agent-md-template.md` |
| a rule enforced while a task runs | subagent with `hooks`, plus a skill that forks into it | `${CLAUDE_PLUGIN_ROOT}/templates/agent-md-template.md`, `${CLAUDE_PLUGIN_ROOT}/templates/hook-script-template.py` |
| several skills, agents, MCP servers or hooks shipped together | plugin | `${CLAUDE_PLUGIN_ROOT}/templates/plugin/` |

Worked references: `${CLAUDE_PLUGIN_ROOT}/examples/` covers agent-scoped hooks (skills/guarded-shell forking into agents/guarded-operator.md), scoped tools (release-notes), model and fork (deep-review), a subagent that preloads skills (agents/release-manager.md), and a plugin with a bundled MCP server (plugins/docs-toolkit).

## 2. Capture the failure first

Ask for, or write down, 2-3 real requests where Claude currently gets it wrong, and what went wrong. The description's trigger words and the body's guidance come from these. If nothing fails without a skill, say so and stop.

## 3. Fill every field that applies

Copy the template to `.claude/skills/<name>/SKILL.md` (project) or `~/.claude/skills/<name>/SKILL.md` (personal). Follow its field table, and fill the gaps the user did not specify:

- `description`: "Use when ..." with the situations and phrasings from step 2. Third person, one line, at most 1,024 characters.
- `when_to_use`: always fill it with example requests and synonyms. Claude Code uses it natively, and export folds it into `description` for surfaces that lack it.
- `argument-hint` and `arguments` when the skill takes input. Keep the hint quoted.
- `allowed-tools`: only the scoped rules the steps need, e.g. `Bash(git status *)`. Never bare `Bash`.
- `model` / `effort` only when the task needs them, and only as an alias (`opus`, `sonnet`, `haiku`, `fable`, `inherit`). Never a dated ID.
- `context: fork` + `agent` for self-contained tasks; `disable-model-invocation: true` for side effects.
- Enforcement belongs on agents. Skill-level `hooks` stay on the main thread for the rest of the session once the skill runs. Put task-scoped hooks in a subagent's frontmatter, where they start and stop with the agent, and point the skill at it with `context: fork` + `agent: <subagent>`. Keep skill hooks only for session-wide policy or `once: true`. The hook script reads JSON on stdin and exits 2 to block (exit 1 does not block). Use a `${CLAUDE_PROJECT_DIR}`-anchored path, because `${CLAUDE_SKILL_DIR}` is not substituted in hook commands.
- Subagents use camelCase fields (`tools`, `disallowedTools`, `maxTurns`) and preload knowledge with `skills:`.
- Plugins: MCP tool names are `mcp__plugin_<plugin>_<server>__<tool>`. Plugin agents ignore `hooks`, `mcpServers` and `permissionMode`.

Delete unused optional fields and the template notes. Replace every `{{placeholder}}`.

## 4. Prove it loads

Run all three and fix every error before you report back:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/format_skill.py <path>
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_skill.py <path> --strict
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_docs_safety.py <path>
```

Exit code 4 means a parser is missing. Ask the user to install Bun and Node.js, then run `npm ci --prefix ${CLAUDE_PLUGIN_ROOT}/scripts`.

For claude.ai or the Skills API, also run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/export_skill.py <skill-dir> --out dist/ --zip`. It reports every field those surfaces cannot use.

## 5. Report

Tell the user: where the file is, how it gets invoked (`/<name>`, or automatically on which requests), what each hook enforces, and the validator's final line.
