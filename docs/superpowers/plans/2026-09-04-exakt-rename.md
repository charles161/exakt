# Exakt Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the public Forge skill and every owned contract surface to Exakt, then prove a fresh npx installation works in a new Codex process.

**Architecture:** Perform a clean pre-release rename with one canonical `exakt` skill and no duplicate compatibility skill. Preserve behavior while migrating package paths, commands, persisted-state names, schema domains, installer logic, UI copy, documentation, and the public repository as one coherent contract.

**Tech Stack:** Agent Skills SKILL.md, Python 3 standard library, JSON Schema files, unittest, Codex/Claude command wrappers, Git, GitHub CLI, Vercel Agent Skills CLI.

---

### Task 1: Lock the rename contract in tests

**Files:**
- Modify: `tests/test_package_structure.py`
- Modify: `tests/test_installer.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_renderer.py`
- Modify: `tests/test_state_store.py`

- [ ] **Step 1: Change focused assertions to the Exakt contract**

Require `skills/exakt`, `$exakt`, `/exakt`, `.exakt/exakt-state.json`, `urn:exakt:schema:`, `exakt-report-v1`, Exakt report text, and Exakt state-home directories. Add a package assertion that old `skills/forge`, wrapper files, and user-facing Forge markers are absent.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest -v \
  tests.test_package_structure \
  tests.test_installer \
  tests.test_cli \
  tests.test_contracts.SchemaDocumentTests \
  tests.test_renderer \
  tests.test_state_store.StateHomeResolutionTests
```

Expected: failures identify the still-present Forge paths, identifiers, output, and defaults.

- [ ] **Step 3: Commit only after the implementation is green**

The test edits and implementation belong in the same behavior-preserving rename commit so the branch never advertises an unusable package.

### Task 2: Rename package entry points and local state

**Files:**
- Rename: `skills/forge/` → `skills/exakt/`
- Rename: `skills/exakt/scripts/forge.py` → `skills/exakt/scripts/exakt.py`
- Rename: `.claude/commands/forge.md` → `.claude/commands/exakt.md`
- Rename: `commands/forge.toml` → `commands/exakt.toml`
- Modify: `skills/exakt/SKILL.md`
- Modify: `skills/exakt/scripts/install.py`
- Modify: `skills/exakt/scripts/exakt.py`
- Modify: `skills/exakt/scripts/state_store.py`
- Modify: `.gitignore`

- [ ] **Step 1: Rename the owned paths**

Use `git mv` for all four public path changes so history remains traceable.

- [ ] **Step 2: Migrate invocation and state defaults**

The canonical snippets become:

```text
$exakt <task or product brief>
/exakt <task or product brief>
.exakt/exakt-state.json
.exakt/exakt-report.html
```

The installer must install to a directory named `exakt`, refuse collisions as before, and create the `exakt.md` Claude wrapper.

- [ ] **Step 3: Preserve the existing safety behavior**

Only names, paths, identifiers, and copy change. Approval binding, crash recovery, canonical JSON, action policy, phase gates, and verification semantics remain unchanged.

### Task 3: Rename contracts, manifests, report UI, and documentation

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `.claude-plugin/plugin.json`
- Modify: `skills/exakt/schemas/*.json`
- Rename: `skills/exakt/schemas/forge-feedback-v1.json` → `skills/exakt/schemas/exakt-feedback-v1.json`
- Modify: `skills/exakt/scripts/contracts.py`
- Modify: `skills/exakt/scripts/render_report.py`
- Modify: `skills/exakt/assets/report-template.html`
- Modify: `skills/exakt/references/*.md`
- Modify: `skills/exakt/agents/*.md`
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `examples/gst-decoded-navigation.{json,html}`
- Modify: `tests/fixtures/report-state.json`

- [ ] **Step 1: Migrate the machine-readable brand domain**

Replace owned identifiers consistently:

```text
urn:forge:schema:*     → urn:exakt:schema:*
forge-*-v1             → exakt-*-v1
Forge Canonical JSON   → Exakt Canonical JSON
```

Rename only the branded feedback schema filename; other schema filenames describe their domain and stay stable.

- [ ] **Step 2: Migrate user-facing copy and metadata**

Use `Exakt`, `exakt`, and the tagline `From intent to evidence.` consistently in manifests, report titles, CLI output, wrapper descriptions, docs, examples, and feedback download names.

- [ ] **Step 3: Update installation documentation**

Document the public commands exactly:

```bash
npx skills add charles161/exakt
npx skills add charles161/exakt --skill exakt -g -a codex claude-code -y
```

### Task 4: Prove the local migration

**Files:**
- Modify as failures require: only files already listed above

- [ ] **Step 1: Run the focused suite and verify GREEN**

Run the focused command from Task 1. Expected: all selected tests pass.

- [ ] **Step 2: Run complete verification**

```bash
python3 -m unittest discover -v
python3 -m compileall -q skills tests
git diff --check
python3 skills/exakt/scripts/exakt.py --help
```

Expected: 170 or more tests pass, compilation exits zero, no whitespace errors, and the Exakt CLI help renders.

- [ ] **Step 3: Scan for stale public branding**

```bash
rg -n --hidden --glob '!.git/**' \
  'skills/forge|commands/forge|\\$forge|/forge|forge-skill|\\.forge|urn:forge|forge-[a-z-]+-v1|\\bForge\\b' .
```

Expected: no matches outside the approved rename design/plan's historical explanation.

- [ ] **Step 4: Commit the green rename**

```bash
git add -A
git commit -m "feat: rename Forge to Exakt"
```

### Task 5: Publish the renamed repository

**Files:**
- Modify: local Git remote configuration only

- [ ] **Step 1: Fast-forward local main**

From the primary worktree, verify it is clean and run:

```bash
git merge --ff-only rename/exakt
```

- [ ] **Step 2: Rename the GitHub repository**

Confirm `charles161/exakt` does not already exist, then use GitHub CLI to rename `charles161/forge-skill` to `charles161/exakt`.

- [ ] **Step 3: Update remotes and push**

Set `origin` to the renamed repository URL in the shared repository and push `main`. Verify GitHub reports public visibility and the expected default branch/HEAD commit.

### Task 6: Fresh-install and end-to-end test Exakt

**Files:**
- Create temporarily: a clean test repository under `/tmp/`
- Create in that repository: `.exakt/exakt-state.json` and `.exakt/exakt-report.html`

- [ ] **Step 1: Remove the previous installation**

Remove only the verified global paths `~/.agents/skills/forge` and `~/.agents/skills/exakt` if present. Do not touch unrelated skills.

- [ ] **Step 2: Install from the renamed public repository**

```bash
npx -y skills@latest add charles161/exakt@exakt -g -a codex -y
npx -y skills@latest list -g -a codex --json
```

Expected: one global skill named `exakt`, sourced from `charles161/exakt`; no `forge` installation remains.

- [ ] **Step 3: Start a genuinely new Codex process**

Run an ephemeral Codex session in a clean disposable Git repository with `$exakt` and a bounded implementation task. Require tests, the JSON state, and the rendered HTML report.

- [ ] **Step 4: Independently verify the result**

Run the generated tests and Exakt verification gate outside the child session, inspect the diff and report markers, and confirm the child did not publish or deploy anything.

- [ ] **Step 5: Record the outcome**

Update operational memory and audit the public rename, fresh install, commands run, pass/fail counts, timing, and any remaining caveat.
