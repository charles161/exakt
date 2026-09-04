import re
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PACKAGE_ROOT / "skills/exakt"
SKILL = SKILL_ROOT / "SKILL.md"
CONTRACT = SKILL_ROOT / "references/clarity-and-proof.md"


def assert_semantic_contract(test_case, contract):
    normative_patterns = (
        r"Ask \*\*one consequential question\*\* only when",
        r"A \*\*blocking conflict\*\* stops the affected planning or implementation",
        r"confirm it \*\*fails for the intended reason\*\*",
        r"Each item below invalidates the proof and blocks milestone closure",
        r"Run the \*\*test-legitimacy gate\*\* and a fresh falsification",
        r"Commit only when the user has authorized commits for the exact scope",
        r"\*\*mark dependent tasks and proof stale\*\* by following trace links",
    )
    for pattern in normative_patterns:
        test_case.assertRegex(contract, pattern)

    prohibited_patterns = (
        r"does not stop the affected planning or implementation",
        r"does not invalidate the proof",
        r"skip (?:the )?RED",
        r"commit without (?:user )?authori[sz]ation",
        r"falsification is optional",
    )
    for pattern in prohibited_patterns:
        test_case.assertIsNone(re.search(pattern, contract, flags=re.IGNORECASE))


class PromptContractTests(unittest.TestCase):
    def test_core_prompt_routes_to_clarity_and_proof_contract(self):
        skill = SKILL.read_text(encoding="utf-8")

        self.assertIn("references/clarity-and-proof.md", skill)
        self.assertIn("Intent → Requirement", skill)
        self.assertIn("RED", skill)
        self.assertIn("milestone", skill.casefold())

    def test_canonical_contract_contains_required_gates(self):
        self.assertTrue(CONTRACT.is_file(), "missing canonical prompt contract")
        contract = CONTRACT.read_text(encoding="utf-8")
        required_language = (
            "known / assumed / decided / unknown / conflicted",
            "one consequential question",
            "best guess",
            "decision delta",
            "blocking conflict",
            "Behavior → Invariant → Acceptance criterion → Oracle",
            "at least one concrete counterexample",
            "fails for the intended reason",
            "before-state",
            "test-legitimacy gate",
            "invalidates the proof and blocks milestone closure",
            "weakened assertions",
            "fixture hard-coding",
            "test-environment branches",
            "snapshots or golden files changed",
            "independent falsification",
            "stable M<N>",
            "merge status by stable ID",
            "mark dependent tasks and proof stale",
            "Milestone: M<N> — <outcome>",
            "Completed: <user-visible and technical result>",
            "Covered: <requirement / behavior / invariant / acceptance-criterion IDs>",
            "Changed: <important files or artifacts>",
            "Proved: <fresh evidence IDs, commands, inspections, and outcomes>",
            "Commit: <hash, prepared message, not authorized, or blocked>",
            "Status: <verified | partially_verified | failed | contradicted | blocked | unverified | stale>",
            "Commit only when the user has authorized commits for the exact scope",
            "unrelated changes are already staged",
            "prepare this exact message",
            "Accepts: <acceptance-criterion IDs>",
            "Spec-Digest: sha256:<digest of approved contract projection>",
            "at most eight lines",
            "below 80 non-blank lines",
        )
        for phrase in required_language:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, contract)

        self.assertNotIn("Reject or explicitly justify", contract)
        self.assertNotIn("Use test-first work for behavior changes when practical", SKILL.read_text(encoding="utf-8"))
        assert_semantic_contract(self, contract)

    def test_inverted_critical_gates_fail_semantic_validation(self):
        contract = CONTRACT.read_text(encoding="utf-8")
        mutations = (
            (
                "stops the affected planning or implementation",
                "does not stop the affected planning or implementation",
            ),
            (
                "invalidates the proof and blocks milestone closure",
                "does not invalidate the proof or block milestone closure",
            ),
            (
                "fails for the intended reason",
                "passes eventually, without an intended RED reason",
            ),
            (
                "a fresh falsification before marking",
                "falsification is optional before marking",
            ),
            (
                "Commit only when the user has authorized commits for the exact scope",
                "Commit without user authorization for the exact scope",
            ),
            (
                "mark dependent tasks and proof stale",
                "leave dependent tasks and proof unchanged",
            ),
        )
        for original, replacement in mutations:
            with self.subTest(gate=original):
                mutated = contract.replace(original, replacement, 1)
                self.assertNotEqual(contract, mutated)
                with self.assertRaises(AssertionError):
                    assert_semantic_contract(self, mutated)

    def test_secondary_references_delegate_normative_authority(self):
        for name in (
            "workflows.md",
            "product-mode.md",
            "harness-adapters.md",
            "verification.md",
            "report-interface.md",
        ):
            with self.subTest(reference=name):
                content = (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")
                self.assertIn("[clarity-and-proof.md](clarity-and-proof.md)", content)

    def test_no_runtime_dependency_on_addys_skill_pack(self):
        package = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SKILL_ROOT.rglob("*.md")
            if "docs/superpowers" not in str(path)
        )

        self.assertNotIn("agent-skills:", package)


if __name__ == "__main__":
    unittest.main()
