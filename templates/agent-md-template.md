---
name: "{{agent-name}}"
description: "Use when {{the situations Claude should delegate to this subagent: task types, trigger phrases, signals in the request}}"
tools: Read, Grep, Glob
model: inherit
skills:
  - "{{skill-to-preload}}"
---

{{Role in one sentence: who this subagent is and what it delivers.}}

When invoked:

1. {{First step, with the exact files, commands or tools to use}}
2. {{Next step}}
3. {{How to verify the work before replying}}

Reply with:

- {{the exact shape of the result the main conversation gets back}}

<!--
Template notes (delete before shipping). Save as .claude/agents/<name>.md
(project), ~/.claude/agents/<name>.md (personal), or <plugin>/agents/<name>.md.

Subagent field names are camelCase and must match exactly; Claude Code
silently ignores anything else. A skill's "allowed-tools" does nothing here.

| Field | Fill it when | How |
|---|---|---|
| name | always | lowercase-hyphens. No ':' and no leading '-', or the file is skipped |
| description | always | when Claude should delegate. Without it, the file is skipped |
| tools | restrict the agent | allowlist: Read, Grep, Bash, mcp__github (a whole server), mcp__plugin_<plugin>_<server>__<tool> |
| disallowedTools | inherit everything except a few | Write, Edit. A specifier like Bash(git push *) still removes all of Bash |
| model | the job needs a different model | sonnet, opus, haiku, fable, or inherit. Aliases, not dated IDs |
| effort | reasoning depth | low, medium, high, xhigh, max |
| skills | the agent needs domain knowledge up front | skill names; their full content is injected at startup. Skills with disable-model-invocation: true cannot be preloaded |
| mcpServers | the agent needs servers the main session doesn't have | "- github" reuses a configured server; "- name: {type: stdio, command: npx, args: [...]}" connects only for this agent |
| hooks | a rule must hold while this agent works | THE place for task-scoped enforcement: agent hooks start and stop with the agent, while skill hooks stay on the main thread for the whole session. Same format as skills; Stop becomes SubagentStop. A skill reaches this agent with context: fork + agent: <name> |
| permissionMode | the agent needs a specific mode | default, acceptEdits, auto, dontAsk, plan (bypassPermissions only applies when the session already bypasses) |
| maxTurns | cap long loops | a positive integer |
| memory | cross-session learning | user, project, or local |
| isolation | the agent edits files you want isolated | worktree |
| background, omitClaudeMd, color, initialPrompt, experimental | rarely | see references/claude-code-frontmatter.md |

In a plugin's agents/ folder, Claude Code ignores hooks, mcpServers and
permissionMode (security). Ship the plugin's MCP servers in .mcp.json and its
hooks in hooks/hooks.json instead. See templates/plugin/.

Check: python3 scripts/format_skill.py <file> && python3 scripts/validate_skill.py <file> --strict
-->
