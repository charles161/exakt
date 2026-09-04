import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATE_STORE_PATH = PACKAGE_ROOT / "skills/forge/scripts/state_store.py"


def load_state_store_module():
    spec = importlib.util.spec_from_file_location("forge_journal_store", STATE_STORE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import state-store module: {STATE_STORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DurableJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_state_store_module()
        cls.scope_id = "scope-" + ("1" * 64)
        cls.work_item_id = "work-" + ("2" * 32)

    def make_journal(self, base: Path):
        state_home = base / "state"
        state_home.mkdir(mode=0o700)
        return self.store.DurableJournal(
            state_home, self.scope_id, self.work_item_id
        )

    def append(self, journal, payload, expected_state_root, suffix="1"):
        return journal.append_event(
            payload,
            event_id=f"event-{suffix}",
            event_type="test.event",
            actor="controller",
            timestamp=f"2026-09-03T00:00:0{suffix}Z",
            idempotency_key=f"key-{suffix}",
            workflow_phase="intake",
            run_status="active",
            expected_state_root=expected_state_root,
        )

    def test_genesis_and_state_root_formula_are_exact(self):
        record_hash = "a" * 64
        expected = hashlib.sha256(
            (
                "forge-state-v1\n"
                + self.scope_id
                + "\n"
                + self.work_item_id
                + "\n7\n"
                + record_hash
            ).encode("ascii")
        ).hexdigest()
        self.assertEqual(
            expected,
            self.store.compute_state_root(
                self.scope_id, self.work_item_id, 7, record_hash
            ),
        )
        genesis = self.store.compute_genesis_state_root(
            self.scope_id, self.work_item_id
        )
        self.assertEqual(
            self.store.compute_state_root(
                self.scope_id, self.work_item_id, 0, "0" * 64
            ),
            genesis,
        )
        self.assertEqual(
            "96a76b577260a0589957f90c32643ade9bd345819c2e28246d76ede75e406e0e",
            self.store.compute_state_root(
                self.scope_id, self.work_item_id, 1, "3" * 64
            ),
        )
        for sequence in (-1, True, "1"):
            with self.subTest(sequence=sequence):
                with self.assertRaises(self.store.JournalError):
                    self.store.compute_state_root(
                        self.scope_id, self.work_item_id, sequence, "3" * 64
                    )

    def test_object_and_record_hash_golden_vectors_are_unframed(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-golden-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            payload = {"kind": "probe", "text": "é"}
            expected_object = b'{"kind":"probe","text":"\xc3\xa9"}'
            expected_digest = (
                "1be78d650df46570ac717101100815776bed7349eacbb2013cbd16d243d2e506"
            )
            self.assertEqual(expected_object, self.store.canonical_json_bytes(payload))
            self.assertEqual(expected_digest, journal.put_object(payload))
            result = journal.append_event(
                payload,
                event_id="event-golden",
                event_type="test.event",
                actor="controller",
                timestamp="2026-09-03T00:00:00Z",
                idempotency_key="key-golden",
                workflow_phase="intake",
                run_status="active",
                expected_state_root=journal.genesis_state_root,
            )
            self.assertEqual(
                "066eb4639c5f7efb44e5d1edcafc7b24999df258af8fa5c9b7df35c5cdf9e434",
                result.record_hash,
            )
            record = journal.inspect().records[0]
            event = dict(record)
            event.pop("record_hash")
            self.assertFalse(self.store.canonical_json_bytes(event).endswith(b"\n"))
            self.assertTrue(journal.journal_path.read_bytes().endswith(b"\n"))

    def test_content_addressed_objects_are_canonical_private_and_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="forge-object-store-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            value = {"z": [3, 2, 1], "a": "value"}
            digest = journal.put_object(value)
            expected_bytes = b'{"a":"value","z":[3,2,1]}'
            self.assertEqual(hashlib.sha256(expected_bytes).hexdigest(), digest)
            self.assertEqual(value, journal.read_object(digest))
            first_stat = journal.object_path(digest).stat()
            self.assertEqual(digest, journal.put_object(value))
            path = journal.object_path(digest)
            second_stat = path.stat()
            self.assertEqual(expected_bytes, path.read_bytes())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(first_stat.st_ino, second_stat.st_ino)
            self.assertEqual(first_stat.st_mtime_ns, second_stat.st_mtime_ns)
            self.assertEqual([path.name], sorted(item.name for item in path.parent.iterdir()))

    def test_unicode_is_not_normalized_and_corrupt_digest_target_is_never_replaced(self):
        with tempfile.TemporaryDirectory(prefix="forge-object-integrity-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            nfc = journal.put_object({"text": "é"})
            nfd = journal.put_object({"text": "e\u0301"})
            self.assertNotEqual(nfc, nfd)

            wanted = {"collision": "payload"}
            wanted_bytes = self.store.canonical_json_bytes(wanted)
            digest = hashlib.sha256(wanted_bytes).hexdigest()
            target = journal.object_path(digest)
            corrupt = b'{"different":true}'
            target.write_bytes(corrupt)
            target.chmod(0o600)
            with self.assertRaises(self.store.JournalCorruptionError):
                journal.put_object(wanted)
            self.assertEqual(corrupt, target.read_bytes())

    def test_invalid_event_is_rejected_before_object_or_journal_mutation(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-preflight-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            payload = {"must": "remain absent"}
            digest = hashlib.sha256(
                self.store.canonical_json_bytes(payload)
            ).hexdigest()
            with self.assertRaises(self.store.JournalError):
                journal.append_event(
                    payload,
                    event_id="",
                    event_type="test.event",
                    actor="controller",
                    timestamp="2026-09-03T00:00:00Z",
                    idempotency_key="key-invalid",
                    workflow_phase="intake",
                    run_status="active",
                    expected_state_root=journal.genesis_state_root,
                )
            self.assertFalse(journal.object_path(digest).exists())
            self.assertFalse(journal.journal_path.exists())
            with self.assertRaises(self.store.CanonicalStateError):
                journal.append_event(
                    payload,
                    event_id="event-surrogate",
                    event_type="test.event",
                    actor="\ud800",
                    timestamp="2026-09-03T00:00:00Z",
                    idempotency_key="key-surrogate",
                    workflow_phase="intake",
                    run_status="active",
                    expected_state_root=journal.genesis_state_root,
                )
            self.assertFalse(journal.object_path(digest).exists())
            self.assertFalse(journal.journal_path.exists())

    def test_append_writes_object_then_canonical_hash_chain(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-chain-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            genesis = journal.genesis_state_root
            first = self.append(journal, {"value": 1}, genesis, "1")
            first_bytes = journal.journal_path.read_bytes()
            second = self.append(journal, {"value": 2}, first.state_root, "2")

            inspection = journal.inspect()
            self.assertTrue(inspection.complete)
            self.assertFalse(inspection.head_pending)
            self.assertFalse(inspection.truncated_tail)
            self.assertEqual((), inspection.diagnostics)
            self.assertEqual(2, len(inspection.records))
            self.assertEqual(second.state_root, inspection.state_root)
            self.assertEqual(2, inspection.head_sequence)
            self.assertEqual(second.state_root, inspection.head_state_root)
            self.assertEqual(0o600, stat.S_IMODE(journal.head_path.stat().st_mode))
            self.assertEqual(1, inspection.records[0]["sequence"])
            self.assertIsNone(inspection.records[0]["previous_record_hash"])
            self.assertEqual(first.record_hash, inspection.records[1]["previous_record_hash"])
            self.assertEqual(genesis, inspection.records[0]["prior_state_root"])
            self.assertEqual(first.state_root, inspection.records[1]["prior_state_root"])
            for result, record in zip((first, second), inspection.records):
                without_hash = dict(record)
                self.assertEqual(result.record_hash, without_hash.pop("record_hash"))
                self.assertEqual(
                    result.record_hash,
                    self.store.canonical_sha256(without_hash),
                )
                digest = record["payload"]["payload_hash"]
                self.assertEqual(f"sha256:{digest}", record["payload"]["object_id"])

            raw_lines = journal.journal_path.read_bytes().splitlines(keepends=True)
            self.assertEqual(2, len(raw_lines))
            self.assertTrue(journal.journal_path.read_bytes().startswith(first_bytes))
            self.assertTrue(all(line.endswith(b"\n") for line in raw_lines))
            self.assertEqual(
                b"".join(
                    self.store.canonical_json_record(record)
                    for record in inspection.records
                ),
                journal.journal_path.read_bytes(),
            )

    def test_stale_expected_root_is_rejected_without_mutating_journal(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-cas-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            genesis = journal.genesis_state_root
            first = self.append(journal, {"value": 1}, genesis)
            before = journal.journal_path.read_bytes()
            with self.assertRaises(self.store.JournalConflictError) as caught:
                self.append(journal, {"value": 2}, genesis, "2")
            self.assertEqual(genesis, caught.exception.expected_state_root)
            self.assertEqual(first.state_root, caught.exception.observed_state_root)
            self.assertEqual(before, journal.journal_path.read_bytes())
            self.assertEqual(first.state_root, journal.inspect().state_root)

    def test_orphan_valid_object_is_tolerated_but_corrupt_object_blocks_replay(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-objects-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            orphan = journal.put_object({"orphan": True})
            empty = journal.inspect()
            self.assertTrue(empty.complete)
            self.assertEqual(journal.genesis_state_root, empty.state_root)
            self.assertTrue(journal.object_path(orphan).is_file())

            result = self.append(
                journal, {"referenced": True}, journal.genesis_state_root
            )
            record = journal.inspect().records[0]
            object_path = journal.object_path(record["payload"]["payload_hash"])
            object_path.write_bytes(b'{"corrupt":true}')
            object_path.chmod(0o600)
            broken = journal.inspect()
            self.assertFalse(broken.complete)
            self.assertEqual(0, len(broken.records))
            self.assertTrue(any("object" in item for item in broken.diagnostics))
            with self.assertRaises(self.store.JournalCorruptionError):
                self.append(journal, {"next": True}, result.state_root, "2")

    def test_wrong_sequence_previous_hash_and_record_hash_stop_at_valid_prefix(self):
        mutations = ("sequence", "previous_record_hash", "record_hash")
        for field in mutations:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(prefix="forge-journal-chain-bad-") as temp_dir:
                    journal = self.make_journal(Path(temp_dir))
                    first = self.append(
                        journal, {"value": 1}, journal.genesis_state_root, "1"
                    )
                    self.append(journal, {"value": 2}, first.state_root, "2")
                    records = [dict(item) for item in journal.inspect().records]
                    if field == "sequence":
                        records[1][field] = 9
                    else:
                        records[1][field] = "f" * 64
                    journal.journal_path.write_bytes(
                        b"".join(
                            self.store.canonical_json_record(item) for item in records
                        )
                    )
                    journal.journal_path.chmod(0o600)
                    inspection = journal.inspect()
                    self.assertFalse(inspection.complete)
                    self.assertEqual(1, len(inspection.records))
                    self.assertEqual(first.state_root, inspection.state_root)

    def test_closed_record_contract_and_chain_fields_fail_at_first_bad_record(self):
        mutations = {
            "prior_state_root": "e" * 64,
            "work_item_id": "work-" + ("9" * 32),
            "object_id": "sha256:" + ("f" * 64),
            "unknown_field": True,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(prefix="forge-journal-envelope-") as temp_dir:
                    journal = self.make_journal(Path(temp_dir))
                    self.append(journal, {"value": 1}, journal.genesis_state_root)
                    record = dict(journal.inspect().records[0])
                    if field == "object_id":
                        record["payload"] = dict(record["payload"])
                        record["payload"][field] = value
                    else:
                        record[field] = value
                    event = dict(record)
                    event.pop("record_hash")
                    record["record_hash"] = self.store.canonical_sha256(event)
                    journal.journal_path.write_bytes(
                        self.store.canonical_json_record(record)
                    )
                    journal.journal_path.chmod(0o600)
                    inspection = journal.inspect()
                    self.assertFalse(inspection.complete)
                    self.assertEqual(0, len(inspection.records))
                    expected_code = {
                        "prior_state_root": "prior_root_mismatch",
                        "work_item_id": "work_item_mismatch",
                        "object_id": "object_reference_mismatch",
                        "unknown_field": "event_contract",
                    }[field]
                    self.assertEqual(expected_code, inspection.diagnostics[0].code)

    def test_noncanonical_framing_is_diagnosed_without_modifying_bytes(self):
        for variant in ("missing_lf", "whitespace", "crlf", "bom", "blank"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory(prefix="forge-journal-framing-") as temp_dir:
                    journal = self.make_journal(Path(temp_dir))
                    self.append(journal, {"value": 1}, journal.genesis_state_root)
                    valid = journal.journal_path.read_bytes()
                    if variant == "missing_lf":
                        damaged = valid[:-1]
                    elif variant == "whitespace":
                        damaged = valid.replace(b":", b": ", 1)
                    elif variant == "crlf":
                        damaged = valid[:-1] + b"\r\n"
                    elif variant == "bom":
                        damaged = b"\xef\xbb\xbf" + valid
                    else:
                        damaged = b"\n" + valid
                    journal.journal_path.write_bytes(damaged)
                    journal.journal_path.chmod(0o600)
                    inspection = journal.inspect()
                    self.assertFalse(inspection.complete)
                    self.assertEqual(damaged, journal.journal_path.read_bytes())
                    if variant == "missing_lf":
                        self.assertTrue(inspection.truncated_tail)
                    else:
                        self.assertEqual(
                            "noncanonical_record", inspection.diagnostics[0].code
                        )

    def test_truncated_tail_recovers_only_after_explicit_prefix_cas_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-truncated-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            first = self.append(journal, {"value": 1}, journal.genesis_state_root, "1")
            original = journal.journal_path.read_bytes()
            truncated = original + b'{"uncommitted":"partial"'
            journal.journal_path.write_bytes(truncated)
            journal.journal_path.chmod(0o600)

            inspection = journal.inspect()
            self.assertFalse(inspection.complete)
            self.assertTrue(inspection.truncated_tail)
            self.assertEqual(1, len(inspection.records))
            self.assertEqual(first.state_root, inspection.state_root)
            with self.assertRaises(self.store.JournalConflictError):
                journal.recover_longest_valid_prefix("0" * 64)
            recovery = journal.recover_longest_valid_prefix(first.state_root)
            self.assertEqual(first.state_root, recovery.state_root)
            self.assertEqual(truncated, recovery.quarantined_path.read_bytes())
            self.assertTrue(journal.inspect().complete)
            self.assertEqual(1, len(journal.inspect().records))

    def test_every_truncation_offset_reports_the_exact_longest_valid_prefix(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-all-truncations-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            first = self.append(journal, {"value": 1}, journal.genesis_state_root, "1")
            self.append(journal, {"value": 2}, first.state_root, "2")
            complete = journal.journal_path.read_bytes()
            first_boundary = complete.index(b"\n") + 1
            for cutoff in range(len(complete) + 1):
                journal.journal_path.write_bytes(complete[:cutoff])
                journal.journal_path.chmod(0o600)
                inspection = journal.inspect()
                expected_records = int(cutoff >= first_boundary) + int(
                    cutoff == len(complete)
                )
                self.assertEqual(expected_records, len(inspection.records), cutoff)
                expected_prefix = (
                    len(complete)
                    if cutoff == len(complete)
                    else first_boundary if cutoff >= first_boundary else 0
                )
                self.assertEqual(
                    expected_prefix, inspection.valid_prefix_bytes, cutoff
                )
                self.assertEqual(cutoff == len(complete), inspection.complete)
                self.assertEqual(
                    cutoff not in (0, first_boundary, len(complete)),
                    inspection.truncated_tail,
                )
                if cutoff < len(complete) and not inspection.truncated_tail:
                    self.assertTrue(
                        any(
                            diagnostic.code == "head_ahead_of_journal"
                            for diagnostic in inspection.diagnostics
                        )
                    )

    def test_clean_suffix_truncation_is_detected_and_cannot_be_appended_over(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-clean-truncate-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            first = self.append(journal, {"value": 1}, journal.genesis_state_root, "1")
            first_bytes = journal.journal_path.read_bytes()
            second = self.append(journal, {"value": 2}, first.state_root, "2")
            journal.journal_path.write_bytes(first_bytes)
            journal.journal_path.chmod(0o600)

            inspection = journal.inspect()
            self.assertFalse(inspection.complete)
            self.assertEqual("head_ahead_of_journal", inspection.diagnostics[-1].code)
            self.assertEqual(second.state_root, inspection.head_state_root)
            before = journal.journal_path.read_bytes()
            with self.assertRaises(self.store.JournalCorruptionError):
                self.append(journal, {"value": 3}, first.state_root, "3")
            self.assertEqual(before, journal.journal_path.read_bytes())
            with self.assertRaises(self.store.JournalCorruptionError):
                journal.recover_longest_valid_prefix(first.state_root)

    def test_missing_corrupt_or_unsafe_head_never_falls_back_to_journal_alone(self):
        for variant in ("missing", "corrupt", "unsafe_mode"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory(prefix="forge-journal-head-bad-") as temp_dir:
                    base = Path(temp_dir)
                    journal = self.make_journal(base)
                    committed = self.append(
                        journal, {"value": 1}, journal.genesis_state_root
                    )
                    journal_bytes = journal.journal_path.read_bytes()
                    if variant == "missing":
                        journal.head_path.unlink()
                    elif variant == "corrupt":
                        journal.head_path.write_bytes(b"{}")
                        journal.head_path.chmod(0o600)
                    else:
                        journal.head_path.chmod(0o644)

                    reopened = self.store.DurableJournal(
                        journal.state_home, self.scope_id, self.work_item_id
                    )
                    inspection = reopened.inspect()
                    self.assertFalse(inspection.complete)
                    expected = (
                        "unsafe_head" if variant == "unsafe_mode" else "head_corrupt"
                    )
                    self.assertEqual(expected, inspection.diagnostics[-1].code)
                    with self.assertRaises(self.store.JournalCorruptionError):
                        self.append(reopened, {"value": 2}, committed.state_root, "2")
                    self.assertEqual(journal_bytes, reopened.journal_path.read_bytes())

    def test_pending_one_record_commit_reconciles_only_for_current_root_writer(self):
        class SimulatedCrash(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="forge-journal-pending-head-") as temp_dir:
            base = Path(temp_dir)
            state_home = base / "state"
            state_home.mkdir(mode=0o700)

            def fault(point):
                if point == "after_journal_replace":
                    raise SimulatedCrash(point)

            journal = self.store.DurableJournal(
                state_home,
                self.scope_id,
                self.work_item_id,
                fault_hook=fault,
            )
            genesis = journal.genesis_state_root
            with self.assertRaises(SimulatedCrash):
                self.append(journal, {"value": 1}, genesis, "1")
            pending = journal.inspect()
            self.assertTrue(pending.complete)
            self.assertTrue(pending.head_pending)
            self.assertEqual(1, len(pending.records))

            journal = self.store.DurableJournal(
                state_home, self.scope_id, self.work_item_id
            )
            with self.assertRaises(self.store.JournalConflictError):
                self.append(journal, {"value": 2}, genesis, "2")
            self.assertTrue(journal.inspect().head_pending)
            final = self.append(journal, {"value": 2}, pending.state_root, "2")
            inspection = journal.inspect()
            self.assertTrue(inspection.complete)
            self.assertFalse(inspection.head_pending)
            self.assertEqual(2, len(inspection.records))
            self.assertEqual(final.state_root, inspection.head_state_root)

    def test_crash_window_artifacts_are_quarantined_without_becoming_authority(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-crash-") as temp_dir:
            journal = self.make_journal(Path(temp_dir))
            object_temp = journal.objects_path / ".payload.tmp-before-object-install"
            object_temp.write_bytes(b"partial object")
            object_temp.chmod(0o600)
            journal_temp = journal.work_root / ".journal.jsonl.tmp-before-journal-replace"
            journal_temp.write_bytes(b"partial journal")
            journal_temp.chmod(0o600)

            orphan = journal.put_object({"installed": "after-object-install"})
            self.assertTrue(journal.object_path(orphan).is_file())
            self.assertFalse(journal.journal_path.exists())
            quarantined = journal.quarantine_temp_files()
            self.assertEqual(2, len(quarantined))
            self.assertFalse(object_temp.exists())
            self.assertFalse(journal_temp.exists())
            self.assertEqual(
                {b"partial object", b"partial journal"},
                {path.read_bytes() for path in quarantined},
            )
            self.assertTrue(journal.inspect().complete)
            final = self.append(
                journal,
                {"installed": "after-journal-replace"},
                journal.genesis_state_root,
            )
            self.assertEqual(final.state_root, journal.inspect().state_root)

    @unittest.skipUnless(os.name == "posix", "POSIX process crashes required")
    def test_real_process_crash_matrix_recovers_only_published_authority(self):
        program = r'''
import importlib.util, os, sys
module_path, state_home, scope_id, work_item_id, point, expected = sys.argv[1:]
spec = importlib.util.spec_from_file_location("forge_crash_child_" + point, module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
def fault(observed):
    if observed == point:
        os._exit(73)
journal = module.DurableJournal(
    state_home, scope_id, work_item_id, fault_hook=fault
)
journal.append_event(
    {"crash": point}, event_id="event-" + point,
    event_type="test.crash", actor="controller",
    timestamp="2026-09-03T00:00:09Z", idempotency_key="key-" + point,
    workflow_phase="intake", run_status="active", expected_state_root=expected,
)
raise SystemExit(2)
'''
        expectations = {
            "before_object_install": (1, False, 1, False),
            "after_object_install": (1, True, 0, False),
            "before_journal_replace": (1, True, 1, False),
            "after_journal_replace": (2, True, 0, True),
            "before_head_replace": (2, True, 1, True),
            "after_head_replace": (2, True, 0, False),
        }
        for point, (
            record_count,
            object_exists,
            temp_count,
            head_pending,
        ) in expectations.items():
            with self.subTest(point=point):
                with tempfile.TemporaryDirectory(prefix="forge-real-crash-") as temp_dir:
                    journal = self.make_journal(Path(temp_dir))
                    first = self.append(
                        journal, {"value": 1}, journal.genesis_state_root
                    )
                    payload = {"crash": point}
                    digest = hashlib.sha256(
                        self.store.canonical_json_bytes(payload)
                    ).hexdigest()
                    process = subprocess.run(
                        (
                            sys.executable,
                            "-c",
                            program,
                            str(STATE_STORE_PATH),
                            str(journal.state_home),
                            self.scope_id,
                            self.work_item_id,
                            point,
                            first.state_root,
                        ),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(73, process.returncode, process.stderr)
                    inspection = journal.inspect()
                    self.assertTrue(inspection.complete)
                    self.assertEqual(record_count, len(inspection.records))
                    self.assertEqual(head_pending, inspection.head_pending)
                    self.assertEqual(object_exists, journal.object_path(digest).exists())
                    leftovers = [
                        path
                        for directory in (journal.work_root, journal.objects_path)
                        for path in directory.iterdir()
                        if path.name.startswith(
                            (
                                ".object.tmp-",
                                ".journal.jsonl.tmp-",
                                ".head.json.tmp-",
                            )
                        )
                    ]
                    self.assertEqual(temp_count, len(leftovers))
                    quarantined = journal.quarantine_temp_files()
                    self.assertEqual(temp_count, len(quarantined))
                    self.assertTrue(journal.inspect().complete)

    def test_quarantine_refuses_symlinked_temp_without_following_it(self):
        with tempfile.TemporaryDirectory(prefix="forge-quarantine-link-") as temp_dir:
            base = Path(temp_dir)
            journal = self.make_journal(base)
            victim = base / "victim"
            victim.write_bytes(b"must remain")
            link = journal.objects_path / ".object.tmp-link"
            link.symlink_to(victim)
            with self.assertRaises(self.store.UnsafeFilesystemError):
                journal.quarantine_temp_files()
            self.assertEqual(b"must remain", victim.read_bytes())
            self.assertTrue(link.is_symlink())
            link.unlink()

            if hasattr(os, "mkfifo"):
                fifo = journal.objects_path / ".object.tmp-fifo"
                os.mkfifo(fifo, 0o600)
                with self.assertRaises(self.store.UnsafeFilesystemError):
                    journal.quarantine_temp_files()
                self.assertTrue(fifo.exists())
                fifo.unlink()

            directory = journal.objects_path / ".object.tmp-directory"
            directory.mkdir(mode=0o700)
            with self.assertRaises(self.store.UnsafeFilesystemError):
                journal.quarantine_temp_files()
            self.assertTrue(directory.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX process locking required")
    def test_two_processes_from_same_root_produce_one_append_and_one_conflict(self):
        with tempfile.TemporaryDirectory(prefix="forge-journal-race-") as temp_dir:
            base = Path(temp_dir)
            journal = self.make_journal(base)
            program = r'''
import importlib.util, json, pathlib, sys
module_path, state_home, scope_id, work_item_id, suffix, expected = sys.argv[1:]
spec = importlib.util.spec_from_file_location("forge_journal_child_" + suffix, module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
journal = module.DurableJournal(state_home, scope_id, work_item_id)
try:
    result = journal.append_event(
        {"writer": suffix}, event_id="event-" + suffix,
        event_type="test.concurrent", actor="controller",
        timestamp="2026-09-03T00:00:00Z", idempotency_key="key-" + suffix,
        workflow_phase="intake", run_status="active", expected_state_root=expected,
    )
except module.JournalConflictError:
    print("conflict")
else:
    print("ok:" + result.state_root)
'''
            processes = [
                subprocess.Popen(
                    (
                        sys.executable,
                        "-c",
                        program,
                        str(STATE_STORE_PATH),
                        str(journal.state_home),
                        self.scope_id,
                        self.work_item_id,
                        suffix,
                        journal.genesis_state_root,
                    ),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for suffix in ("a", "b")
            ]
            outputs = [process.communicate(timeout=15) for process in processes]
            for process, (_stdout, stderr) in zip(processes, outputs):
                self.assertEqual(0, process.returncode, stderr)
            labels = sorted(stdout.strip().split(":", 1)[0] for stdout, _ in outputs)
            self.assertEqual(["conflict", "ok"], labels)
            inspection = journal.inspect()
            self.assertTrue(inspection.complete)
            self.assertEqual(1, len(inspection.records))


if __name__ == "__main__":
    unittest.main()
