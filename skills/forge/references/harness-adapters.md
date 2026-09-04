# Harness adapters

Preserve Forge semantics while using only capabilities the active host actually exposes.

| Need | Codex | Claude Code | Generic fallback |
|---|---|---|---|
| Invoke | `$forge <task>` | `/forge <task>` | Load `SKILL.md` with the task |
| Question | Native input UI when available | Native question tool when available | One concise A/B/C question |
| Plan/progress | Plan and commentary primitives | Task/status primitives | Short Markdown checklist |
| Specialists | Host subagents | Host subagents | Separated lead-agent passes |
| Evidence | Native diff, terminal, browser, file links | Native diff, terminal, browser, file links | Commands plus artifact paths |
| Report | `forge.py render` and a local link | `forge.py render` and a local link | `forge.py render` and its path |

Detect capabilities before using them. Never imply that a host has an approval UI, background worker, sandbox, persistent task system, or independent agent when it does not.

## Specialist handoff

Give a specialist:

- one bounded objective and perspective;
- relevant repository paths and approved requirements;
- explicit read-only scope;
- requested evidence or findings format; and
- a request to separate facts, inferences, and unknowns.

Run independent subtasks concurrently only when they do not share mutable state. Specialists must not edit, approve work, claim completion, or spawn further agents. Integrate their findings in the lead context and verify important claims directly.

## Report command

Use the installed `skills/forge/scripts/forge.py` as the state/report entry point. Check `python3 skills/forge/scripts/forge.py --help`, then invoke its `render` command with the current work item. Do not handcraft or silently patch generated HTML. Keep a terminal handoff if rendering is unavailable or fails.
