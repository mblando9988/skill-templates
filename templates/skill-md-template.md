---
name: "{{skill-name}}"
description: "Use when {{trigger conditions: concrete situations, user phrasings, symptoms, error messages. Third person, no workflow summary, under 1,024 characters}}"
when_to_use: "{{extra trigger phrases and example requests, e.g. 'Examples: ship it, cut a release, deploy to staging'}}"
argument-hint: "{{[argument] shown in / autocomplete. Delete this line if the skill takes no arguments}}"
allowed-tools: Read Grep Glob
model: inherit
---

# {{Skill Title}}

{{One paragraph: what this skill lets Claude do, and the core principle. 2-3 sentences.}}

## When to use

- {{Symptom or situation}}
- {{Symptom or situation}}

When NOT to use: {{adjacent cases that belong to other skills or need no skill}}

## {{Core section: the recipe, checklist, or pattern that fixes the failure you observed}}

{{The actual guidance. One excellent worked example beats many mediocre ones.
Keep SKILL.md under 500 lines and 1,500 words. Put depth in references/ and
link it: see [references/{{topic}}.md](references/{{topic}}.md).
Reference bundled scripts as ${CLAUDE_SKILL_DIR}/scripts/<name>.py so they
resolve from any working directory.}}

## Common mistakes

| Mistake | Fix |
|---|---|
| {{what goes wrong}} | {{what to do instead}} |

<!--
Template notes (delete before shipping)

HOW TO FILL THE FRONTMATTER (Claude: fill every field that applies, delete the rest)

| Field | Fill it when | How |
|---|---|---|
| name | always | lowercase-hyphens, max 64 chars, same as the directory |
| description | always | "Use when ..." trigger conditions only: situations, user phrasings, error text. Third person. Max 1,024 chars. It is the only text Claude sees before loading the skill |
| when_to_use | always, when you have more trigger phrases than fit | Example requests and synonyms. Claude Code appends it to description in the skill listing (combined cap 1,536 chars). claude.ai uploads and the Skills API don't know this field: export_skill.py folds it into description for them |
| argument-hint | the skill takes arguments | "[issue-number]" or "[from] [to]". Keep the quotes: unquoted [x] is a YAML list |
| arguments | you want named $placeholders | arguments: [issue, branch] makes $issue and $branch work in the body |
| allowed-tools | the skill runs tools the user would otherwise approve each time | Scoped rules: Read Grep Bash(git status *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/x.py *). Never bare Bash. Grants last only for the turn that invokes the skill |
| disallowed-tools | tools must never run while active | e.g. AskUserQuestion for a background loop |
| model | the task needs a different model than the session | family alias only: opus, sonnet, haiku, fable, or inherit. Never a dated ID like claude-...-20250929: it breaks when that model retires |
| effort | the task needs deeper or cheaper reasoning | low, medium, high, xhigh, max |
| context + agent | the task is self-contained and should not see the conversation | context: fork, agent: Explore (read-only), Plan, general-purpose, or your own subagent name. The body must be a complete task |
| background | with context: fork, when the invoking turn must wait | background: false |
| disable-model-invocation | the skill has side effects (deploys, commits, sends) | true, so only /name runs it |
| user-invocable | background knowledge users should not type | false |
| paths | the skill only matters for some files | "src/**/*.ts, tests/**" |
| hooks | a rule must hold for the WHOLE session | Rarely. Skill hooks stay on the main thread for the rest of the session once the skill runs. For task-scoped enforcement, put the hooks on a subagent and set context: fork + agent: <subagent> (see ENFORCEMENT below) |
| license, compatibility, metadata | you share the skill | portable fields; metadata is a map of strings |

ENFORCEMENT: hooks belong on the agent that does the work

Hooks in a skill's frontmatter stay registered on the main conversation for
the rest of the session once the skill runs, so they keep firing on every
later tool call. Hooks in a subagent's frontmatter run only while that
subagent runs. So:

1. Put the hook on a subagent (templates/agent-md-template.md):

   hooks:
     PreToolUse:
       - matcher: Bash
         hooks:
           - type: command
             command: python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/{{skill-name}}/scripts/guard.py"
             timeout: 10

2. Run this skill in that subagent:

   context: fork
   agent: {{agent-name}}
   background: false        # wait for the result

Worked example: examples/skills/guarded-shell + examples/agents/guarded-operator.md.
Keep hooks in the skill itself only for session-wide policy, or with once: true.

- The script gets the event as JSON on stdin. Exit 2 blocks, with the reason
  on stderr. Exit 1 does NOT block. There is no $TOOL_INPUT variable.
- Hooks run in the session's working directory. ${CLAUDE_SKILL_DIR} works in
  the body and in allowed-tools, not in hooks: anchor with ${CLAUDE_PROJECT_DIR}.

BEFORE SHIPPING
- Replace every {{placeholder}}; delete unused optional fields and these notes.
- python3 scripts/format_skill.py <skill-dir>
- python3 scripts/validate_skill.py <skill-dir> --strict
- python3 scripts/check_docs_safety.py <skill-dir>
- For claude.ai or the API: python3 scripts/export_skill.py <skill-dir> --out dist/ --zip
- Ship evals/ (triggers.json + scenarios/) per references/condensed.md section 11.
-->
