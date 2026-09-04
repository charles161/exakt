import importlib.util
import json
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCRIPT = PACKAGE_ROOT / "tests/evidence/reproduce_task1_red.py"
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "reproduce_task1_red",
    EVIDENCE_SCRIPT,
)
if EVIDENCE_SPEC is None or EVIDENCE_SPEC.loader is None:
    raise RuntimeError(f"cannot load evidence helper: {EVIDENCE_SCRIPT}")
EVIDENCE_MODULE = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(EVIDENCE_MODULE)
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".claude/commands/exakt.md",
    "commands/exakt.toml",
    "tests/__init__.py",
    "tests/test_package_structure.py",
)
MANIFESTS = {
    ".codex-plugin/plugin.json": "codex",
    ".claude-plugin/plugin.json": "claude",
}
CODEX_PATH_FIELDS = ("skills", "apps", "mcpServers", "hooks")
CODEX_INTERFACE_PATH_FIELDS = (
    "composerIcon",
    "logo",
    "logoDark",
    "screenshots",
)
CLAUDE_DIRECT_PATH_FIELDS = (
    "agents",
    "skills",
    "hooks",
    "outputStyles",
    "themes",
    "workflows",
    "monitors",
)
CLAUDE_SERVER_PATH_FIELDS = ("mcpServers", "lspServers")
CLAUDE_EXPERIMENTAL_PATH_FIELDS = (
    "outputStyles",
    "themes",
    "monitors",
    "evals",
)
MCP_BUNDLE_SUFFIXES = (".mcpb", ".dxt")
SIMPLE_YAML_MAPPING_LINE = re.compile(
    r"([A-Za-z][A-Za-z0-9_-]*): +(.+?) *"
)
SIMPLE_YAML_PLAIN_STRING = re.compile(
    r"[A-Za-z][A-Za-z0-9 ./_$()'+-]*"
)
AMBIGUOUS_YAML_PLAIN_SCALARS = {
    "false",
    "n",
    "no",
    "null",
    "off",
    "on",
    "true",
    "y",
    "yes",
}


def is_external_mcp_bundle_url(value):
    """Match the remote MCP bundle form accepted by the Claude host."""
    if not isinstance(value, str) or not value.startswith(
        ("http://", "https://")
    ):
        return False
    if not value.endswith(MCP_BUNDLE_SUFFIXES):
        return False
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(
        parsed.netloc and parsed.hostname
    )


def iter_direct_paths(value, *, permit_bundle_urls=False):
    """Yield path strings, ignoring inline component definitions."""
    if isinstance(value, str):
        if not (permit_bundle_urls and is_external_mcp_bundle_url(value)):
            yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield from iter_direct_paths(
                    item,
                    permit_bundle_urls=permit_bundle_urls,
                )


def iter_manifest_paths(manifest, contract):
    """Yield package paths from a current Codex or Claude manifest."""
    if contract == "codex":
        for field in CODEX_PATH_FIELDS:
            yield from iter_direct_paths(manifest.get(field))
        interface = manifest.get("interface")
        if isinstance(interface, dict):
            for field in CODEX_INTERFACE_PATH_FIELDS:
                yield from iter_direct_paths(interface.get(field))
        return

    if contract != "claude":
        raise ValueError(f"unknown plugin manifest contract: {contract}")

    commands = manifest.get("commands")
    if isinstance(commands, dict):
        for command in commands.values():
            if isinstance(command, dict):
                yield from iter_direct_paths(command.get("source"))
    else:
        yield from iter_direct_paths(commands)

    for field in CLAUDE_DIRECT_PATH_FIELDS:
        yield from iter_direct_paths(manifest.get(field))
    for field in CLAUDE_SERVER_PATH_FIELDS:
        yield from iter_direct_paths(
            manifest.get(field),
            permit_bundle_urls=(field == "mcpServers"),
        )

    experimental = manifest.get("experimental")
    if isinstance(experimental, dict):
        for field in CLAUDE_EXPERIMENTAL_PATH_FIELDS:
            yield from iter_direct_paths(experimental.get(field))


def is_safe_package_path(value):
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    posix_path = PurePosixPath(value.replace("\\", "/"))
    windows_path = PureWindowsPath(value)
    return (
        not posix_path.is_absolute()
        and not windows_path.drive
        and ".." not in posix_path.parts
        and "://" not in value
    )


def parse_simple_claude_metadata(lines):
    """Parse Exakt's deliberately small, fail-closed YAML subset.

    The wrapper needs only a flat mapping of unique keys to non-empty strings.
    This subset accepts conservative plain strings and JSON-compatible
    double-quoted strings. It rejects indentation, inline comments, implicit
    typed scalars, collections, aliases, tags, and block or multiline values
    rather than trying to emulate a general YAML parser.
    """
    metadata = {}
    for line_with_ending in lines:
        line = line_with_ending.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = SIMPLE_YAML_MAPPING_LINE.fullmatch(line)
        if match is None:
            raise ValueError(
                "Claude command frontmatter must be a flat string mapping"
            )
        key, raw_value = match.groups()
        if key in metadata:
            raise ValueError(f"duplicate Claude command frontmatter key: {key}")

        if raw_value.startswith('"'):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid quoted Claude command frontmatter value: {key}"
                ) from error
            if not isinstance(value, str):
                raise ValueError(
                    f"Claude command frontmatter value must be a string: {key}"
                )
        elif (
            SIMPLE_YAML_PLAIN_STRING.fullmatch(raw_value) is None
            or raw_value.casefold() in AMBIGUOUS_YAML_PLAIN_SCALARS
        ):
            raise ValueError(
                f"unsupported Claude command frontmatter value: {key}"
            )
        else:
            value = raw_value

        if not value.strip():
            raise ValueError(f"Claude command frontmatter value is empty: {key}")
        metadata[key] = value
    return metadata


def effective_wrapper_prompt(relative_path, content):
    """Return the text the host supplies as the wrapper prompt."""
    if relative_path == "commands/exakt.toml":
        try:
            command = tomllib.loads(content)
        except tomllib.TOMLDecodeError as error:
            raise ValueError("invalid Codex command TOML") from error
        prompt = command.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Codex command prompt must be a non-empty string")
        return prompt

    if relative_path != ".claude/commands/exakt.md":
        raise ValueError(f"unsupported command wrapper: {relative_path}")

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("Claude command must start with YAML frontmatter")
    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing_index is None:
        raise ValueError("Claude command frontmatter is not closed")

    metadata = parse_simple_claude_metadata(lines[1:closing_index])
    if not metadata.get("description"):
        raise ValueError("Claude command frontmatter requires a description")

    body = "".join(lines[closing_index + 1 :])
    if not body.strip():
        raise ValueError("Claude command body must not be empty")
    return re.sub(r"<!--.*?(?:-->|$)", "", body, flags=re.DOTALL)


class PackageStructureTests(unittest.TestCase):
    def test_required_plugin_files_exist(self):
        missing = [path for path in REQUIRED_FILES if not (PACKAGE_ROOT / path).is_file()]
        self.assertEqual([], missing, f"missing required plugin files: {missing}")

    def test_manifests_are_json_objects(self):
        for relative_path in MANIFESTS:
            with self.subTest(manifest=relative_path):
                manifest = json.loads((PACKAGE_ROOT / relative_path).read_text())
                self.assertIsInstance(manifest, dict)
                self.assertEqual("exakt", manifest.get("name"))
                self.assertIsInstance(manifest.get("author"), dict)
                self.assertTrue(manifest["author"].get("name"))

    def test_claude_manifest_path_fields_are_extracted(self):
        manifest = {
            "commands": {
                "exakt": {"source": "./commands/exakt.md"},
                "inline": {"content": "No package path here."},
            },
            "agents": ["./agents/reviewer.md"],
            "skills": "./skills/",
            "hooks": ["./hooks/hooks.json", {"SessionStart": []}],
            "mcpServers": [
                "./.mcp.json",
                "https://example.test/server.mcpb",
                "HTTPS://example.test/server.dxt",
                "ftp://example.test/server.mcpb",
                {"inline": {"command": "server"}},
            ],
            "lspServers": [
                "./.lsp.json",
                {"inline": {"command": "language-server"}},
            ],
            "outputStyles": ["./output-styles/"],
            "themes": "./themes/",
            "workflows": ["./workflows/"],
            "monitors": "./monitors/monitors.json",
            "experimental": {
                "outputStyles": "./experimental-output-styles/",
                "themes": ["./experimental-themes/"],
                "monitors": "./experimental-monitors.json",
                "evals": ["./evals/"],
            },
        }
        self.assertEqual(
            {
                "./commands/exakt.md",
                "./agents/reviewer.md",
                "./skills/",
                "./hooks/hooks.json",
                "./.mcp.json",
                "HTTPS://example.test/server.dxt",
                "ftp://example.test/server.mcpb",
                "./.lsp.json",
                "./output-styles/",
                "./themes/",
                "./workflows/",
                "./monitors/monitors.json",
                "./experimental-output-styles/",
                "./experimental-themes/",
                "./experimental-monitors.json",
                "./evals/",
            },
            set(iter_manifest_paths(manifest, "claude")),
        )

    def test_codex_manifest_path_fields_are_extracted(self):
        manifest = {
            "skills": "./skills/",
            "apps": "./.app.json",
            "mcpServers": "./.mcp.json",
            "hooks": "./hooks/hooks.json",
            "interface": {
                "composerIcon": "./assets/composer.png",
                "logo": "./assets/logo.png",
                "logoDark": "./assets/logo-dark.png",
                "screenshots": ["./assets/screenshot.png"],
            },
        }
        self.assertEqual(
            {
                "./skills/",
                "./.app.json",
                "./.mcp.json",
                "./hooks/hooks.json",
                "./assets/composer.png",
                "./assets/logo.png",
                "./assets/logo-dark.png",
                "./assets/screenshot.png",
            },
            set(iter_manifest_paths(manifest, "codex")),
        )

    def test_codex_escaping_hook_path_reaches_safety_guard(self):
        declared_paths = list(
            iter_manifest_paths({"hooks": "../outside/hooks.json"}, "codex")
        )
        self.assertEqual(["../outside/hooks.json"], declared_paths)
        self.assertFalse(is_safe_package_path(declared_paths[0]))

    def test_claude_external_mcp_bundle_urls_follow_host_contract(self):
        for valid_url in (
            "http://example.test/server.mcpb",
            "https://example.test/server.dxt",
        ):
            with self.subTest(valid_url=valid_url):
                self.assertEqual(
                    [],
                    list(
                        iter_manifest_paths(
                            {"mcpServers": valid_url},
                            "claude",
                        )
                    ),
                )

        for unsafe_or_unsupported in (
            "../escape://bundle.mcpb",
            "file:///tmp/server.mcpb",
            "ftp://example.test/server.mcpb",
            "https:///server.mcpb",
            "HTTPS://example.test/server.dxt",
            "https://example.test/server.zip",
            "https://example.test/server.mcpb?download=1",
        ):
            with self.subTest(unsafe_or_unsupported=unsafe_or_unsupported):
                declared_paths = list(
                    iter_manifest_paths(
                        {"mcpServers": unsafe_or_unsupported},
                        "claude",
                    )
                )
                self.assertEqual([unsafe_or_unsupported], declared_paths)
                self.assertFalse(is_safe_package_path(declared_paths[0]))

    def test_manifest_path_extraction_ignores_non_package_strings(self):
        manifest = {
            "author": {"logo": "/not/a/plugin/component.png"},
            "mcpServers": {
                "inline": {
                    "command": "/usr/bin/example-server",
                    "args": ["--config", "/etc/example-server.json"],
                }
            },
        }
        self.assertEqual([], list(iter_manifest_paths(manifest, "codex")))

    def test_manifest_paths_stay_inside_package(self):
        for relative_path, contract in MANIFESTS.items():
            manifest = json.loads((PACKAGE_ROOT / relative_path).read_text())
            for declared_path in iter_manifest_paths(manifest, contract):
                with self.subTest(manifest=relative_path, path=declared_path):
                    self.assertTrue(
                        is_safe_package_path(declared_path),
                        f"unsafe package path in {relative_path}: {declared_path}",
                    )

    def test_command_wrappers_route_to_exakt_skill(self):
        wrappers = {
            ".claude/commands/exakt.md": "$ARGUMENTS",
            "commands/exakt.toml": "{{args}}",
        }
        for relative_path, argument_token in wrappers.items():
            with self.subTest(wrapper=relative_path):
                wrapper = (PACKAGE_ROOT / relative_path).read_text()
                prompt = effective_wrapper_prompt(relative_path, wrapper)
                self.assertIn("skills/exakt/SKILL.md", prompt)
                self.assertIn(argument_token, prompt)

    def test_wrapper_parsers_reject_malformed_documents(self):
        malformed_wrappers = {
            "commands/exakt.toml": 'prompt = "unterminated\n',
            ".claude/commands/exakt.md": (
                "---\n"
                "description: Missing the closing delimiter\n"
                "Read skills/exakt/SKILL.md with $ARGUMENTS\n"
            ),
        }
        for relative_path, wrapper in malformed_wrappers.items():
            with self.subTest(wrapper=relative_path):
                with self.assertRaises(ValueError):
                    effective_wrapper_prompt(relative_path, wrapper)

    def test_claude_wrapper_rejects_unsupported_or_non_string_yaml(self):
        for metadata_line in (
            'description: "unterminated',
            "description: []",
            "description: {}",
            "description: true",
            "description: 42",
            "description: &description Run Exakt",
            "description: Run Exakt: now",
        ):
            with self.subTest(metadata_line=metadata_line):
                wrapper = (
                    f"---\n{metadata_line}\n---\n"
                    "Read skills/exakt/SKILL.md with $ARGUMENTS\n"
                )
                with self.assertRaises(ValueError):
                    effective_wrapper_prompt(
                        ".claude/commands/exakt.md",
                        wrapper,
                    )

    def test_claude_wrapper_accepts_supported_string_yaml_subset(self):
        wrapper = (
            "---\n"
            "# Full-line comments are part of the supported subset.\n"
            "description: Run Exakt's verification-first workflow\n"
            'argument-hint: "Task or product brief"\n'
            "---\n"
            "Read skills/exakt/SKILL.md with $ARGUMENTS\n"
        )
        prompt = effective_wrapper_prompt(
            ".claude/commands/exakt.md",
            wrapper,
        )
        self.assertEqual(
            "Read skills/exakt/SKILL.md with $ARGUMENTS\n",
            prompt,
        )

    def test_wrapper_tokens_in_metadata_or_comments_do_not_count(self):
        misleading_wrappers = {
            "commands/exakt.toml": (
                'description = "No route"\n'
                "# skills/exakt/SKILL.md {{args}}\n"
                'prompt = "Do something else"\n'
            ),
            ".claude/commands/exakt.md": (
                "---\n"
                "description: skills/exakt/SKILL.md $ARGUMENTS\n"
                "---\n"
                "<!-- skills/exakt/SKILL.md $ARGUMENTS -->\n"
                "Do something else.\n"
            ),
        }
        for relative_path, wrapper in misleading_wrappers.items():
            with self.subTest(wrapper=relative_path):
                prompt = effective_wrapper_prompt(relative_path, wrapper)
                self.assertNotIn("skills/exakt/SKILL.md", prompt)
                expected_token = (
                    "$ARGUMENTS"
                    if relative_path.endswith(".md")
                    else "{{args}}"
                )
                self.assertNotIn(expected_token, prompt)

    def test_red_evidence_git_ignores_replacement_objects(self):
        with tempfile.TemporaryDirectory(prefix="exakt-git-replace-") as temp_dir:
            repo_root = Path(temp_dir)
            subprocess.run(
                ("git", "init", "--quiet", str(repo_root)),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            trusted_blob = repo_root / "trusted.py"
            replacement_blob = repo_root / "replacement.py"
            trusted_blob.write_bytes(b"print('trusted')\n")
            replacement_blob.write_bytes(b"print('replacement')\n")
            trusted_oid = subprocess.run(
                (
                    "git",
                    "-C",
                    str(repo_root),
                    "hash-object",
                    "-w",
                    str(trusted_blob),
                ),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            replacement_oid = subprocess.run(
                (
                    "git",
                    "-C",
                    str(repo_root),
                    "hash-object",
                    "-w",
                    str(replacement_blob),
                ),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ("git", "-C", str(repo_root), "replace", trusted_oid, replacement_oid),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            substituted = subprocess.run(
                ("git", "-C", str(repo_root), "cat-file", "blob", trusted_oid),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout

            observed = EVIDENCE_MODULE.git(
                repo_root,
                "cat-file",
                "blob",
                trusted_oid,
            ).stdout

        self.assertNotEqual(trusted_oid, replacement_oid)
        self.assertEqual(b"print('replacement')\n", substituted)
        self.assertEqual(b"print('trusted')\n", observed)

    def test_path_guard_rejects_absolute_and_escaping_paths(self):
        for unsafe_path in (
            "/tmp/exakt",
            "../exakt",
            "./skills/../../exakt",
            r"C:\exakt",
            r"C:..\exakt",
            r"C:exakt",
            "C:",
            r"Z:.\exakt",
            r"..\exakt",
            r"safe\..\exakt",
            r"\\server\share\exakt",
            r"\\?\C:\exakt",
        ):
            with self.subTest(path=unsafe_path):
                self.assertFalse(is_safe_package_path(unsafe_path))

    def test_path_guard_accepts_safe_relative_package_paths(self):
        for safe_path in (
            ".",
            "./skills/",
            "commands/exakt.toml",
            r"skills\exakt\SKILL.md",
            "assets/my..icon.png",
            "themes/dark/theme.json",
        ):
            with self.subTest(path=safe_path):
                self.assertTrue(is_safe_package_path(safe_path))


if __name__ == "__main__":
    unittest.main()
