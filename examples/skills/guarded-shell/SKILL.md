---
name: guarded-shell
description: "Use when the user asks to run shell commands on production systems, shared databases, or anything they describe as risky, destructive, or hard to undo."
when_to_use: "Trigger phrases: 'run this on prod', 'clean up the server', 'delete the old data', 'be careful with this command', 'force-push the fix'."
argument-hint: "[what to run, and where]"
disable-model-invocation: true
context: fork
agent: guarded-operator
background: false
---

# Guarded shell

Carry out this risky shell task: $ARGUMENTS

You run as the `guarded-operator` subagent. You start without the conversation's history, so everything you need is in the task above and in the repository. Its PreToolUse hook checks every Bash command before it runs.

## Steps

1. Read the current state first: `git status`, `git diff`, and read-only queries.
2. Plan the change as a short list of commands. For each one, give what it changes and how to undo it.
3. Run the commands one at a time and check each result before you move on.
4. If the guard blocks a command, stop and report it. Never rephrase a blocked command to get past the guard.

## What the guard blocks

`scripts/guard_bash.py` gets each tool call as JSON on stdin and exits 2 to block. Exit 1 would not block, because Claude Code treats it as a non-blocking error. It blocks:

| Pattern | Why |
|---|---|
| `rm -rf` on `/`, `~`, `*` or `..` | irreversible deletes |
| `git push --force` without `--force-with-lease` | overwrites other people's work |
| `git reset --hard`, `git clean -fdx` | discards uncommitted work |
| `DROP TABLE`, `DROP DATABASE`, `TRUNCATE` | destroys data |
| `curl ... \| sh`, `eval`, `bash -c "$(...)"` | runs code nobody reviewed |
| `chmod -R 777`, `mkfs`, `dd of=/dev/...` | damages the system |

The guard fails closed: if its input is missing or malformed, it blocks the command.

## Why a forked agent carries the hook

A hook declared in a skill's frontmatter stays registered on the main conversation for the rest of the session once the skill runs. Every later Bash call in that session would keep going through the guard. This skill declares no hooks. Instead, it runs in the `guarded-operator` subagent (`context: fork`, `agent: guarded-operator`), whose frontmatter owns the hook. Subagent hooks run only while the subagent runs, so the guard ends when the task ends. `background: false` makes the invoking turn wait for the report. `disable-model-invocation: true` means the task runs only when you type `/guarded-shell`.

## Install

- Copy this folder to `.claude/skills/guarded-shell/` and `examples/agents/guarded-operator.md` to `.claude/agents/`. The agent's hook runs `.claude/skills/guarded-shell/scripts/guard_bash.py` through `${CLAUDE_PROJECT_DIR}`, because hooks run in the session's working directory.
- Test the guard: `echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | python3 ${CLAUDE_SKILL_DIR}/scripts/guard_bash.py; echo "exit $?"` should print `exit 2`.
