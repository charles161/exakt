# Exakt

**From intent to evidence.**

Exakt is a portable engineering workflow for turning a task or full product
brief into a researched specification, architecture, acceptance criteria,
implementation plan, working change, and evidence-calibrated handoff. It runs
inside the coding harness you already use and adds a self-contained local HTML
project view rather than another IDE or hosted product.

## Invocation

- Codex: `$exakt <task or product brief>`
- Claude Code: `/exakt <task or product brief>`
- Generic harness: load `skills/exakt/SKILL.md` with the request.

Exakt chooses a proportional task or product workflow, inspects the real
repository, collaborates on consequential decisions, uses bounded specialist
agents when the host supports them, and compares the actual result with the
approved acceptance criteria before claiming success.

The compact architecture and truth model are documented in
[`docs/design.md`](docs/design.md).

## Install

The recommended primitive is the open Agent Skills CLI. Install Exakt into the
current project interactively:

```sh
npx skills add charles161/exakt
```

Or install it globally for both Codex and Claude Code without prompts:

```sh
npx skills add charles161/exakt --skill exakt -g -a codex claude-code -y
```

For a manual installation or a harness the Skills CLI does not detect:

```sh
git clone https://github.com/charles161/exakt.git
cd exakt

# Install for Codex
python3 skills/exakt/scripts/install.py --host codex

# Install for Claude Code
python3 skills/exakt/scripts/install.py --host claude
```

Use `--root <path>` for an isolated or generic install. The installer refuses
to overwrite an existing skill.

## Project view

The skill maintains a compact JSON state and renders a responsive, offline HTML
report with the brief, architecture, plan, critiques, progress, verification,
and evidence:

```sh
python3 skills/exakt/scripts/exakt.py init "Add shareable chapter navigation" --mode task
python3 skills/exakt/scripts/exakt.py render .exakt/exakt-state.json --force
python3 skills/exakt/scripts/exakt.py verify .exakt/exakt-state.json
```

The minimum completion gate refuses `verified` when acceptance criteria or
fresh verification evidence remain missing. External, destructive, costly, and
production actions remain subject to the host's explicit approval controls.

## Verify the package

From this directory, run:

```sh
python3 -m unittest tests.test_package_structure -v
```

The suite covers package paths, contracts, state/journal safety, replay and
action ordering, the CLI, and the report renderer.

## Reproduce the Task 1 red state

The original pre-scaffold failure can be reconstructed from the immutable base
commit and structural-test blob recorded in Git:

```sh
python3 tests/evidence/reproduce_task1_red.py
```

The evidence command verifies the object identities and confirms that the six
package files were absent at the base commit. It then runs the exact original
test blob in that package state and succeeds only after observing the child
test exit 1 with five tests, one failure, and five errors.
