# Claude Code frontmatter reference

Everything Claude Code reads from skill, command, subagent and plugin files, and what happens when it is wrong. Verified September 2026 against code.claude.com/docs (skills, sub-agents, hooks, permissions, plugins-reference, mcp, model-config) and the Claude Code 2.1.42 loader. `scripts/claude_spec.py` holds the same data for the scripts.

## How Claude Code reads the file

- Frontmatter counts only when `---` is the very first line. A BOM or a blank line before it turns the whole file into body text.
- The frontmatter ends at the first `---`, even one in the middle of a line or inside quotes. Never write `---` inside a value.
- Two YAML parsers load it: **eemeli/yaml** in npm builds and **Bun.YAML** in native builds. When the YAML fails to parse, the file loads with no fields and the failure is logged only with `--debug`. The two parsers also disagree on valid-looking YAML:

| Frontmatter | npm build | native build |
|---|---|---|
| `description: Use when you need to wait...` followed by any key | `wait...` | two YAML documents, so every field is lost |
| a key written twice | parse error, every field lost | the last value wins |
| `description: Use when: X` (unquoted `: `) | parse error | parse error |
| `argument-hint: [issue-number]` | a list, not text | a list, not text |
| `timeout: "30"` in a hook | a string, so 2.1.x rejects all the file's hooks | same |

`validate_skill.py` runs both parsers and reports each difference. `format_skill.py` quotes values until both parsers agree.

## Skill fields (`SKILL.md`)

| Field | Type | Notes |
|---|---|---|
| `name` | string | Display name; the directory name is the command (`/dir-name`). Lowercase letters, digits and hyphens, at most 64 characters, same as the directory. In plugins it sets the command's last segment |
| `description` | string | The triggering text: "Use when ...", third person, at most 1,024 characters (Agent Skills spec and API limit). If it is missing, Claude sees only the body's first line |
| `when_to_use` | string | Extra trigger phrases, appended to description in the listing. The combined text is cut at 1,536 characters (`skillListingMaxDescChars`) |
| `argument-hint` | string | Autocomplete hint. Quote it: `"[issue-number]"` |
| `arguments` | string or list | Names for `$name` placeholders, mapped to argument positions |
| `disable-model-invocation` | boolean | `true` = only `/name` runs it; the skill also cannot be preloaded into subagents |
| `user-invocable` | boolean | `false` = hidden from the `/` menu |
| `allowed-tools` | string or list | Rules pre-approved for the turn that invokes the skill: `Read`, `Bash(git status *)`, `mcp__server__tool`. `${CLAUDE_SKILL_DIR}` is substituted in Bash rules |
| `disallowed-tools` | string or list | Tools removed while the skill is active |
| `model` | string | `opus`, `sonnet`, `haiku`, `fable`, `best`, `opusplan`, `sonnet[1m]`, `opus[1m]`, `default`, `inherit`, or a full model ID. Use an alias: dated IDs retire |
| `effort` | string | `low`, `medium`, `high`, `xhigh`, `max` |
| `context` | `fork` | Run the body as the task of a subagent that cannot see the conversation |
| `agent` | string | Subagent type for `context: fork`: `Explore`, `Plan`, `general-purpose`, or a custom agent name |
| `background` | boolean | With `context: fork`, `false` makes the invoking turn wait for the result (default `true`) |
| `hooks` | mapping | Registered when the skill is invoked and kept for the rest of the session |
| `paths` | string or list | Globs; the skill auto-loads only for matching files |
| `shell` | string | `bash` or `powershell`, for `` !`command` `` injection |
| `license`, `compatibility`, `metadata` | string / string / map | Agent Skills fields; Claude Code accepts them and does not act on them |

Booleans: write `true` and `false`. Claude Code 2.1.218 and later also accept `yes`/`no`/`on`/`off`/`1`/`0`. Earlier versions read those as false.

Commands (`.claude/commands/*.md`) accept the same fields except `name` and `paths`.

## Subagent fields (`.claude/agents/*.md`)

camelCase names. `name` and `description` are required, or the file is skipped without any message.

| Field | Notes |
|---|---|
| `name` | no `:` and no leading `-` |
| `description` | when Claude should delegate |
| `tools`, `disallowedTools` | comma-separated string or list. `mcp__server` covers a whole server. If no entry resolves to a tool, the subagent fails to launch |
| `model` | alias, full ID, or `inherit` |
| `permissionMode` | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`, `manual` |
| `maxTurns` | positive integer |
| `skills` | skills whose full content is preloaded |
| `mcpServers` | configured server names, or `{name: config}` inline definitions |
| `hooks` | active while the subagent runs; `Stop` becomes `SubagentStop`; `once` is ignored |
| `memory` | `user`, `project`, `local` |
| `background`, `omitClaudeMd` | booleans |
| `effort`, `isolation` (`worktree`), `color`, `initialPrompt`, `experimental` (`cacheTtl: 5m or 1h`) | |

## Hooks

```yaml
hooks:
  PreToolUse:                      # event (see below)
    - matcher: "Edit|Write"        # tool names or a regex; omit for all
      hooks:
        - type: command            # command | http | mcp_tool | prompt | agent
          command: python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/x/scripts/check.py"
          timeout: 30              # seconds, a number
          if: "Bash(git *)"        # optional permission rule; tool events only
```

- **Scope decides where hooks go.** Skill hooks are registered when the skill is invoked and stay on the main conversation for the rest of the session. Subagent hooks run only while that subagent runs. Plugin `hooks/hooks.json` hooks apply whenever the plugin is enabled. For rules that belong to one task, put the hooks on a subagent and run the skill in it with `context: fork` + `agent: <subagent>`. The main thread then never builds up hook loops.
- Command hooks get the event JSON on **stdin**. No `$TOOL_INPUT` variable exists, and `${tool_input.x}` is never substituted in commands.
- Exit 0: no objection. Exit 2: block; stderr goes to Claude. Any other exit code: a non-blocking error, and the action proceeds. `PermissionRequest` is the exception: it ignores exit 2, so deny there with its JSON decision (below).
- A script that cannot start fails the same non-blocking way, so the gate is silently off. Anchor paths with `${CLAUDE_PROJECT_DIR}` or `${CLAUDE_PLUGIN_ROOT}`, and quote them in shell form.
- JSON decisions (exit 0). PreToolUse: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny" | "allow" | "ask", "permissionDecisionReason": "..."}}`. PermissionRequest: `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny" | "allow", "message": "..."}}}`.
- Events with matchers: PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest, PermissionDenied, SessionStart, Setup, SessionEnd, Notification, SubagentStart, SubagentStop, PreCompact, PostCompact, PreModelSwitch, PostModelSwitch, ConfigChange, DirectoryAdded, FileChanged, StopFailure, InstructionsLoaded, UserPromptExpansion, Elicitation, ElicitationResult.
- Events without matchers: UserPromptSubmit, PostToolBatch, Stop, TeammateIdle, TaskCreated, TaskCompleted, WorktreeCreate, WorktreeRemove, MessageDisplay, CwdChanged.

## Plugins

| Path | Contains |
|---|---|
| `.claude-plugin/plugin.json` | manifest; only `name` (kebab-case) is required |
| `skills/<name>/SKILL.md` | skills, invoked as `/<plugin>:<name>` |
| `agents/**/*.md` | subagents; `hooks`, `mcpServers` and `permissionMode` are ignored here |
| `hooks/hooks.json` | `{"hooks": {...}}` with `${CLAUDE_PLUGIN_ROOT}` paths |
| `.mcp.json` | `{"mcpServers": {...}}`; pin server versions |

Tools from a plugin's MCP server are named `mcp__plugin_<plugin>_<server>__<tool>`. That full name is what goes in `allowed-tools`, `tools`, and hook matchers.

## Portable subset (claude.ai upload, Skills API, Agent Skills spec)

Only `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools` are accepted, and any other key fails the upload. The name must not contain "anthropic" or "claude", and the description may contain no XML tags. `export_skill.py` produces a compliant copy: it folds `when_to_use` into `description` and reports every dropped field.
