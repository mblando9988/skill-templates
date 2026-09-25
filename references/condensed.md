# References — Condensed

Single-file cheat sheet distilled from 11 reference notes. Use for designing, writing, and reviewing skills.

## 1. Frontmatter

Keep SKILL.md under 500 lines / 1500 words. Depth goes in `references/`, runnable files in `assets/`, helpers in `scripts/`. No `<details>` blocks.

Key fields (full reference: `references/claude-code-frontmatter.md`):

| Field | Purpose |
|---|---|
| `name` | lowercase-hyphens, ≤64 chars, must match directory (the directory is the command) |
| `description` | trigger conditions only, third person, ≤1024 chars. No workflow summary |
| `when_to_use` | extra trigger phrases; Claude Code appends it to description (combined listing cap 1,536 chars). Not an Agent Skills field: `export_skill.py` folds it into description |
| `argument-hint`, `arguments` | autocomplete hint (quote it: `"[issue]"`), named `$name` placeholders |
| `allowed-tools` / `disallowed-tools` | pre-approve scoped rules for the invoking turn / remove tools. Never bare `Bash` |
| `model`, `effort` | family alias only (`opus`, `sonnet`, `haiku`, `fable`, `inherit`); never a dated ID (`claude-*-YYYYMMDD` rots) |
| `context: fork` + `agent:` (+ `background`) | run the body as a task in a subagent |
| `disable-model-invocation`, `user-invocable` | `true`/`false` literals (`yes` only works on Claude Code ≥ 2.1.218) |
| `hooks` | registered when the skill is invoked and kept on the main thread for the rest of the session (`once: true` removes a hook after its first successful run). For task-scoped enforcement, put hooks on a subagent and use `context: fork` + `agent:` |
| `paths`, `shell` | activate only for matching files; `bash` or `powershell` for `!` injection |
| `license`, `compatibility`, `metadata` | portable Agent Skills fields |

Claude Code silently ignores misspelled fields (`allowed_tools`, `tools` in a SKILL.md). If the YAML fails to parse, the skill loads with no fields. Run `validate_skill.py`: it parses with both of Claude Code's YAML parsers (Bun.YAML, eemeli/yaml).

Hooks:

```yaml
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/<name>/scripts/check_input.py"
```

- Hook command gets JSON on stdin, replies via exit code / stdout JSON. No `$TOOL_INPUT` env vars.
- Never interpolate payload text into shell strings — parse stdin inside the script.
- Exit 0 = no objection (normal permission flow applies). Exit 2 = block, stderr goes to Claude. Any other code (including 1) = non-blocking error: the action proceeds. Exception: `PermissionRequest` ignores exit 2; deny there with JSON `hookSpecificOutput.decision.behavior: "deny"`.
- Hooks run in the session's working directory: anchor paths with `${CLAUDE_PROJECT_DIR}` (`${CLAUDE_SKILL_DIR}` is not substituted in hook commands). A script that cannot start is a non-blocking error, so the gate is silently off.
- Scope: a skill's hooks keep firing on the main thread for the whole session. A subagent's hooks run only while it runs. Enforce task rules with a hooked subagent that the skill forks into (`examples/skills/guarded-shell` + `examples/agents/guarded-operator.md`).
- Template: `templates/hook-script-template.py` (fails closed on bad input).

Placement: personal `~/.claude/skills/<name>/`, project `.claude/skills/`.

## 2. Degrees of Freedom

Match specificity to fragility. Freedom decreases from thinking → acting.

- **High (prose/heuristics):** many valid approaches, context-dependent. Use for analysis, decisions, recommendations.
- **Medium (pseudocode/parameterized steps):** known-good pattern, details vary. Use for generation, config, file structure.
- **Low (exact script/command):** one right way, high failure cost. Use for validation, deploys, state mutations.

Typical skill: high for analysis → medium for plan → low for execution → high for iteration.

Mistakes: low everywhere = brittle; high everywhere = inconsistent; validation must always be low (exact script).

## 3. Longevity (advisory)

Don't self-score as a gate. Enforce mechanically: no pinned versions, require extension points.

Design for time:

- Principle over implementation. Document WHY for every non-obvious decision.
- Abstract volatile deps (model IDs, tool versions, external APIs).
- Minimum 2 documented extension points (e.g. `patterns/`, new output format, new validation rule).
- Loose coupling, graceful degradation, version-agnostic paths.

Anti-patterns: pinned dated model, tool-specific title (`ESLint 8 Generator` → `Lint Config Generator`), closed list with no extension path, `npm run build` hardcoded (make configurable), point-in-time claims (`now supports 200K`).

Reject if lifespan is weeks-months. Ship if 2+ years with extension points.

## 4. Iterate

Loop: USE on real task → NOTICE friction → IDENTIFY location (SKILL.md vs references/ vs scripts/ vs assets/) → IMPLEMENT one fix → TEST by re-running → REPEAT.

After every change: re-run evals. New real-world failure → new eval scenario first, then fix.

| Signal | Fix |
|---|---|
| Asks question skill should answer | Add answer to SKILL.md / reference |
| Output shape varies | Add low-freedom template + example |
| Skips step | Explicit checkpoint: "confirm X before proceeding" |
| Invents own approach | Lower freedom for that step |
| Correct execution, wrong result | Fix instructions, not the agent |
| Manual step error-prone | Replace prose with script |
| Too many turns | Consolidate steps |

Iterate if <30% needs change. Redesign if core approach wrong, users work around it, or >50% rewrite.

## 5. Thinking Lenses (11, use what helps)

Load-bearing for every skill: **Inversion, Pareto, Root Cause**. Scan rest, apply any that changes design.

1. First Principles: strip conventions, atomic value?
2. Inversion: list every way to fail (adoption/execution/integration/evolution) → anti-patterns.
3. Second-Order: then-what chain 3-4 deep.
4. Pre-Mortem: assume dead in 6mo, why? Mitigate top risks now.
5. Systems: inputs/processes/outputs, integrations, feedback loops, leverage points.
6. Devil's Advocate: strongest counterargument per decision; keep only if it survives.
7. Constraints: hard (token limits) vs soft (assumed conventions); challenge soft.
8. Pareto: which 20% gives 80% value? Cut rest to backlog.
9. Root Cause: ask Why 5x; fix cause, not symptom.
10. Comparative: weighted criteria table for architecture options.
11. Opportunity Cost: what you gain vs sacrifice, why worth it.

Protocol: rapid scan all 11 (H/M/L relevance) → deep-dive Highs → resolve conflicts explicitly → require ≥3 actionable insights.

## 6. Regression Questions

Max 4 productive rounds. Stop earlier when a round changes no decision.

Categories to rotate (2-3 per round):

1. Missing: what am I missing / assuming / edge cases?
2. Experts: domain, UX, architect, security, performance, maintainer — what would each flag?
3. Failure: what causes complete / silent / adoption / obsolete / unmaintainable / conflict failure? Likelihood × impact → mitigation.
4. Temporal: now / 1wk / 1mo / 6mo / 1yr / 2yr / 5yr — still valid?
5. Completeness: lenses, domains, stakeholders, integrations, quality attrs covered?
6. Meta: did last round change a decision? If no, stop. What haven't I asked? Where would I lose a debate?
7. Scripts: what repeats identically? What needs validation / state / verification? Can it run overnight unaided? How does a fresh agent recover from script failure?

Per skill type: executors (inputs/outputs/side-effects), analyzers (misleading results, confidence), generators (templates, customization, validation), orchestrators (composition, handoff format, failure), validators (false pos/neg, severity).

## 7. Scripts — When and How

Skill with scripts: automated, self-verifying, persistent, deterministic.

Create script if: deterministic reliability needed, logic repeats >2x, state persists, self-verification needed, complex calc/transform, external API, progress tracking. Else prose is enough.

7 types: Validation, State, Generation, Transformation, Integration, Visualization, Calculation. Default language Python (stdlib only). Bash only if <30 lines glue.

Agentic patterns: self-verify output, retry with fallback, persist JSON state in `~/.cache/<skill>/`, structured `--json` output to stdout + human text / errors to stderr, graceful fallback for optional deps.

Call from skill: direct (`python scripts/validate.py <file>`), conditional (single vs batch), piped, subcommands (`tracker.py init/add/update/status`).

Exit codes: 0 success, 1 fail, 2 bad args, 3 file missing, 10 validation fail, 11 verification fail.

Document every script in SKILL.md with purpose + usage + exit codes.

Hooks + scripts: PreToolUse = gate, PostToolUse = verify, Stop = cleanup. Hook script parses stdin JSON, exit 0 allow / 2 block. Exit 1 does not block.

## 8. Script Patterns — Quick Ref

- `Result`: `{success, message, data, errors, warnings}` + `to_dict()`, `__bool__`.
- `ValidationResult`: `passed/warnings/errors`, `check(name, cond, msg)`, `is_valid`, `summary`, `format_report()`.
- Simple argparse: one job, `path` + `--verbose/--json/--strict`, exit 0/1/2.
- Subcommand argparse: `init/run/status` dispatch dict, for trackers.
- JSON state: `load_state()` with corrupt-backup, `save_state()` atomic via `.tmp` rename, `updated_at` stamp.
- Fallback: `try: import yaml/rich except ImportError: basic parse/plain print`.
- Exit enum: SUCCESS 0, GENERAL 1, ARGS 2, NOTFOUND 3, VALIDATION 10, VERIFY 11, TIMEOUT 30.
- Progress: `[====----] 50%`, `[ ]/[>]/[x]/[V]/[!]/[-]` icons, tree render.
- Self-verify: `execute → verify_func → Result`; verifiers like `verify_json`, `verify_file_exists`, `verify_non_empty`.

## 9. Spec (written before generation)

Minimal tier (default) — only artifact generator sees this, must stand alone:

```markdown
# Spec: <skill-name>
## Problem — who hurts, when, verbatim baseline failures
## Requirements — R1 explicit / R2 implicit / R3 discovered, each traceable
## Failure classification & guidance form — rule-skip/wrong-shape/omission/conditional → prohibition/recipe/template/conditional; freedom level per section + why
## Key decisions — | Decision | Choice | WHY | Rejected alt |
## Description draft — trigger-only + keywords from baseline
## Scripts — needs_scripts yes/no + name/category/purpose
## Success criteria & eval plan — RED tasks, positive + near-miss triggers, measurable checks
```

Full tier (only for infra/meta-skills others depend on) adds: Architecture (pattern + phases + verification), Evolution (extension points, obsolescence triggers), Anti-patterns.

Validate: no placeholders, every decision has WHY + rejected alt, baseline pasted verbatim, eval plan complete.

## 10. Review — Lint + One Adversary

No approval panel. Ship when lint passes + evals pass + no blocker survives.

1. Mechanical (fix all ERRORs first):
```bash
python3 scripts/format_skill.py <dir>
python3 scripts/validate_skill.py <dir> --strict
python3 scripts/check_docs_safety.py <dir>
python3 scripts/export_skill.py <dir> --out dist/   # only for claude.ai / Skills API
```
2. Behavioral: RED failures recorded, GREEN runs clear them, trigger recall/precision checked.
3. One fresh adversarial reviewer (no shared history) charged to REFUTE:
   1. FACTS — verify every API/command/path claim
   2. OVER-TRIGGER — 3 realistic requests where it would load but shouldn't
   3. MISLEADING — case where following verbatim is worse than ignoring
   4. GAPS — likeliest real variant it doesn't survive
   5. STRUCTURE — workflow summary in description? misplaced body? dead sections?
   Findings as blocker / should-fix / note, with concrete failing case. No scores.
4. Disposition: blockers → fix + re-run evals + fresh reviewer (max 3 cycles); should-fix → fix if within budget else log as limit; notes → discretion.

Checklists: guidance form matches failure, freedom matches fragility, scripts only where deterministic, no circular refs, trigger-only description, runnable examples, references linked.

## 11. Testing — RED → GREEN → Triggers

Quality = behavior, not documents.

RED (before design): 2-3 real target prompts → fresh subagent WITHOUT skill → capture verbatim failures. No failure → don't build.

Failure → form:

| Failure | Right form | Wrong form |
|---|---|---|
| Knows rule, skips under pressure | Prohibition + rationalization table + red flags | Soft "prefer/consider" |
| Right intent, wrong shape | Positive recipe/contract, parts in order | Prohibition list |
| Omits required element | REQUIRED slot in template | Prose reminders |
| Conditional behavior | Conditional on observable predicate | Rule + exemption clauses |

No "don't X unless it matters" — make exceptions their own conditionals.

GREEN (after gen): re-run RED tasks WITH skill in fresh agents (1x min, 3x for discipline skills under pressure: time + sunk cost + authority). Gate: zero baseline failures. Fix body, don't weaken scenario.

Triggers: ~10 positives + ~5 near-misses. Static keyword check + live roster test (which skill would load?). Hold out 2-3 positives — measure only, never edit against. Description: third person, "Use when…", symptoms, no workflow summary.

IMPROVE: blind A/B old vs new, shuffled, fresh judge. Ship only if wins/ties all and wins ≥1.

Ship with tests:
```
<skill>/evals/triggers.json
<skill>/evals/scenarios/01-<slug>.md  # task + baseline_failure + assertions + runs
```
`--static` = CI-safe lint, `--live` = headless runs. Re-run after any edit like unit tests.

## 12. Subagents and Plugins

Subagent files (`.claude/agents/<name>.md`) use camelCase fields; `name` and `description` are required, or the file is skipped.

| Field | Purpose |
|---|---|
| `tools` / `disallowedTools` | allowlist / denylist. `mcp__<server>` covers a whole server. A specifier in `disallowedTools` still removes the whole tool |
| `skills` | preload skills' full content at startup (not skills with `disable-model-invocation: true`) |
| `mcpServers` | `- github` reuses a configured server; `- name: {type: stdio, command: ...}` connects only for this agent |
| `model`, `effort`, `maxTurns`, `permissionMode`, `memory`, `isolation`, `hooks` | per-agent overrides; `Stop` hooks become `SubagentStop` |

Plugins bundle skills, agents, hooks and MCP servers:

- Components live at the plugin root (`skills/`, `agents/`, `hooks/hooks.json`, `.mcp.json`), never inside `.claude-plugin/`.
- Tools from a plugin's MCP server are named `mcp__plugin_<plugin>_<server>__<tool>`; `mcp__<server>__...` matches nothing.
- Plugin agents ignore `hooks`, `mcpServers` and `permissionMode` (security): ship servers in `.mcp.json` and hooks in `hooks/hooks.json`.
- Templates: `templates/agent-md-template.md`, `templates/plugin/`. Worked examples: `examples/agents/`, `examples/plugins/docs-toolkit/`.
