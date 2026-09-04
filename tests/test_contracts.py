import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = PACKAGE_ROOT / "skills/exakt/schemas"
SCRIPTS_ROOT = PACKAGE_ROOT / "skills/exakt/scripts"
CONTRACTS_PATH = SCRIPTS_ROOT / "contracts.py"
CLI_PATH = SCRIPTS_ROOT / "validate_state.py"
FIXTURE_ROOT = PACKAGE_ROOT / "tests/fixtures/contracts"
DESIGN_SPEC = PACKAGE_ROOT / "docs/design.md"
SCHEMA_BASE_ID = "urn:exakt:schema:"


def stable_schema_id(filename):
    return SCHEMA_BASE_ID + filename.removesuffix(".json")


REQUIRED_SCHEMAS = (
    "journal-event-v1.json",
    "source-intent-v1.json",
    "oracle-v1.json",
    "task-graph-v1.json",
    "product-graph-v1.json",
    "approval-v1.json",
    "input-manifest-v1.json",
    "evidence-v1.json",
    "verification-ledger-v1.json",
    "verification-bundle-v1.json",
    "verifier-attestation-v1.json",
    "external-action-v1.json",
    "exakt-feedback-v1.json",
    "specialist-manifest-v1.json",
    "agent-envelope-v1.json",
)
EXPECTED_ENUMS = {
    "workflow_phase": {
        "intake",
        "recon",
        "requirements",
        "design",
        "plan",
        "execute",
        "verify",
        "handoff",
    },
    "run_status": {"active", "suspended", "cancelled"},
    "task_status": {
        "ready",
        "implementing",
        "observing",
        "verifying",
        "repairing",
        "verified",
        "blocked",
        "unverified",
        "failed",
        "cancelled",
    },
    "claim_status": {
        "verified",
        "partially_verified",
        "failed",
        "blocked",
        "unverified",
        "stale",
        "contradicted",
    },
    "verification_tier": {"none", "standard", "independent"},
    "closure_status": {
        "open",
        "verified_complete",
        "independently_verified_complete",
        "closed_with_unverified_items",
        "blocked",
        "failed",
        "cancelled",
    },
    "external_action_state": {
        "planned",
        "approved",
        "intent_persisted",
        "send_started",
        "confirmed",
        "unknown",
        "failed",
    },
}


def load_contracts_module():
    if not CONTRACTS_PATH.is_file():
        raise AssertionError(f"missing validator module: {CONTRACTS_PATH}")
    spec = importlib.util.spec_from_file_location("exakt_contracts", CONTRACTS_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import validator module: {CONTRACTS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def iter_schema_nodes(schema):
    yield schema
    for child in schema.get("$defs", {}).values():
        yield from iter_schema_nodes(child)
    for child in schema.get("properties", {}).values():
        yield from iter_schema_nodes(child)
    items = schema.get("items")
    if isinstance(items, dict):
        yield from iter_schema_nodes(items)


def find_named_definitions(schemas):
    definitions = {}
    for schema in schemas.values():
        for name, definition in schema.get("$defs", {}).items():
            if name in EXPECTED_ENUMS:
                if name in definitions:
                    if definitions[name] != definition:
                        raise AssertionError(
                            f"inconsistent definition for {name}: "
                            f"{definitions[name]!r} != {definition!r}"
                        )
                else:
                    definitions[name] = definition
    return definitions


def iter_json_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from iter_json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_strings(child)


def write_probe_schema(root, value_schema, definitions=None):
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:exakt:schema:probe-v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "value"],
        "properties": {
            "schema_version": {"type": "string", "const": "probe-v1"},
            "value": value_schema,
        },
    }
    if definitions is not None:
        schema["$defs"] = definitions
    path = Path(root) / "probe-v1.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def remove_path(document, path):
    cursor = document
    for part in path[:-1]:
        cursor = cursor[part]
    del cursor[path[-1]]


def set_path(document, path, value):
    cursor = document
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


class SchemaDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema_paths = {
            path.name: path for path in SCHEMA_ROOT.glob("*.json")
        }

    def test_exact_required_schema_file_set_exists(self):
        self.assertEqual(set(REQUIRED_SCHEMAS), set(self.schema_paths))

    def test_schema_files_are_json_objects_with_stable_ids_and_versions(self):
        for filename in REQUIRED_SCHEMAS:
            with self.subTest(schema=filename):
                path = SCHEMA_ROOT / filename
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(schema, dict)
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema.get("$schema"),
                )
                self.assertEqual(stable_schema_id(filename), schema.get("$id"))
                self.assertEqual("object", schema.get("type"))
                version = filename.removesuffix(".json")
                self.assertEqual(
                    {"type": "string", "const": version},
                    schema.get("properties", {}).get("schema_version"),
                )
                self.assertIn("schema_version", schema.get("required", []))

    def test_contract_json_never_claims_an_unowned_openai_namespace(self):
        paths = list(SCHEMA_ROOT.glob("*.json")) + [
            path
            for path in FIXTURE_ROOT.glob("*.json")
            if path.name != "malformed.json"
        ]
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            for value in iter_json_strings(document):
                with self.subTest(path=path.name, value=value):
                    self.assertFalse(value.startswith("https://exakt.openai.com/"))

    def test_every_object_shape_has_explicit_fail_closed_additional_properties(self):
        for filename in REQUIRED_SCHEMAS:
            schema = json.loads(
                (SCHEMA_ROOT / filename).read_text(encoding="utf-8")
            )
            for index, node in enumerate(iter_schema_nodes(schema)):
                if node.get("type") == "object" or any(
                    key in node for key in ("properties", "required")
                ):
                    with self.subTest(schema=filename, node=index):
                        self.assertIn("additionalProperties", node)
                        self.assertIs(
                            False,
                            node["additionalProperties"],
                            "Exakt object contracts must reject unknown fields",
                        )

    def test_normative_enums_are_complete_and_orthogonal(self):
        schemas = {
            filename: json.loads(
                (SCHEMA_ROOT / filename).read_text(encoding="utf-8")
            )
            for filename in REQUIRED_SCHEMAS
        }
        definitions = find_named_definitions(schemas)
        self.assertEqual(set(EXPECTED_ENUMS), set(definitions))
        for name, expected in EXPECTED_ENUMS.items():
            with self.subTest(enum=name):
                self.assertEqual("string", definitions[name].get("type"))
                self.assertEqual(expected, set(definitions[name].get("enum", [])))

        self.assertNotIn(
            "blocked",
            definitions["workflow_phase"]["enum"],
            "blocked is a status, never a workflow phase",
        )
        self.assertNotIn(
            "independently_verified",
            definitions["claim_status"]["enum"],
            "independence is a tier, never a claim result",
        )
        self.assertIn("independent", definitions["verification_tier"]["enum"])
        self.assertIn(
            "independently_verified_complete",
            definitions["closure_status"]["enum"],
        )

    def test_section_4_9_uses_tier_not_independently_verified_claim_status(self):
        text = DESIGN_SPEC.read_text(encoding="utf-8")
        section = text.split("### 4.9 `verification-ledger.json`", 1)[1].split(
            "### 4.10 Evidence freshness and invalidation", 1
        )[0]
        self.assertNotIn("`independently_verified`", section)
        self.assertIn("`verification_tier=independent`", section)

    def test_portable_contract_resource_limits_are_documented(self):
        text = DESIGN_SPEC.read_text(encoding="utf-8")
        self.assertIn("100,000 digits per integer", text)
        self.assertIn("256 levels of JSON nesting", text)
        self.assertIn("100,000 JSON nodes per document", text)
        self.assertIn("256 local-reference hops", text)


class ContractValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = load_contracts_module()
        cls.registry = cls.contracts.ContractRegistry(SCHEMA_ROOT)
        cls.examples = json.loads(
            (FIXTURE_ROOT / "valid-examples.json").read_text(encoding="utf-8")
        )

    def test_fixture_has_one_valid_real_file_example_per_schema(self):
        self.assertEqual(set(REQUIRED_SCHEMAS), set(self.examples))
        for filename, document in self.examples.items():
            with self.subTest(schema=filename):
                self.assertEqual(
                    stable_schema_id(filename),
                    self.registry.validate(document, filename),
                )

    def test_schema_version_auto_selects_the_real_installed_schema(self):
        for filename, document in self.examples.items():
            with self.subTest(schema=filename):
                self.assertEqual(
                    stable_schema_id(filename),
                    self.registry.validate(document),
                )

    def test_every_contract_rejects_unknown_fields_and_versions(self):
        for filename, valid_document in self.examples.items():
            with self.subTest(schema=filename, case="unknown field"):
                document = copy.deepcopy(valid_document)
                document["future_field"] = "must fail closed"
                with self.assertRaisesRegex(
                    self.contracts.ContractError,
                    r"\$\.future_field: unknown field",
                ):
                    self.registry.validate(document, filename)

            with self.subTest(schema=filename, case="unknown version"):
                document = copy.deepcopy(valid_document)
                document["schema_version"] = filename.replace("-v1.json", "-v2")
                with self.assertRaises(self.contracts.ContractError):
                    self.registry.validate(document, filename)

    def test_unknown_schema_id_and_future_auto_version_are_rejected(self):
        document = copy.deepcopy(self.examples["approval-v1.json"])
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "unknown schema",
        ):
            self.registry.validate(
                document,
                "urn:exakt:schema:approval-v99",
            )

        document["schema_version"] = "approval-v99"
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "unknown schema version",
        ):
            self.registry.validate(document)

    def test_confusing_status_and_independence_fixtures_fail_or_pass_explicitly(self):
        cases = (
            ("invalid-workflow-phase-blocked.json", "journal-event-v1.json", False),
            ("valid-task-status-blocked.json", "task-graph-v1.json", True),
            (
                "invalid-claim-status-independently-verified.json",
                "verification-ledger-v1.json",
                False,
            ),
            (
                "valid-independent-verification.json",
                "verification-ledger-v1.json",
                True,
            ),
            ("invalid-agent-prose-approval.json", "approval-v1.json", False),
            ("valid-live-user-approval-shape.json", "approval-v1.json", True),
        )
        for fixture, schema, valid in cases:
            document = self.contracts.load_json_document(FIXTURE_ROOT / fixture)
            with self.subTest(fixture=fixture):
                if valid:
                    self.registry.validate(document, schema)
                else:
                    with self.assertRaises(self.contracts.ContractError):
                        self.registry.validate(document, schema)

    def test_agent_prose_is_not_an_approval_authority_kind(self):
        document = self.contracts.load_json_document(
            FIXTURE_ROOT / "invalid-agent-prose-approval.json"
        )
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            r"\$\.authority\.authority_kind:.*agent_prose",
        ):
            self.registry.validate(document, "approval-v1")

    def test_live_user_fixture_proves_shape_not_authentication(self):
        document = self.contracts.load_json_document(
            FIXTURE_ROOT / "valid-live-user-approval-shape.json"
        )
        self.assertEqual("live_user", document["authority"]["authority_kind"])
        self.registry.validate(document, "approval-v1")

    def test_required_v1_contract_data_has_closed_digest_bound_shapes(self):
        oracle_row = self.examples["oracle-v1.json"]["rows"][0]
        self.assertEqual(
            {"subject_kind", "target_ids"}, set(oracle_row["scope"])
        )

        product_slice = self.examples["product-graph-v1.json"]["slices"][0]
        self.assertRegex(product_slice["parent_oracle_digest"], r"^[0-9a-f]{64}$")
        for collection in ("inherited_oracle_rows", "local_oracle_rows"):
            self.assertGreater(len(product_slice[collection]), 0)
            self.assertEqual(
                {"row_id", "row_digest"}, set(product_slice[collection][0])
            )
        self.assertIn(product_slice["closure_policy"], {"required", "optional", "deferred"})
        self.assertEqual(
            {
                "child_terminal_state_root",
                "child_oracle_id",
                "child_oracle_digest",
                "verification_bundle_id",
                "verification_bundle_digest",
                "closure_status",
            },
            set(product_slice["closure_receipt"]),
        )

        approval_scope = self.examples["approval-v1.json"]["scope"]
        self.assertEqual(
            {
                "targets",
                "action_class",
                "slice_id",
                "oracle_digest",
                "external_action_policy_digest",
                "action_budget_digest",
            },
            set(approval_scope),
        )

        manifest = self.examples["input-manifest-v1.json"]
        self.assertEqual({"commit", "tree"}, set(manifest["source_revision"]))
        self.assertEqual(
            {"tracked", "dirty", "untracked"},
            {entry["provenance"] for entry in manifest["regular_files"]},
        )
        self.assertEqual(
            {"path", "provenance", "content_digest", "size"},
            set(manifest["regular_files"][0]),
        )
        self.assertEqual(
            {"path", "provenance", "link_target", "link_target_digest"},
            set(manifest["symlinks"][0]),
        )
        self.assertEqual(
            {
                "path",
                "provenance",
                "pointer_digest",
                "pointer_size",
                "materialized_object_digest",
                "materialized_size",
            },
            set(manifest["lfs_objects"][0]),
        )
        self.assertEqual(
            {
                "path",
                "provenance",
                "repository_id",
                "pinned_commit",
                "dirty_manifest_digest",
            },
            set(manifest["submodules"][0]),
        )
        self.assertEqual(
            {"path", "provenance", "base_digest"},
            set(manifest["deleted_files"][0]),
        )
        self.assertEqual("dirty", manifest["deleted_files"][0]["provenance"])
        self.assertEqual(
            {"included_classes", "excluded_classes", "explicitly_included_paths"},
            set(manifest["inclusion_rules"]),
        )

        evidence = self.examples["evidence-v1.json"]
        self.assertNotIn("input_manifest_digest", evidence)
        self.assertRegex(evidence["subject_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual({"kind", "description"}, set(evidence["operation"]))
        self.assertEqual(
            {
                "input_manifest_digest",
                "tool_versions",
                "runtime_versions",
                "environment_identity",
                "configuration_revisions",
                "provider_revisions",
                "external_revision",
            },
            set(evidence["execution_envelope"]),
        )
        self.assertIn("residual_risks", evidence)

        bundle = self.examples["verification-bundle-v1.json"]
        self.assertEqual(
            {"proof_contract_id", "proof_contract_digest", "claim_ids"},
            set(bundle["proof_contract_refs"][0]),
        )
        self.assertEqual(["claim-001"], bundle["proof_contract_refs"][0]["claim_ids"])
        subject_node = bundle["subject_graph"]["nodes"][0]
        self.assertIn("subject_digest", subject_node)
        self.assertEqual(
            {"starts_at", "expires_at"}, set(subject_node["applicability_window"])
        )

        ledger = self.examples["verification-ledger-v1.json"]
        self.assertRegex(ledger["bundle_digest"], r"^[0-9a-f]{64}$")
        proof = ledger["claims"][0]["proof_contract"]
        self.assertIn("proof_contract_id", proof)
        self.assertEqual(
            {"subject_id", "subject_digest"}, set(proof["subjects"][0])
        )

        attestation = self.examples["verifier-attestation-v1.json"]
        self.assertRegex(attestation["bundle_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            {"subject_id", "subject_digest"},
            set(attestation["subject_bindings"][0]),
        )

    def test_pointer_only_lfs_inputs_are_representable(self):
        document = copy.deepcopy(self.examples["input-manifest-v1.json"])
        lfs_object = document["lfs_objects"][0]
        lfs_object["materialized_object_digest"] = None
        lfs_object["materialized_size"] = None

        self.registry.validate(document, "input-manifest-v1")

    def test_verification_bundle_maps_each_proof_reference_to_claims(self):
        document = copy.deepcopy(self.examples["verification-bundle-v1.json"])
        document["proof_contract_refs"][0]["claim_ids"] = ["claim-001"]

        self.registry.validate(document, "verification-bundle-v1")

    def test_evidence_can_bind_the_exact_immutable_subject(self):
        document = copy.deepcopy(self.examples["evidence-v1.json"])
        document["subject_digest"] = "9" * 64

        self.registry.validate(document, "evidence-v1")

    def test_each_new_immutable_binding_is_required(self):
        cases = {
            "oracle-v1.json": [
                ("rows", 0, "scope"),
                ("rows", 0, "scope", "subject_kind"),
                ("rows", 0, "scope", "target_ids"),
            ],
            "product-graph-v1.json": [
                ("slices", 0, "parent_oracle_digest"),
                ("slices", 0, "inherited_oracle_rows"),
                ("slices", 0, "inherited_oracle_rows", 0, "row_id"),
                ("slices", 0, "inherited_oracle_rows", 0, "row_digest"),
                ("slices", 0, "local_oracle_rows"),
                ("slices", 0, "local_oracle_rows", 0, "row_id"),
                ("slices", 0, "local_oracle_rows", 0, "row_digest"),
                ("slices", 0, "closure_policy"),
                ("slices", 0, "closure_receipt"),
                ("slices", 0, "closure_receipt", "child_terminal_state_root"),
                ("slices", 0, "closure_receipt", "child_oracle_id"),
                ("slices", 0, "closure_receipt", "child_oracle_digest"),
                ("slices", 0, "closure_receipt", "verification_bundle_id"),
                ("slices", 0, "closure_receipt", "verification_bundle_digest"),
                ("slices", 0, "closure_receipt", "closure_status"),
            ],
            "approval-v1.json": [
                ("scope", "slice_id"),
                ("scope", "oracle_digest"),
                ("scope", "external_action_policy_digest"),
                ("scope", "action_budget_digest"),
            ],
            "input-manifest-v1.json": [
                ("source_revision", "commit"),
                ("source_revision", "tree"),
                ("regular_files", 0, "provenance"),
                ("regular_files", 0, "content_digest"),
                ("symlinks", 0, "provenance"),
                ("symlinks", 0, "link_target"),
                ("symlinks", 0, "link_target_digest"),
                ("lfs_objects", 0, "provenance"),
                ("lfs_objects", 0, "pointer_digest"),
                ("lfs_objects", 0, "materialized_object_digest"),
                ("submodules", 0, "repository_id"),
                ("submodules", 0, "pinned_commit"),
                ("submodules", 0, "dirty_manifest_digest"),
                ("deleted_files",),
                ("deleted_files", 0, "provenance"),
                ("deleted_files", 0, "base_digest"),
                ("inclusion_rules", "explicitly_included_paths"),
            ],
            "evidence-v1.json": [
                ("subject_digest",),
                ("operation",),
                ("operation", "kind"),
                ("operation", "description"),
                ("execution_envelope",),
                ("execution_envelope", "input_manifest_digest"),
                ("execution_envelope", "tool_versions"),
                ("execution_envelope", "runtime_versions"),
                ("execution_envelope", "environment_identity"),
                ("execution_envelope", "configuration_revisions"),
                ("execution_envelope", "provider_revisions"),
                ("execution_envelope", "external_revision"),
                ("residual_risks",),
            ],
            "verification-bundle-v1.json": [
                ("subject_graph", "nodes", 0, "subject_digest"),
                ("subject_graph", "nodes", 0, "applicability_window"),
                ("subject_graph", "nodes", 0, "applicability_window", "starts_at"),
                ("subject_graph", "nodes", 0, "applicability_window", "expires_at"),
                ("proof_contract_refs",),
                ("proof_contract_refs", 0, "proof_contract_id"),
                ("proof_contract_refs", 0, "proof_contract_digest"),
                ("proof_contract_refs", 0, "claim_ids"),
            ],
            "verification-ledger-v1.json": [
                ("bundle_digest",),
                ("claims", 0, "proof_contract", "proof_contract_id"),
                ("claims", 0, "proof_contract", "subjects"),
                ("claims", 0, "proof_contract", "subjects", 0, "subject_id"),
                ("claims", 0, "proof_contract", "subjects", 0, "subject_digest"),
            ],
            "verifier-attestation-v1.json": [
                ("bundle_digest",),
                ("subject_bindings",),
                ("subject_bindings", 0, "subject_id"),
                ("subject_bindings", 0, "subject_digest"),
            ],
        }
        for schema, paths in cases.items():
            for path in paths:
                with self.subTest(schema=schema, path=path):
                    document = copy.deepcopy(self.examples[schema])
                    remove_path(document, path)
                    with self.assertRaisesRegex(
                        self.contracts.ContractError, "required field is missing"
                    ):
                        self.registry.validate(document, schema)

    def test_new_nested_contract_shapes_reject_unknown_fields(self):
        cases = {
            "oracle-v1.json": [("rows", 0, "scope", "future")],
            "product-graph-v1.json": [
                ("slices", 0, "inherited_oracle_rows", 0, "future"),
                ("slices", 0, "closure_receipt", "future"),
            ],
            "approval-v1.json": [("scope", "future")],
            "input-manifest-v1.json": [
                ("source_revision", "future"),
                ("regular_files", 0, "future"),
                ("symlinks", 0, "future"),
                ("lfs_objects", 0, "future"),
                ("submodules", 0, "url"),
                ("deleted_files", 0, "future"),
                ("inclusion_rules", "future"),
            ],
            "evidence-v1.json": [
                ("operation", "future"),
                ("execution_envelope", "future"),
                ("execution_envelope", "tool_versions", 0, "future"),
                ("execution_envelope", "environment_identity", "future"),
                ("execution_envelope", "configuration_revisions", 0, "future"),
                ("execution_envelope", "external_revision", "future"),
            ],
            "verification-bundle-v1.json": [
                ("subject_graph", "nodes", 0, "applicability_window", "future"),
                ("proof_contract_refs", 0, "future"),
            ],
            "verification-ledger-v1.json": [
                ("claims", 0, "proof_contract", "subjects", 0, "future"),
            ],
            "verifier-attestation-v1.json": [
                ("subject_bindings", 0, "future"),
            ],
        }
        for schema, paths in cases.items():
            for path in paths:
                with self.subTest(schema=schema, path=path):
                    document = copy.deepcopy(self.examples[schema])
                    set_path(document, path, "must fail closed")
                    with self.assertRaisesRegex(self.contracts.ContractError, "unknown field"):
                        self.registry.validate(document, schema)

    def test_python_bool_is_not_accepted_as_integer(self):
        document = copy.deepcopy(self.examples["task-graph-v1.json"])
        document["tasks"][0]["attempts"] = True
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            r"\$\.tasks\[0\]\.attempts: expected integer",
        ):
            self.registry.validate(document, "task-graph-v1")

    def test_structured_const_and_enum_use_recursive_json_type_equality(self):
        value_schema = {
            "type": "array",
            "items": {"type": ["boolean", "integer"]},
            "const": [True],
            "enum": [[True]],
        }
        with tempfile.TemporaryDirectory(prefix="exakt-contract-equality-") as temp_dir:
            write_probe_schema(temp_dir, value_schema)
            registry = self.contracts.ContractRegistry(temp_dir)
            with self.assertRaises(self.contracts.ContractError):
                registry.validate({"schema_version": "probe-v1", "value": [1]})
            registry.validate({"schema_version": "probe-v1", "value": [True]})

        self.assertFalse(self.contracts._json_equal([True], [1]))
        self.assertFalse(
            self.contracts._json_equal({"nested": [True]}, {"nested": [1]})
        )

    def test_unique_items_uses_json_type_sensitive_structural_equality(self):
        value_schema = {
            "type": "array",
            "items": {"type": ["boolean", "integer"]},
            "uniqueItems": True,
        }
        with tempfile.TemporaryDirectory(prefix="exakt-contract-unique-") as temp_dir:
            write_probe_schema(temp_dir, value_schema)
            registry = self.contracts.ContractRegistry(temp_dir)
            registry.validate(
                {"schema_version": "probe-v1", "value": [True, 1]}
            )
            with self.assertRaisesRegex(self.contracts.ContractError, "duplicate"):
                registry.validate(
                    {"schema_version": "probe-v1", "value": [1, 1]}
                )

    def test_large_json_integers_validate_without_raw_runtime_errors(self):
        large_integer_text = "1" + ("0" * 4999)
        parsed = self.contracts.loads_json_document(large_integer_text)
        self.assertIsInstance(parsed, int)

        document = copy.deepcopy(self.examples["task-graph-v1.json"])
        document["tasks"][0]["attempts"] = parsed
        self.registry.validate(document, "task-graph-v1")

        over_limit = "1" + ("0" * self.contracts.MAX_PARSED_INTEGER_DIGITS)
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "portable parser limit",
        ):
            self.contracts.loads_json_document(over_limit)

        large_negative = self.contracts.loads_json_document("-" + ("1" * 5000))
        document["tasks"][0]["attempts"] = large_negative
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "below minimum",
        ):
            self.registry.validate(document, "task-graph-v1")

    def test_parser_is_independent_of_process_global_integer_digit_limit(self):
        previous_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(640)
            value = self.contracts.loads_json_document("1" * 641)
        finally:
            sys.set_int_max_str_digits(previous_limit)
        self.assertIsInstance(value, int)

    def test_importable_values_obey_the_same_integer_resource_boundary(self):
        document = copy.deepcopy(self.examples["task-graph-v1.json"])
        document["tasks"][0]["attempts"] = (
            10 ** self.contracts.MAX_PARSED_INTEGER_DIGITS
        )
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "integer exceeds portable",
        ):
            self.registry.validate(document, "task-graph-v1")

    def test_parser_enforces_document_depth_and_node_limits(self):
        too_deep = ("[" * 257) + "0" + ("]" * 257)
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "nesting depth",
        ):
            self.contracts.loads_json_document(too_deep)

        too_many_nodes = "[" + ",".join(["0"] * 100_001) + "]"
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "node limit",
        ):
            self.contracts.loads_json_document(too_many_nodes)

    def test_excessive_instance_nesting_fails_as_a_contract_error(self):
        value = 0
        for _ in range(1100):
            value = [value]

        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "nesting depth",
        ):
            self.registry.validate(value, "approval-v1")

    def test_importable_api_rejects_every_non_json_domain_value_as_contract_error(self):
        base = copy.deepcopy(self.examples["approval-v1.json"])
        cases = []

        non_string_key = copy.deepcopy(base)
        non_string_key[1] = "invalid key"
        cases.append(non_string_key)

        nested_non_string_key = copy.deepcopy(base)
        nested_non_string_key["authority"][1] = "invalid key"
        cases.append(nested_non_string_key)

        for value in (1.25, float("nan"), b"bytes", ("tuple",), {"set"}):
            document = copy.deepcopy(base)
            document["authority"]["identity"] = value
            cases.append(document)

        cyclic_list = []
        cyclic_list.append(cyclic_list)
        document = copy.deepcopy(base)
        document["authority"]["identity"] = cyclic_list
        cases.append(document)

        cyclic_object = {}
        cyclic_object["self"] = cyclic_object
        document = copy.deepcopy(base)
        document["authority"]["identity"] = cyclic_object
        cases.append(document)

        for index, document in enumerate(cases):
            with self.subTest(case=index):
                messages = []
                for _ in range(2):
                    with self.assertRaises(self.contracts.ContractError) as caught:
                        self.registry.validate(document, "approval-v1")
                    messages.append(str(caught.exception))
                self.assertEqual(messages[0], messages[1])
                self.assertIn("JSON", messages[0])

    def test_importable_api_rejects_non_string_schema_selectors(self):
        document = copy.deepcopy(self.examples["approval-v1.json"])
        for selector in (1, [], {}):
            with self.subTest(selector=selector):
                with self.assertRaisesRegex(
                    self.contracts.ContractError, "schema selector must be a string"
                ):
                    self.registry.validate(document, selector)

    def test_metadata_types_are_checked_and_nested_resources_are_rejected(self):
        cases = (
            ({"title": []}, "title must be a string"),
            ({"description": 1}, "description must be a string"),
            ({"$id": []}, r"\$id must be a string"),
            ({"$id": "urn:exakt:nested"}, "nested resource identifiers"),
            ({"$schema": self.contracts.SCHEMA_DIALECT}, "nested schema dialects"),
        )
        for metadata, message in cases:
            with self.subTest(metadata=metadata):
                with tempfile.TemporaryDirectory(
                    prefix="exakt-contract-metadata-"
                ) as temp_dir:
                    write_probe_schema(
                        temp_dir,
                        {"type": "string", **metadata},
                    )
                    with self.assertRaisesRegex(
                        self.contracts.SchemaDefinitionError, message
                    ):
                        self.contracts.ContractRegistry(temp_dir)

    def test_ref_siblings_are_conjunctive_and_cycles_still_fail_closed(self):
        definitions = {
            "text": {"type": "string", "minLength": 1},
        }
        value_schema = {"$ref": "#/$defs/text", "minLength": 3}
        with tempfile.TemporaryDirectory(prefix="exakt-contract-ref-") as temp_dir:
            write_probe_schema(temp_dir, value_schema, definitions)
            registry = self.contracts.ContractRegistry(temp_dir)
            with self.assertRaisesRegex(self.contracts.ContractError, "at least 1"):
                registry.validate({"schema_version": "probe-v1", "value": ""})
            with self.assertRaisesRegex(self.contracts.ContractError, "at least 3"):
                registry.validate({"schema_version": "probe-v1", "value": "ab"})
            registry.validate({"schema_version": "probe-v1", "value": "abc"})

        definitions = {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            }
        }
        with tempfile.TemporaryDirectory(prefix="exakt-contract-array-ref-") as temp_dir:
            write_probe_schema(
                temp_dir,
                {"$ref": "#/$defs/names", "minItems": 2},
                definitions,
            )
            registry = self.contracts.ContractRegistry(temp_dir)
            with self.assertRaisesRegex(self.contracts.ContractError, "at least 2"):
                registry.validate({"schema_version": "probe-v1", "value": ["one"]})
            registry.validate(
                {"schema_version": "probe-v1", "value": ["one", "two"]}
            )

        definitions = {
            "payload": {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {"name": {"type": "string"}},
            }
        }
        with tempfile.TemporaryDirectory(prefix="exakt-contract-object-ref-") as temp_dir:
            write_probe_schema(
                temp_dir,
                {"$ref": "#/$defs/payload", "required": ["name"]},
                definitions,
            )
            registry = self.contracts.ContractRegistry(temp_dir)
            with self.assertRaisesRegex(self.contracts.ContractError, "required field"):
                registry.validate({"schema_version": "probe-v1", "value": {}})
            registry.validate(
                {"schema_version": "probe-v1", "value": {"name": "Exakt"}}
            )

        cyclic = {
            "first": {"$ref": "#/$defs/second"},
            "second": {"$ref": "#/$defs/first"},
        }
        with tempfile.TemporaryDirectory(prefix="exakt-contract-cycle-") as temp_dir:
            write_probe_schema(temp_dir, {"$ref": "#/$defs/first"}, cyclic)
            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError, "cyclic"
            ):
                self.contracts.ContractRegistry(temp_dir)

    def test_malformed_root_definitions_fail_before_reference_resolution(self):
        with tempfile.TemporaryDirectory(prefix="exakt-contract-defs-") as temp_dir:
            write_probe_schema(temp_dir, {"$ref": "#/$defs/d0"})
            schema_path = Path(temp_dir) / "probe-v1.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema["$defs"] = None
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError,
                r"\$defs must be an object",
            ):
                self.contracts.ContractRegistry(temp_dir)

    def test_excessive_acyclic_reference_chains_fail_with_a_resource_error(self):
        definitions = {
            f"d{index}": {"$ref": f"#/$defs/d{index + 1}"}
            for index in range(1100)
        }
        definitions["d1100"] = {"type": "string"}
        with tempfile.TemporaryDirectory(prefix="exakt-contract-ref-limit-") as temp_dir:
            write_probe_schema(
                temp_dir,
                {"$ref": "#/$defs/d0"},
                definitions,
            )
            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError,
                "reference hop limit",
            ):
                self.contracts.ContractRegistry(temp_dir)

    def test_validator_reads_contract_rules_from_schema_documents(self):
        with tempfile.TemporaryDirectory(prefix="exakt-contract-schema-") as temp_dir:
            copied_root = Path(temp_dir) / "schemas"
            shutil.copytree(SCHEMA_ROOT, copied_root)
            approval_path = copied_root / "approval-v1.json"
            schema = json.loads(approval_path.read_text(encoding="utf-8"))
            schema["properties"]["approval_id"]["const"] = "only-this-id"
            approval_path.write_text(json.dumps(schema), encoding="utf-8")

            registry = self.contracts.ContractRegistry(copied_root)
            with self.assertRaisesRegex(
                self.contracts.ContractError,
                r"\$\.approval_id: expected constant",
            ):
                registry.validate(
                    self.examples["approval-v1.json"],
                    "approval-v1",
                )

    def test_keyword_type_applicability_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="exakt-contract-keyword-") as temp_dir:
            copied_root = Path(temp_dir) / "schemas"
            shutil.copytree(SCHEMA_ROOT, copied_root)
            schema_path = copied_root / "approval-v1.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema["properties"]["approval_id"]["minItems"] = 1
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError,
                "minItems.*string schema",
            ):
                self.contracts.ContractRegistry(copied_root)

    def test_actual_unsupported_validation_keywords_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="exakt-contract-unsupported-") as temp_dir:
            write_probe_schema(
                temp_dir,
                {"type": "string", "maxLength": 10},
            )
            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError,
                "unsupported schema keywords: maxLength",
            ):
                self.contracts.ContractRegistry(temp_dir)

    def test_regex_resource_errors_fail_as_schema_definition_errors(self):
        with tempfile.TemporaryDirectory(prefix="exakt-contract-regex-") as temp_dir:
            write_probe_schema(
                temp_dir,
                {
                    "type": "string",
                    "pattern": "a{999999999999999999999999999999999999999999}",
                },
            )
            with self.assertRaisesRegex(
                self.contracts.SchemaDefinitionError,
                "pattern is invalid",
            ):
                self.contracts.ContractRegistry(temp_dir)

    def test_large_length_bounds_never_leak_integer_rendering_errors(self):
        previous_limit = sys.get_int_max_str_digits()
        temp_roots = []
        try:
            sys.set_int_max_str_digits(0)
            huge_bound = 10 ** 5000
            schemas_and_values = (
                (
                    {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": huge_bound,
                    },
                    [],
                ),
                ({"type": "string", "minLength": huge_bound}, ""),
            )
            for value_schema, value in schemas_and_values:
                temp_root = tempfile.TemporaryDirectory(
                    prefix="exakt-contract-bound-"
                )
                temp_roots.append((temp_root, value))
                write_probe_schema(temp_root.name, value_schema)
        finally:
            sys.set_int_max_str_digits(previous_limit)

        try:
            for temp_root, value in temp_roots:
                registry = self.contracts.ContractRegistry(temp_root.name)
                with self.assertRaisesRegex(
                    self.contracts.ContractError,
                    "expected at least",
                ):
                    registry.validate(
                        {"schema_version": "probe-v1", "value": value}
                    )
        finally:
            for temp_root, _ in temp_roots:
                temp_root.cleanup()

    def test_sha256_patterns_reject_a_trailing_newline(self):
        document = copy.deepcopy(self.examples["approval-v1.json"])
        document["subject_digest"] += "\n"
        with self.assertRaisesRegex(
            self.contracts.ContractError,
            "does not match pattern",
        ):
            self.registry.validate(document, "approval-v1")

    def test_malformed_duplicate_key_and_non_object_documents_are_rejected(self):
        for payload in (
            "{not json}",
            '{"schema_version":"approval-v1","schema_version":"approval-v1"}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(self.contracts.ContractError):
                    self.contracts.loads_json_document(payload)

        with self.assertRaisesRegex(
            self.contracts.ContractError,
            r"\$: expected object",
        ):
            self.registry.validate([], "approval-v1")


class ContractCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            (sys.executable, str(CLI_PATH), *map(str, args)),
            cwd=PACKAGE_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_cli_validates_a_real_fixture(self):
        result = self.run_cli(
            "--schema",
            "approval-v1",
            FIXTURE_ROOT / "valid-live-user-approval-shape.json",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "valid: approval-v1.json\n",
            result.stdout,
        )
        self.assertEqual("", result.stderr)

    def test_cli_reports_path_aware_validation_errors_deterministically(self):
        args = (
            "--schema",
            "approval-v1",
            FIXTURE_ROOT / "invalid-agent-prose-approval.json",
        )
        first = self.run_cli(*args)
        second = self.run_cli(*args)
        self.assertEqual(1, first.returncode)
        self.assertEqual(first.stderr, second.stderr)
        self.assertIn(
            "$.authority.authority_kind:",
            first.stderr,
        )
        self.assertEqual("", first.stdout)

    def test_cli_rejects_unknown_schema_and_malformed_json(self):
        unknown = self.run_cli(
            "--schema",
            "approval-v99",
            FIXTURE_ROOT / "valid-live-user-approval-shape.json",
        )
        self.assertEqual(1, unknown.returncode)
        self.assertIn("unknown schema", unknown.stderr)

        malformed = self.run_cli(
            "--schema",
            "approval-v1",
            FIXTURE_ROOT / "malformed.json",
        )
        self.assertEqual(1, malformed.returncode)
        self.assertIn("invalid JSON", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
