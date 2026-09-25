---
name: deep-review
description: "Use when the user asks for a thorough, independent review of a pull request, a risky diff, or a design before it merges."
when_to_use: "Examples: 'deep review this PR', 'get a second opinion on this migration', 'audit this change for bugs before I merge'."
argument-hint: "[branch, PR, or commit range]"
disable-model-invocation: true
allowed-tools: Bash(git diff *) Bash(git log *) Bash(git show *) Read Grep Glob
model: opus
effort: high
context: fork
agent: Explore
background: false
---

# Deep review

Review the changes in `$ARGUMENTS`. If it is empty, review the current branch against its merge base with the default branch. You run in a fresh subagent that cannot see the conversation, so get all the context from git.

## Method

1. List the changed files: `git diff --stat <base>...<head>`. Read every changed hunk, plus the callers of any function whose behavior changed.
2. For each change, ask what input or state makes it wrong. Look for:
   - off-by-one and boundary errors, null or empty inputs, error paths
   - concurrency, partial failure and retries
   - security: injection, authorization, secrets in code or logs
   - compatibility: public APIs, data formats, migrations
3. Report only problems you can make concrete. A finding needs a file, a line, and a scenario that shows the wrong outcome.

## Output

Report findings with the most severe first:

| Severity | File:line | Problem | Failing scenario | Fix |
|---|---|---|---|---|

End with one line: `Verdict: ship`, `Verdict: fix first`, or `Verdict: redesign`.

## Why the frontmatter looks like this

- `model: opus` and `effort: high` use a family alias, so the skill follows new model releases. A dated ID would break when that model is retired.
- `context: fork` with `agent: Explore` runs the review in a read-only subagent that skips CLAUDE.md. With `context: fork`, the `model` setting applies to that subagent.
- `background: false` makes the invoking turn wait for the verdict.
- `disable-model-invocation: true` means the review runs only when you type `/deep-review`.
