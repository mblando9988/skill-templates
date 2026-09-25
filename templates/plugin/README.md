# {{plugin-name}}

Plugin skeleton. Copy this folder, rename `skills/skill-name/` and `agents/agent-name.md`, and replace every `{{placeholder}}`.

| Path | Loaded as | Notes |
|---|---|---|
| `.claude-plugin/plugin.json` | manifest | only `name` is required; component folders stay at the plugin root |
| `skills/<name>/SKILL.md` | `/{{plugin-name}}:<name>` | same fields as any skill |
| `agents/<name>.md` | `{{plugin-name}}:<name>` | `hooks`, `mcpServers` and `permissionMode` are ignored here |
| `.mcp.json` | MCP servers | tools are named `mcp__plugin_{{plugin-name}}_<server>__<tool>`; pin the server version |
| `hooks/hooks.json` | hooks | use `${CLAUDE_PLUGIN_ROOT}` paths, preferably in exec form (`command` + `args`) |
| `scripts/` | helper scripts | `guard.py` is a copy of `templates/hook-script-template.py` |

Check before publishing:

```bash
python3 scripts/format_skill.py <plugin-dir>
python3 scripts/validate_skill.py <plugin-dir> --strict
python3 scripts/check_docs_safety.py <plugin-dir>
claude plugin validate <plugin-dir>
```
