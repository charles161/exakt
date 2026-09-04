# Exakt rename design

## Decision

Rename Forge to **Exakt** with the tagline **From intent to evidence.** The user-facing invocation becomes `$exakt <brief>` in Codex and `/exakt <brief>` in Claude Code. The public repository becomes `charles161/exakt`.

`Specd` was rejected because an active spec-driven coding-agent product already uses that exact name and command. Exakt is short, command-friendly, and describes the workflow's central promise: make intent explicit, implement it, and prove the result matches.

## Scope

This is a clean pre-release rename rather than a compatibility layer. Rename:

- the packaged skill directory and frontmatter;
- Codex and Claude command wrappers;
- plugin manifests and install examples;
- the Python controller and its human-facing output;
- local project state from `.forge/` to `.exakt/`;
- schema IDs, schema filenames where branded, report metadata, feedback filenames, state-home paths, tests, fixtures, and documentation;
- the GitHub repository after the renamed package passes locally.

The old GitHub URL may redirect after GitHub's repository rename, but the package will expose only one skill (`exakt`) so discovery and invocation remain unambiguous. Existing local `forge` installations must be removed before installing Exakt.

## Migration sequence

1. Change tests first so they demand the Exakt paths, identifiers, command output, and absence of stale user-facing Forge branding.
2. Observe the focused tests fail against the current Forge package.
3. Rename paths and apply the smallest complete brand/contract migration.
4. Run focused tests, then all 170 tests, compile checks, JSON validation, and stale-brand scans.
5. Commit the migration, fast-forward `main`, rename the public GitHub repository, update the remote, and push.
6. Remove the current Forge installation; install `charles161/exakt@exakt` through `npx skills`; start a fresh ephemeral Codex process and exercise `$exakt` against a clean disposable repository.

## Verification boundary

Completion requires evidence that:

- the source tree contains `skills/exakt`, `.claude/commands/exakt.md`, and `commands/exakt.toml`, with no old wrapper or skill path;
- package discovery finds exactly the Exakt skill;
- all tests pass and the controller gate rejects false completion as before;
- the report is self-contained and branded Exakt;
- the public repository is visible at `https://github.com/charles161/exakt`;
- a fresh `npx skills` install is attributed to `charles161/exakt` and a new Codex process actually invokes Exakt.

