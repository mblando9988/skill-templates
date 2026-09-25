---
name: answer-from-docs
description: "Use when the user asks how something in this project works and the answer should come from the project's docs/ folder, with citations."
when_to_use: "Examples: 'what do the docs say about auth?', 'find where the deploy process is documented', 'answer from our docs'."
allowed-tools: mcp__plugin_docs-toolkit_docs__search_files mcp__plugin_docs-toolkit_docs__read_text_file mcp__plugin_docs-toolkit_docs__list_directory mcp__plugin_docs-toolkit_docs__directory_tree
disallowed-tools: mcp__plugin_docs-toolkit_docs__write_file mcp__plugin_docs-toolkit_docs__edit_file mcp__plugin_docs-toolkit_docs__move_file mcp__plugin_docs-toolkit_docs__create_directory
---

# Answer from docs

Answer only from the files in the project's `docs/` folder, which the bundled `docs` MCP server exposes read-only.

1. Find candidate files with `search_files` (the tool's full name is `mcp__plugin_docs-toolkit_docs__search_files`). Use `directory_tree` when the layout is unknown.
2. Read the relevant files with `read_text_file` and quote the passages that answer the question.
3. Answer in a few sentences, then list sources as `docs/<path>#<heading>`.
4. If the docs don't cover the question, say so plainly. Don't fill the gap from general knowledge.

## Why the names look like this

Tools from a server a plugin bundles are named `mcp__plugin_<plugin>_<server>__<tool>`. Here that is `mcp__plugin_docs-toolkit_docs__...`. A rule written as `mcp__docs__search_files` matches nothing, and `validate_skill.py` flags it.
