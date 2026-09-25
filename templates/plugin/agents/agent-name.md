---
name: "{{agent-name}}"
description: "Use when {{the situations Claude should delegate to this plugin agent}}"
tools: Read, Grep, Glob
model: inherit
skills:
  - "{{skill-name}}"
---

{{System prompt. In a plugin, Claude Code ignores hooks, mcpServers and permissionMode
in agent files: ship servers in .mcp.json and hooks in hooks/hooks.json.
Rename this file to the agent name. See templates/agent-md-template.md for the full field guide.}}
