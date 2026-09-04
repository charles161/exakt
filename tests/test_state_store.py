import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PACKAGE_ROOT / "skills/forge/scripts"
STATE_STORE_PATH = SCRIPTS_ROOT / "state_store.py"
FIXTURE_ROOT = PACKAGE_ROOT / "tests/fixtures/state-store"


def load_state_store_module():
    if not STATE_STORE_PATH.is_file():
        raise AssertionError(f"missing state-store module: {STATE_STORE_PATH}")
    spec = importlib.util.spec_from_file_location("forge_state_store", STATE_STORE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import state-store module: {STATE_STORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StateStoreTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_state_store_module()


class ImportIsolationTests(unittest.TestCase):
    def test_file_import_never_executes_cwd_contracts_module(self):
        with tempfile.TemporaryDirectory(prefix="forge-import-isolation-") as temp_dir:
            cwd = Path(temp_dir)
            sentinel = cwd / "executed"
            (cwd / "contracts.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('executed')\n"
                "raise RuntimeError('repository-controlled module executed')\n"
            )
            program = (
                "import importlib.util, pathlib, sys\n"
                "path = pathlib.Path(sys.argv[1])\n"
                "spec = importlib.util.spec_from_file_location('isolated_state_store', path)\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "sys.modules[spec.name] = module\n"
                "spec.loader.exec_module(module)\n"
                "print(module.CANONICAL_JSON_VERSION)\n"
            )
            result = subprocess.run(
                (sys.executable, "-c", program, str(STATE_STORE_PATH)),
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("forge-canonical-json-v1", result.stdout.strip())
            self.assertFalse(sentinel.exists())


class CanonicalJsonTests(StateStoreTestCase):
    def test_exact_golden_bytes_record_framing_and_digest(self):
        value = {"b": 2, "a": 1}
        canonical = self.store.canonical_json_bytes(value)
        self.assertEqual(b'{"a":1,"b":2}', canonical)
        self.assertEqual(canonical + b"\n", self.store.canonical_json_record(value))
        self.assertEqual(
            "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
            self.store.canonical_sha256(value),
        )

    def test_key_order_is_unicode_code_point_not_utf16(self):
        value = {"\U00010000": 2, "\ue000": 1}
        self.assertEqual(
            b'{"\xee\x80\x80":1,"\xf0\x90\x80\x80":2}',
            self.store.canonical_json_bytes(value),
        )

    def test_fixed_string_escaping_and_raw_utf8(self):
        value = {"text": 'quote=" slash=/ backslash=\\ line=\n caf\u00e9'}
        self.assertEqual(
            b'{"text":"quote=\\\" slash=/ backslash=\\\\ line=\\n caf\xc3\xa9"}',
            self.store.canonical_json_bytes(value),
        )

    def test_unicode_is_preserved_without_normalization(self):
        composed = self.store.canonical_json_bytes({"v": "\u00e9"})
        decomposed = self.store.canonical_json_bytes({"v": "e\u0301"})
        self.assertNotEqual(composed, decomposed)
        self.assertNotEqual(
            self.store.sha256_hex(composed), self.store.sha256_hex(decomposed)
        )

    def test_parsing_rejects_duplicate_keys_bom_and_noncanonical_bytes(self):
        invalid = (
            b'{"a":1,"a":2}',
            b'{"a":1,"\\u0061":2}',
            b'{"outer":{"a":1,"a":2}}',
            b'\xef\xbb\xbf{"a":1}',
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(self.store.CanonicalStateError):
                    self.store.parse_json_bytes(payload)

        with self.assertRaisesRegex(
            self.store.CanonicalStateError, "not Forge Canonical JSON v1"
        ):
            self.store.parse_json_bytes(b'{"b":2, "a":1}', require_canonical=True)
        with self.assertRaisesRegex(
            self.store.CanonicalStateError, "not Forge Canonical JSON v1"
        ):
            self.store.parse_json_bytes(
                b'{"v":"\\u00e9"}', require_canonical=True
            )

    def test_numbers_are_integer_only_across_parsed_and_in_memory_state(self):
        for value in (1.0, -0.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(self.store.CanonicalStateError):
                    self.store.canonical_json_bytes({"value": value})

        for payload in (b'{"v":1.0}', b'{"v":-0.0}', b'{"v":1e3}', b'{"v":NaN}'):
            with self.subTest(payload=payload):
                with self.assertRaises(self.store.CanonicalStateError):
                    self.store.parse_json_bytes(payload)

        self.assertEqual(
            b'{"decimal":"1.25","integer":1}',
            self.store.canonical_json_bytes(
                {"integer": 1, "decimal": "1.25"}
            ),
        )

    def test_python_only_values_cycles_non_string_keys_and_surrogates_fail_closed(self):
        cycle = []
        cycle.append(cycle)
        invalid = (
            {1: "value"},
            {"tuple": (1, 2)},
            {"bytes": b"value"},
            {"cycle": cycle},
            {"surrogate": "\ud800"},
            {"\ud800": "key"},
        )
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(self.store.CanonicalStateError):
                    self.store.canonical_json_bytes(value)

    def test_parser_and_encoder_keep_integer_resource_boundary_deterministic(self):
        previous_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(640)
            accepted = b"1" * self.store.MAX_INTEGER_DIGITS
            value = self.store.parse_json_bytes(accepted)
            self.assertEqual(self.store.MAX_INTEGER_DIGITS, value.bit_length() * 0 + len(accepted))
            self.assertEqual(accepted, self.store.canonical_json_bytes(value))
            with self.assertRaisesRegex(
                self.store.CanonicalStateError, "integer exceeds"
            ):
                self.store.parse_json_bytes(accepted + b"1")
        finally:
            sys.set_int_max_str_digits(previous_limit)


class StateHomeResolutionTests(StateStoreTestCase):
    def test_resolution_precedence_is_pure_and_platform_explicit(self):
        with tempfile.TemporaryDirectory(prefix="forge-state-resolution-") as temp_dir:
            base = Path(temp_dir)
            explicit = base / "explicit"
            xdg = base / "xdg"
            home = base / "home"
            local = base / "local"
            env = {
                "FORGE_STATE_HOME": str(explicit),
                "XDG_STATE_HOME": str(xdg),
                "LOCALAPPDATA": str(local),
            }
            self.assertEqual(
                explicit,
                self.store.resolve_state_home(env, platform_name="linux", home=home),
            )
            self.assertFalse(explicit.exists())

            env.pop("FORGE_STATE_HOME")
            self.assertEqual(
                xdg / "forge",
                self.store.resolve_state_home(env, platform_name="darwin", home=home),
            )
            env.pop("XDG_STATE_HOME")
            self.assertEqual(
                home / "Library/Application Support/Forge",
                self.store.resolve_state_home(env, platform_name="darwin", home=home),
            )
            self.assertEqual(
                local / "Forge",
                self.store.resolve_state_home(env, platform_name="win32", home=home),
            )
            self.assertEqual(
                home / ".local/state/forge",
                self.store.resolve_state_home({}, platform_name="linux", home=home),
            )

    def test_empty_relative_or_missing_required_roots_fail_closed(self):
        for env in (
            {"FORGE_STATE_HOME": ""},
            {"FORGE_STATE_HOME": "relative/path"},
            {"FORGE_STATE_HOME": "/tmp/bad\0path"},
            {"FORGE_STATE_HOME": "/tmp/bad\ud800path"},
            {"XDG_STATE_HOME": "relative/path"},
        ):
            with self.subTest(env=env):
                with self.assertRaises(self.store.StateHomeError):
                    self.store.resolve_state_home(
                        env, platform_name="linux", home=Path("/safe/home")
                    )
        with self.assertRaises(self.store.StateHomeError):
            self.store.resolve_state_home(
                {}, platform_name="win32", home=Path("C:/Users/test")
            )

        for invalid_path in ("/tmp/bad\0path", "/tmp/bad\ud800path"):
            with self.subTest(invalid_path=invalid_path):
                with self.assertRaises(self.store.StateHomeError):
                    self.store.assert_safe_state_home_path(invalid_path, [])
                with self.assertRaises(self.store.StateHomeError):
                    self.store.resolve_state_home(
                        {}, platform_name="linux", home=invalid_path
                    )
                with self.assertRaises(self.store.StateHomeError):
                    self.store.resolve_state_home(
                        {"FORGE_STATE_HOME": "~/state"},
                        platform_name="linux",
                        home=invalid_path,
                    )

        with mock.patch.object(
            self.store.Path, "home", side_effect=RuntimeError("home unavailable")
        ):
            with self.assertRaises(self.store.StateHomeError):
                self.store.resolve_state_home({}, platform_name="linux")

    def test_public_path_and_environment_type_errors_are_controlled(self):
        scope = "scope-" + ("a" * 64)
        work = "work-" + ("a" * 32)
        calls = (
            lambda: self.store.paths_overlap(1, "/tmp"),
            lambda: self.store.assert_safe_state_home_path(1, []),
            lambda: self.store.assert_safe_state_home_path("/tmp/state", None),
            lambda: self.store.resolve_state_home({}, home=[]),
            lambda: self.store.resolve_state_home({}, platform_name=[]),
            lambda: self.store.RepositoryRegistry("/tmp/forge-does-not-exist"),
            lambda: self.store.work_item_state_path(None, scope, work),
            lambda: self.store.scrub_state_environment(None),
            lambda: self.store.scrub_state_environment({1: "value"}),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(self.store.StateStoreError):
                    call()


class StateHomeSafetyTests(StateStoreTestCase):
    def test_overlap_policy_catches_ancestry_aliases_and_not_prefix_siblings(self):
        with tempfile.TemporaryDirectory(prefix="forge-overlap-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            for unsafe in (target, target / "state", base):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(self.store.StateHomeError):
                        self.store.assert_safe_state_home_path(unsafe, [target])
            sibling = base / "repository-state"
            self.assertEqual(
                sibling.resolve(),
                self.store.assert_safe_state_home_path(sibling, [target]),
            )

    def test_case_policy_supports_posix_and_windows_components(self):
        self.assertTrue(
            self.store.paths_overlap(
                "/Repo", "/repo/state", flavor="posix", case_sensitive=False
            )
        )
        self.assertFalse(
            self.store.paths_overlap(
                "/Repo", "/repo/state", flavor="posix", case_sensitive=True
            )
        )
        self.assertTrue(
            self.store.paths_overlap(
                r"C:\Repo", r"c:\repo\state", flavor="windows", case_sensitive=False
            )
        )
        self.assertFalse(
            self.store.paths_overlap(
                r"C:\Repo", r"D:\Repo", flavor="windows", case_sensitive=False
            )
        )
        for invalid_policy in ("false", 0, 1, [], object()):
            with self.subTest(invalid_policy=invalid_policy):
                with self.assertRaises(self.store.StateHomeError):
                    self.store.paths_overlap(
                        "/Repo",
                        "/repo/state",
                        flavor="posix",
                        case_sensitive=invalid_policy,
                    )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_target_owned_links_cannot_reach_state_home(self):
        with tempfile.TemporaryDirectory(prefix="forge-link-safety-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            state_home = base / "private-state"
            state_home.mkdir(mode=0o700)
            (target / "state-alias").symlink_to(state_home, target_is_directory=True)
            with self.assertRaisesRegex(self.store.StateHomeError, "target-owned symlink"):
                self.store.assert_safe_state_home_path(state_home, [target])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_broken_target_owned_links_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="forge-broken-link-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            (target / "broken").symlink_to(base / "missing")
            with self.assertRaisesRegex(self.store.StateHomeError, "broken symlink"):
                self.store.assert_safe_state_home_path(base / "state", [target])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_target_link_scan_streams_entries_and_enforces_bound(self):
        with tempfile.TemporaryDirectory(prefix="forge-streamed-links-") as temp_dir:
            root = Path(temp_dir)
            destination = root / "destination"
            destination.mkdir()
            target = root / "target"
            target.mkdir()
            (target / "a").write_text("a")
            (target / "b").write_text("b")
            link = target / "link"
            link.symlink_to(destination, target_is_directory=True)
            with mock.patch.object(
                self.store.os,
                "walk",
                side_effect=AssertionError("bounded scanner must not use eager os.walk"),
            ):
                self.assertEqual([link], list(self.store._iter_target_links(target)))
            with self.assertRaisesRegex(self.store.StateHomeError, "exceeds"):
                list(self.store._iter_target_links(target, max_entries=2))

    @unittest.skipUnless(os.name == "posix", "POSIX ownership/mode test")
    def test_private_directory_creation_and_existing_unsafe_mode(self):
        with tempfile.TemporaryDirectory(prefix="forge-permissions-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            state_home = base / "state"
            old_umask = os.umask(0)
            try:
                prepared = self.store.prepare_state_home(state_home, [target])
            finally:
                os.umask(old_umask)
            self.assertEqual(state_home.resolve(), prepared.path)
            self.assertEqual(0o700, stat.S_IMODE(state_home.stat().st_mode))
            self.assertTrue(prepared.capabilities.trusted)

            unsafe = base / "unsafe"
            unsafe.mkdir(mode=0o755)
            unsafe.chmod(0o755)
            with self.assertRaisesRegex(self.store.StateHomeError, "private"):
                self.store.prepare_state_home(unsafe, [target])
            self.assertEqual(0o755, stat.S_IMODE(unsafe.stat().st_mode))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_state_home_file_and_broken_symlink_are_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory(prefix="forge-state-node-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            state_file = base / "state-file"
            state_file.write_text("preserve me")
            with self.assertRaises(self.store.StateHomeError):
                self.store.prepare_state_home(state_file, [target])
            self.assertEqual("preserve me", state_file.read_text())

            broken = base / "state-link"
            broken.symlink_to(base / "missing", target_is_directory=True)
            with self.assertRaisesRegex(self.store.StateHomeError, "broken symlink"):
                self.store.prepare_state_home(broken, [target])
            self.assertTrue(broken.is_symlink())

    def test_failed_capability_gate_refuses_trusted_state(self):
        with tempfile.TemporaryDirectory(prefix="forge-capability-") as temp_dir:
            base = Path(temp_dir)
            target = base / "repo"
            target.mkdir()
            state_home = base / "state"
            names = (
                "private_permissions",
                "symlink_safe_creation",
                "exclusive_locking",
                "atomic_replace",
                "file_sync",
                "directory_sync",
                "compare_and_swap",
            )
            for failed_name in names:
                with self.subTest(failed_name=failed_name):
                    values = {name: True for name in names}
                    values[failed_name] = False
                    failed = self.store.FilesystemCapabilities(**values)
                    with self.assertRaisesRegex(
                        self.store.UnsafeFilesystemError, failed_name
                    ):
                        self.store.prepare_state_home(
                            state_home,
                            [target],
                            capability_probe=lambda _path, result=failed: result,
                        )
                    self.assertEqual([], list(state_home.iterdir()))

    @unittest.skipUnless(os.name == "posix", "current probe supports POSIX")
    def test_real_probe_exercises_actual_state_filesystem_and_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix="forge-real-probe-") as temp_dir:
            state_home = Path(temp_dir) / "state"
            state_home.mkdir(mode=0o700)
            capabilities = self.store.probe_state_home_filesystem(state_home)
            self.assertTrue(capabilities.trusted)
            self.assertEqual([], list(state_home.iterdir()))

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux fd accounting required")
    def test_lock_probe_closes_descriptor_when_setup_fails(self):
        with tempfile.TemporaryDirectory(prefix="forge-lock-probe-fd-") as temp_dir:
            lock_path = Path(temp_dir) / "lock"
            before = len(list(Path("/proc/self/fd").iterdir()))
            with mock.patch.object(
                self.store.os, "fchmod", side_effect=OSError("simulated failure")
            ):
                for _ in range(16):
                    self.assertFalse(self.store._probe_cross_process_lock(lock_path))
            after = len(list(Path("/proc/self/fd").iterdir()))
            self.assertEqual(before, after)

    @unittest.skipUnless(os.name == "posix", "POSIX lock publication required")
    def test_failed_published_lock_is_preserved_to_prevent_split_lock_inodes(self):
        with tempfile.TemporaryDirectory(prefix="forge-published-lock-") as temp_dir:
            root = Path(temp_dir)
            root.chmod(0o700)
            lock_path = root / ".lock"
            with mock.patch.object(
                self.store.os, "fchmod", side_effect=OSError("transient setup failure")
            ):
                with self.assertRaises(OSError):
                    self.store._exclusive_open(lock_path)
            self.assertTrue(lock_path.is_file())
            with self.assertRaises(FileExistsError):
                self.store._exclusive_open(lock_path)

    @unittest.skipUnless(os.name == "posix", "CAS adapter is POSIX-only")
    def test_expected_digest_compare_and_swap_rejects_stale_writer(self):
        with tempfile.TemporaryDirectory(prefix="forge-cas-") as temp_dir:
            root = Path(temp_dir)
            root.chmod(0o700)
            head = root / "head"
            head.write_bytes(b"root-a")
            head.chmod(0o600)
            expected = self.store.sha256_hex(b"root-a")
            self.assertTrue(
                self.store.compare_and_swap_file(head, expected, b"root-b")
            )
            self.assertFalse(
                self.store.compare_and_swap_file(head, expected, b"root-c")
            )
            self.assertEqual(b"root-b", head.read_bytes())

            for invalid_name in ("bad\0path", "bad\ud800path"):
                with self.subTest(invalid_target=invalid_name):
                    with self.assertRaises(self.store.StateStoreError):
                        self.store.compare_and_swap_file(
                            root / invalid_name,
                            self.store.sha256_hex(b"missing"),
                            b"replacement",
                        )
                with self.subTest(invalid_lock=invalid_name):
                    with self.assertRaises(self.store.StateStoreError):
                        self.store.compare_and_swap_file(
                            head,
                            self.store.sha256_hex(b"root-b"),
                            b"replacement",
                            lock_path=root / invalid_name,
                        )

            with mock.patch.object(self.store, "MAX_PRIVATE_FILE_BYTES", 4):
                with self.assertRaises(self.store.UnsafeFilesystemError):
                    self.store.compare_and_swap_file(
                        head,
                        self.store.sha256_hex(b"root-b"),
                        b"12345",
                    )
            self.assertEqual(b"root-b", head.read_bytes())
            with self.assertRaises(self.store.UnsafeFilesystemError):
                self.store.compare_and_swap_file(
                    head,
                    self.store.sha256_hex(b"root-b"),
                    b"root-c",
                    lock_path=head,
                )
            self.assertEqual(b"root-b", head.read_bytes())

    def test_probe_name_collision_preserves_preexisting_artifact(self):
        with tempfile.TemporaryDirectory(prefix="forge-probe-collision-") as temp_dir:
            state_home = Path(temp_dir) / "state"
            state_home.mkdir(mode=0o700)
            collision = state_home / ".forge-capability-fixed"
            collision.mkdir(mode=0o700)
            marker = collision / "marker"
            marker.write_text("preserve")
            with mock.patch.object(self.store.secrets, "token_hex", return_value="fixed"):
                capabilities = self.store.probe_state_home_filesystem(state_home)
            self.assertFalse(capabilities.trusted)
            self.assertEqual("preserve", marker.read_text())


class RepositoryIdentityTests(StateStoreTestCase):
    def test_remote_sanitization_strips_secrets_before_serialization(self):
        cases = json.loads((FIXTURE_ROOT / "remote-cases.json").read_text())
        for case in cases:
            with self.subTest(raw=case["raw"]):
                self.assertEqual(
                    case["sanitized"], self.store.sanitize_remote_url(case["raw"])
                )
        sanitized = self.store.sanitize_remote_urls(
            [case["raw"] for case in reversed(cases)] + [cases[0]["raw"]]
        )
        serialized = json.dumps(sanitized)
        self.assertEqual(tuple(sorted(set(sanitized))), sanitized)
        for secret in ("alice", "s3cr3t", "password", "SECRET", "token", "fragment"):
            self.assertNotIn(secret, serialized)

    def test_malformed_or_private_local_remotes_fail_closed(self):
        for remote in (
            "file:///home/private/repo",
            "/home/private/repo",
            "https://example.com:bad/repo",
            "https://example.com/repo\nAuthorization: secret",
            "https://exa%mple.com/repo",
            "git@example.com:org/repo.git?token=SECRET",
            "-:repo.git",
            r"C:\Users\secret\repo",
            r"D:\work\repo.git",
            r"\\server\share\repo.git",
            "//server/share/repo.git",
            r"git@example.com:org\private\repo.git",
            "git@example.com:org/\ud800.git",
            "not a remote",
        ):
            with self.subTest(remote=remote):
                with self.assertRaises(self.store.RepositoryIdentityError):
                    self.store.sanitize_remote_url(remote)

    def test_malformed_remote_errors_never_echo_credentials(self):
        remote = "https://alice:SUPERSECRET@exa／mple.com/repo?token=QUERYSECRET"
        with self.assertRaises(self.store.RepositoryIdentityError) as caught:
            self.store.sanitize_remote_url(remote)
        rendered = str(caught.exception)
        for secret in ("alice", "SUPERSECRET", "QUERYSECRET"):
            self.assertNotIn(secret, rendered)
        self.assertIsNone(caught.exception.__cause__)

    def test_ids_are_random_128_bit_path_safe_values(self):
        values = iter((bytes(range(16)), bytes(reversed(range(16)))))
        first = self.store.new_repository_id(random_bytes=lambda count: next(values))
        second = self.store.new_work_item_id(random_bytes=lambda count: next(values))
        self.assertEqual("repo-000102030405060708090a0b0c0d0e0f", first)
        self.assertEqual("work-0f0e0d0c0b0a09080706050403020100", second)
        self.assertRegex(self.store.new_repository_id(), r"^repo-[0-9a-f]{32}$")
        self.assertRegex(self.store.new_work_item_id(), r"^work-[0-9a-f]{32}$")
        self.assertNotEqual(self.store.new_work_item_id(), self.store.new_work_item_id())

    def test_unique_work_item_allocation_retries_collision_without_title_input(self):
        existing = {"work-" + ("00" * 16)}
        values = iter((b"\x00" * 16, b"\x01" * 16))
        identifier = self.store.allocate_unique_work_item_id(
            existing, random_bytes=lambda count: next(values)
        )
        self.assertEqual("work-" + ("01" * 16), identifier)

        for malformed in ([[]], None):
            with self.subTest(malformed=malformed):
                with self.assertRaises(self.store.StateStoreError):
                    self.store.allocate_unique_work_item_id(malformed)

    def test_scope_ids_are_domain_separated_and_target_order_independent(self):
        a = "repo-000102030405060708090a0b0c0d0e0f"
        b = "repo-0f0e0d0c0b0a09080706050403020100"
        self.assertEqual(
            self.store.scope_id_for_repositories([a, b]),
            self.store.scope_id_for_repositories([b, a]),
        )
        self.assertNotEqual(
            self.store.scope_id_for_repositories([a]),
            self.store.scope_id_for_repositories([a, b]),
        )
        for malformed in ([[]], None):
            with self.subTest(malformed=malformed):
                with self.assertRaises(self.store.RepositoryIdentityError):
                    self.store.scope_id_for_repositories(malformed)
        self.assertRegex(
            self.store.scope_id_for_repositories([a]), r"^scope-[0-9a-f]{64}$"
        )
        self.assertEqual(
            "scope-6144241a8cea76abaed3a8e8c6f5a8e4d6bfe0fa613d8bdae1cf1c868cb270de",
            self.store.scope_id_for_repositories([a]),
        )
        self.assertEqual(
            "scope-501efb3176635ddaa6d796bbbe1a2d5a896d53ff3f376d97fab248f140144550",
            self.store.scope_id_for_repositories([a, b]),
        )

    def test_case_colliding_entries_fail_only_under_insensitive_policy(self):
        paths = ["README", "src/App.tsx", "src/app.tsx"]
        with self.assertRaisesRegex(self.store.RepositoryIdentityError, "case-colliding"):
            self.store.assert_no_case_collisions(paths, case_sensitive=False)
        self.store.assert_no_case_collisions(paths, case_sensitive=True)

    def test_repository_paths_are_normalized_root_relative_posix_on_every_filesystem(self):
        invalid_paths = (
            "/absolute",
            "../escape",
            "dir/../escape",
            "./relative",
            "dir//file",
            "dir\\file",
            "",
            "bad\ud800",
        )
        for case_sensitive in (True, False):
            for path in invalid_paths:
                with self.subTest(case_sensitive=case_sensitive, path=path):
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        self.store.assert_no_case_collisions(
                            [path], case_sensitive=case_sensitive
                        )

    def test_tree_anchor_format_depends_on_vcs_kind(self):
        with tempfile.TemporaryDirectory(prefix="forge-tree-anchor-") as temp_dir:
            root = Path(temp_dir)
            for vcs_kind, digest in (
                ("git", "a" * 40),
                ("git", "a" * 64),
                ("none", "a" * 64),
            ):
                with self.subTest(valid=(vcs_kind, len(digest))):
                    record = self.store.make_repository_record(
                        root,
                        vcs_kind=vcs_kind,
                        remote_urls=[],
                        initial_tree_digest=digest,
                    )
                    self.assertEqual(digest, record["initial_tree_digest"])

            for vcs_kind, digest in (
                ("none", "a" * 40),
                ("archive", "a" * 40),
                ("git", "a" * 41),
                ("git", "A" * 40),
            ):
                with self.subTest(invalid=(vcs_kind, len(digest))):
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        self.store.make_repository_record(
                            root,
                            vcs_kind=vcs_kind,
                            remote_urls=[],
                            initial_tree_digest=digest,
                        )

    def test_repository_record_resolves_root_and_contains_only_sanitized_anchors(self):
        with tempfile.TemporaryDirectory(prefix="forge-repository-record-") as temp_dir:
            base = Path(temp_dir)
            root = base / "repository"
            root.mkdir()
            alias = base / "alias"
            alias.symlink_to(root, target_is_directory=True)
            raw_remote = "https://user:password@example.com/org/repo.git?token=SECRET"
            record = self.store.make_repository_record(
                alias,
                vcs_kind="git",
                remote_urls=[raw_remote],
                initial_tree_digest="a" * 64,
                repository_id="repo-000102030405060708090a0b0c0d0e0f",
            )
            self.assertEqual(root.resolve().as_posix(), record["resolved_root"])
            self.assertEqual(["https://example.com/org/repo.git"], record["remote_urls"])
            encoded = self.store.canonical_json_bytes(record).decode()
            for secret in ("user", "password", "token", "SECRET"):
                self.assertNotIn(secret, encoded)
            self.assertEqual({"device", "inode"}, set(record["filesystem_identity"]))

    def test_explicit_repository_id_is_never_silently_replaced(self):
        with tempfile.TemporaryDirectory(prefix="forge-explicit-repo-id-") as temp_dir:
            for repository_id in ("", 0, False, b""):
                with self.subTest(repository_id=repository_id):
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        self.store.make_repository_record(
                            temp_dir,
                            vcs_kind="git",
                            remote_urls=[],
                            initial_tree_digest="a" * 40,
                            repository_id=repository_id,
                            random_bytes=lambda count: self.fail(
                                "must not mint over an explicit malformed ID"
                            ),
                        )

    def test_missing_or_non_directory_repository_root_is_a_controlled_error(self):
        with tempfile.TemporaryDirectory(prefix="forge-bad-root-") as temp_dir:
            base = Path(temp_dir)
            file_root = base / "file"
            file_root.write_text("not a repository")
            for root in (base / "missing", file_root):
                with self.subTest(root=root):
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        self.store.make_repository_record(
                            root,
                            vcs_kind="git",
                            remote_urls=[],
                            initial_tree_digest="a" * 64,
                        )

    def test_registry_reuses_exact_root_but_not_same_remote_clone(self):
        with tempfile.TemporaryDirectory(prefix="forge-registry-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)
            first_root = base / "clone-a"
            second_root = base / "clone-b"
            first_root.mkdir()
            second_root.mkdir()
            random_values = iter((b"a" * 16, b"b" * 16))
            registry = self.store.RepositoryRegistry(state_home)
            first = registry.get_or_create(
                first_root,
                vcs_kind="git",
                remote_urls=["https://example.com/org/repo.git"],
                initial_tree_digest="c" * 64,
                random_bytes=lambda count: next(random_values),
            )
            alias = first_root / ".." / first_root.name
            same = registry.get_or_create(
                alias,
                vcs_kind="git",
                remote_urls=["https://example.com/org/repo.git"],
                initial_tree_digest="c" * 64,
                random_bytes=lambda count: self.fail("same root must reuse identity"),
            )
            clone = registry.get_or_create(
                second_root,
                vcs_kind="git",
                remote_urls=["https://example.com/org/repo.git"],
                initial_tree_digest="c" * 64,
                random_bytes=lambda count: next(random_values),
            )
            self.assertEqual(first["repository_id"], same["repository_id"])
            self.assertNotEqual(first["repository_id"], clone["repository_id"])
            registry_path = state_home / "repositories-v1.json"
            self.assertEqual(0o600, stat.S_IMODE(registry_path.stat().st_mode))
            serialized = registry_path.read_text()
            self.assertNotIn("password", serialized)

    def test_moved_root_requires_relocation_instead_of_minting_a_new_identity(self):
        with tempfile.TemporaryDirectory(prefix="forge-moved-root-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)
            original = base / "original"
            moved = base / "moved"
            original.mkdir()
            registry = self.store.RepositoryRegistry(state_home)
            registry.get_or_create(
                original,
                vcs_kind="git",
                remote_urls=["https://example.com/org/repo.git"],
                initial_tree_digest="d" * 64,
                random_bytes=lambda count: b"a" * 16,
            )
            original.rename(moved)
            before = (state_home / "repositories-v1.json").read_bytes()
            with self.assertRaises(self.store.RepositoryRelocationRequired):
                registry.get_or_create(
                    moved,
                    vcs_kind="git",
                    remote_urls=["https://example.com/org/repo.git"],
                    initial_tree_digest="d" * 64,
                    random_bytes=lambda count: self.fail("must not mint on relocation"),
                )
            self.assertEqual(before, (state_home / "repositories-v1.json").read_bytes())

    def test_malformed_registry_blocks_identity_creation_without_rewrite(self):
        with tempfile.TemporaryDirectory(prefix="forge-bad-registry-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)
            root = base / "repo"
            root.mkdir()
            registry_path = state_home / "repositories-v1.json"
            malformed = (
                b'{"schema_version":"repository-registry-v1",'
                b'"repositories":[],"repositories":[]}'
            )
            registry_path.write_bytes(malformed)
            registry_path.chmod(0o600)
            registry = self.store.RepositoryRegistry(state_home)
            with self.assertRaises(self.store.CanonicalStateError):
                registry.get_or_create(
                    root,
                    vcs_kind="git",
                    remote_urls=[],
                    initial_tree_digest="e" * 64,
                )
            self.assertEqual(malformed, registry_path.read_bytes())

    def test_malformed_registry_field_types_fail_closed_without_raw_exception(self):
        malformed_records = (
            {
                "repository_id": 7,
                "resolved_root": "/tmp/repo",
                "vcs_kind": "git",
                "remote_urls": [],
                "filesystem_identity": {"device": 1, "inode": 2},
                "initial_tree_digest": "a" * 64,
            },
            {
                "repository_id": "repo-" + ("a" * 32),
                "resolved_root": "/tmp/repo",
                "vcs_kind": "git",
                "remote_urls": ["https://user:secret@example.com/org/repo.git"],
                "filesystem_identity": {"device": True, "inode": 2},
                "initial_tree_digest": "a" * 64,
            },
        )
        for record in malformed_records:
            with self.subTest(record=record):
                with tempfile.TemporaryDirectory(prefix="forge-bad-record-") as temp_dir:
                    base = Path(temp_dir)
                    state_home = base / "state"
                    state_home.mkdir(mode=0o700)
                    root = base / "repo"
                    root.mkdir()
                    registry_path = state_home / "repositories-v1.json"
                    registry_path.write_bytes(
                        self.store.canonical_json_bytes(
                            {
                                "schema_version": "repository-registry-v1",
                                "repositories": [record],
                            }
                        )
                    )
                    registry_path.chmod(0o600)
                    before = registry_path.read_bytes()
                    registry = self.store.RepositoryRegistry(state_home)
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        registry.get_or_create(
                            root,
                            vcs_kind="git",
                            remote_urls=[],
                            initial_tree_digest="f" * 64,
                        )
                    self.assertEqual(before, registry_path.read_bytes())

    def test_registry_rejects_duplicate_filesystem_identity_and_reads_windows_roots(self):
        base_record = {
            "repository_id": "repo-" + ("a" * 32),
            "resolved_root": "C:/Repo",
            "vcs_kind": "git",
            "remote_urls": ["https://example.com/org/repo.git"],
            "filesystem_identity": {"device": 1, "inode": 2},
            "initial_tree_digest": "a" * 64,
        }
        with tempfile.TemporaryDirectory(prefix="forge-registry-platform-") as temp_dir:
            state_home = Path(temp_dir)
            state_home.chmod(0o700)
            path = state_home / "repositories-v1.json"
            path.write_bytes(
                self.store.canonical_json_bytes(
                    {
                        "schema_version": "repository-registry-v1",
                        "repositories": [base_record],
                    }
                )
            )
            path.chmod(0o600)
            registry = self.store.RepositoryRegistry(state_home)
            self.assertEqual([base_record], registry._load()["repositories"])

            duplicate = dict(base_record)
            duplicate["repository_id"] = "repo-" + ("b" * 32)
            duplicate["resolved_root"] = "D:/MovedRepo"
            path.write_bytes(
                self.store.canonical_json_bytes(
                    {
                        "schema_version": "repository-registry-v1",
                        "repositories": [base_record, duplicate],
                    }
                )
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(
                self.store.RepositoryIdentityError, "filesystem identity"
            ):
                registry._load()

    def test_registry_rejects_noncanonical_or_private_path_roots(self):
        base_record = {
            "repository_id": "repo-" + ("a" * 32),
            "resolved_root": "/tmp/repo",
            "vcs_kind": "git",
            "remote_urls": [],
            "filesystem_identity": {"device": 1, "inode": 2},
            "initial_tree_digest": "a" * 40,
        }
        invalid_roots = (
            "/tmp/a/../repo",
            "/tmp//repo",
            "/tmp/./repo",
            "/tmp/repo/",
            "C:\\Repo",
            "C:/Repo/../Other",
            "/tmp/bad\0root",
        )
        for invalid_root in invalid_roots:
            with self.subTest(invalid_root=invalid_root):
                with tempfile.TemporaryDirectory(prefix="forge-root-syntax-") as temp_dir:
                    state_home = Path(temp_dir)
                    state_home.chmod(0o700)
                    record = dict(base_record)
                    record["resolved_root"] = invalid_root
                    path = state_home / "repositories-v1.json"
                    path.write_bytes(
                        self.store.canonical_json_bytes(
                            {
                                "schema_version": "repository-registry-v1",
                                "repositories": [record],
                            }
                        )
                    )
                    path.chmod(0o600)
                    with self.assertRaises(self.store.RepositoryIdentityError):
                        self.store.RepositoryRegistry(state_home)._load()

    @unittest.skipUnless(os.name == "posix", "POSIX mode test")
    def test_registry_rejects_unsafe_existing_file_modes_without_chmod(self):
        for attacked_name, content in (
            (
                "repositories-v1.json",
                b'{"repositories":[],"schema_version":"repository-registry-v1"}',
            ),
            (".repositories-v1.lock", b"lock"),
        ):
            with self.subTest(attacked_name=attacked_name):
                with tempfile.TemporaryDirectory(prefix="forge-registry-mode-") as temp_dir:
                    base = Path(temp_dir)
                    state_home = base / "state"
                    state_home.mkdir(mode=0o700)
                    root = base / "repo"
                    root.mkdir()
                    attacked = state_home / attacked_name
                    attacked.write_bytes(content)
                    attacked.chmod(0o644)
                    registry = self.store.RepositoryRegistry(state_home)
                    with self.assertRaises(self.store.StateStoreError):
                        registry.get_or_create(
                            root,
                            vcs_kind="git",
                            remote_urls=[],
                            initial_tree_digest="f" * 64,
                        )
                    self.assertEqual(0o644, stat.S_IMODE(attacked.stat().st_mode))
                    self.assertEqual(content, attacked.read_bytes())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux ACL adapter required")
    def test_registry_rejects_extended_acl_on_existing_private_file(self):
        with tempfile.TemporaryDirectory(prefix="forge-registry-acl-") as temp_dir:
            state_home = Path(temp_dir)
            state_home.chmod(0o700)
            registry_path = state_home / "repositories-v1.json"
            registry_path.write_bytes(
                b'{"repositories":[],"schema_version":"repository-registry-v1"}'
            )
            registry_path.chmod(0o600)

            def listxattr(target, *args, **kwargs):
                return ["system.posix_acl_access"] if isinstance(target, int) else []

            with mock.patch.object(
                self.store.os, "listxattr", side_effect=listxattr
            ):
                registry = self.store.RepositoryRegistry(state_home)
                with self.assertRaises(self.store.UnsafeFilesystemError):
                    registry._load()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux ACL adapter required")
    def test_new_lock_and_temp_files_fail_closed_on_inherited_extended_acl(self):
        for failing_private_file in (1, 2):
            with self.subTest(failing_private_file=failing_private_file):
                with tempfile.TemporaryDirectory(prefix="forge-new-file-acl-") as temp_dir:
                    base = Path(temp_dir)
                    state_home = base / "state"
                    state_home.mkdir(mode=0o700)
                    root = base / "repo"
                    root.mkdir()
                    descriptor_checks = 0

                    def listxattr(target, *args, **kwargs):
                        nonlocal descriptor_checks
                        if not isinstance(target, int):
                            return []
                        descriptor_checks += 1
                        if descriptor_checks == failing_private_file:
                            return ["system.posix_acl_access"]
                        return []

                    with mock.patch.object(
                        self.store.os, "listxattr", side_effect=listxattr
                    ):
                        registry = self.store.RepositoryRegistry(state_home)
                        with self.assertRaises(self.store.UnsafeFilesystemError):
                            registry.get_or_create(
                                root,
                                vcs_kind="git",
                                remote_urls=[],
                                initial_tree_digest="a" * 40,
                            )
                    self.assertFalse((state_home / "repositories-v1.json").exists())
                    self.assertEqual(
                        [],
                        [
                            path
                            for path in state_home.iterdir()
                            if ".tmp-" in path.name
                        ],
                    )

    def test_atomic_temp_collision_is_preserved_and_never_installed(self):
        with tempfile.TemporaryDirectory(prefix="forge-registry-temp-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)
            root = base / "repo"
            root.mkdir()
            collision = state_home / ".repositories-v1.json.tmp-fixed"
            collision.write_text("preserve")
            collision.chmod(0o600)
            registry = self.store.RepositoryRegistry(state_home)
            with mock.patch.object(self.store.secrets, "token_hex", return_value="fixed"):
                with self.assertRaises(self.store.UnsafeFilesystemError):
                    registry.get_or_create(
                        root,
                        vcs_kind="git",
                        remote_urls=[],
                        initial_tree_digest="f" * 64,
                        random_bytes=lambda count: b"a" * 16,
                    )
            self.assertEqual("preserve", collision.read_text())
            self.assertFalse((state_home / "repositories-v1.json").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_registry_and_lock_symlink_attacks_do_not_touch_victims(self):
        for attacked_name in ("repositories-v1.json", ".repositories-v1.lock"):
            with self.subTest(attacked_name=attacked_name):
                with tempfile.TemporaryDirectory(prefix="forge-registry-link-") as temp_dir:
                    base = Path(temp_dir)
                    state_home = base / "state"
                    state_home.mkdir(mode=0o700)
                    root = base / "repo"
                    root.mkdir()
                    victim = base / "victim"
                    victim.write_text("preserve")
                    (state_home / attacked_name).symlink_to(victim)
                    registry = self.store.RepositoryRegistry(state_home)
                    with self.assertRaises(self.store.StateStoreError):
                        registry.get_or_create(
                            root,
                            vcs_kind="git",
                            remote_urls=[],
                            initial_tree_digest="f" * 64,
                        )
                    self.assertEqual("preserve", victim.read_text())

    def test_state_environment_is_scrubbed_for_untrusted_children(self):
        child = self.store.scrub_state_environment(
            {
                "PATH": "/bin",
                "FORGE_STATE_HOME": "/private/state",
                "FORGE_STATE_HANDLE": "9",
                "forge_state_home": "/also-private/state",
                "Forge_State_Handle": "10",
                "FORGE_MODE": "task",
            }
        )
        self.assertEqual({"PATH": "/bin", "FORGE_MODE": "task"}, child)

    @unittest.skipUnless(os.name == "posix", "registry adapter is POSIX-only")
    def test_concurrent_first_initialization_mints_one_repository_identity(self):
        with tempfile.TemporaryDirectory(prefix="forge-registry-race-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)
            root = base / "repo"
            root.mkdir()
            program = """
import importlib.util, pathlib, sys
module_path, state_home, root, byte = sys.argv[1:]
spec = importlib.util.spec_from_file_location('forge_state_store_child', module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
record = module.RepositoryRegistry(state_home).get_or_create(
    root,
    vcs_kind='git',
    remote_urls=['https://example.com/org/repo.git'],
    initial_tree_digest='a' * 64,
    random_bytes=lambda count: bytes([int(byte)]) * 16,
)
print(record['repository_id'])
"""
            processes = [
                subprocess.Popen(
                    (
                        sys.executable,
                        "-c",
                        program,
                        str(STATE_STORE_PATH),
                        str(state_home),
                        str(root),
                        str(value),
                    ),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for value in (1, 2)
            ]
            results = [process.communicate(timeout=10) for process in processes]
            for process, (_stdout, stderr) in zip(processes, results):
                self.assertEqual(0, process.returncode, stderr)
            identifiers = {stdout.strip() for stdout, _stderr in results}
            self.assertEqual(1, len(identifiers))
            registry = self.store.RepositoryRegistry(state_home)._load()
            self.assertEqual(1, len(registry["repositories"]))


class ResumeSelectionTests(StateStoreTestCase):
    def setUp(self):
        self.scope = "scope-" + ("a" * 64)
        self.items = [
            {
                "scope_id": self.scope,
                "work_item_id": "work-00000000000000000000000000000001",
                "title": "Build search",
                "status": "active",
            },
            {
                "scope_id": self.scope,
                "work_item_id": "work-00000000000000000000000000000002",
                "title": "Build search",
                "status": "suspended",
            },
            {
                "scope_id": "scope-" + ("b" * 64),
                "work_item_id": "work-00000000000000000000000000000003",
                "title": "Build search",
                "status": "active",
            },
        ]

    def test_duplicate_titles_never_auto_resume_and_list_candidate_ids(self):
        with self.assertRaises(self.store.AmbiguousResumeError) as caught:
            self.store.select_resume_candidate(
                self.items, scope_id=self.scope, title="Build search"
            )
        message = str(caught.exception)
        self.assertLess(message.index("00000001"), message.index("00000002"))
        self.assertNotIn("00000003", message)

    def test_explicit_id_is_scope_bound_and_titles_are_not_identifiers(self):
        selected = self.store.select_resume_candidate(
            self.items,
            scope_id=self.scope,
            work_item_id="work-00000000000000000000000000000002",
        )
        self.assertEqual("work-00000000000000000000000000000002", selected["work_item_id"])
        self.assertIsNone(
            self.store.select_resume_candidate(
                self.items,
                scope_id=self.scope,
                work_item_id="work-00000000000000000000000000000003",
            )
        )
        self.assertIsNone(
            self.store.select_resume_candidate(
                self.items, scope_id=self.scope, title="No match"
            )
        )

    def test_cancelled_items_are_not_resumable_and_malformed_candidates_fail_closed(self):
        cancelled = {
            "scope_id": self.scope,
            "work_item_id": "work-00000000000000000000000000000004",
            "title": "Cancelled",
            "status": "cancelled",
        }
        self.assertIsNone(
            self.store.select_resume_candidate(
                self.items + [cancelled], scope_id=self.scope, title="Cancelled"
            )
        )
        for candidates in ([7], [{"scope_id": self.scope, "title": "missing id"}]):
            with self.subTest(candidates=candidates):
                with self.assertRaises(self.store.StateStoreError):
                    self.store.select_resume_candidate(
                        candidates, scope_id=self.scope, title="missing id"
                    )

        unknown = {
            "scope_id": self.scope,
            "work_item_id": "work-00000000000000000000000000000005",
            "title": "Unknown",
            "status": "corrupt",
        }
        with self.assertRaises(self.store.StateStoreError):
            self.store.select_resume_candidate(
                self.items + [unknown], scope_id=self.scope, title="Unknown"
            )

        for malformed_scope in (None, [], 7):
            with self.subTest(malformed_scope=malformed_scope):
                with self.assertRaises(self.store.StateStoreError):
                    self.store.select_resume_candidate(
                        self.items, scope_id=malformed_scope
                    )

    def test_work_item_state_path_is_validated_and_beneath_scope(self):
        with tempfile.TemporaryDirectory(prefix="forge-work-path-") as temp_dir:
            work_id = "work-00000000000000000000000000000001"
            expected = Path(temp_dir) / self.scope / work_id
            self.assertEqual(
                expected,
                self.store.work_item_state_path(temp_dir, self.scope, work_id),
            )
            for invalid in ("../escape", "work-a/b", "work-" + ("a" * 31)):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(self.store.StateStoreError):
                        self.store.work_item_state_path(temp_dir, self.scope, invalid)


if __name__ == "__main__":
    unittest.main()
