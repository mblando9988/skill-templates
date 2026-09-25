---
name: release-notes
description: "Use when the user asks for release notes, a changelog entry, or a summary of what changed between two git tags or since the last release."
when_to_use: "Examples: 'write release notes for v2.3', 'what changed since the last tag?', 'draft the changelog for this release'."
argument-hint: "[from-ref] [to-ref]"
arguments: [from, to]
allowed-tools: Bash(git describe *) Bash(git tag *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/group_commits.py *) Read
---

# Release notes

Write release notes for the commits between `$from` and `$to`. If `$from` is empty, use the latest tag (`git describe --tags --abbrev=0`). If `$to` is empty, use `HEAD`.

## Steps

1. Group the commits:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/group_commits.py <from-ref> <to-ref>
   ```

   The script prints JSON: `{"range": "...", "groups": {"feat": [...], "fix": [...], ...}}`. It exits 3 when a ref does not exist and 2 when a ref looks unsafe.
2. Write the notes in this shape:

   ```markdown
   ## <to-ref> - <one-line theme>

   ### New
   - <user-visible change, in plain words> (#<pr>)

   ### Fixed
   - ...

   ### Upgrade notes
   - <breaking changes and required actions; "None" if there are none>
   ```

3. Leave out chores, CI and refactors unless users can see the effect.
4. Show the draft to the user. Do not commit, tag or push. This skill pre-approves only read-only git commands and the grouping script.

## Why allowed-tools looks like this

- Each rule pre-approves one command shape for the turn that invokes the skill. Other tools still ask for permission.
- `Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/group_commits.py *)` matches the exact command in step 1, because Claude Code substitutes `${CLAUDE_SKILL_DIR}` both in `allowed-tools` and in the body.
- There is no bare `Bash`, which would pre-approve every shell command.
