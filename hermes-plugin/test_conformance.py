"""Runs conformance/vectors.json against the Python evaluator.

The same file is run by packages/governance-pdp/__tests__/conformance.test.ts.
If the two ever disagree, one of them is wrong and hosts are being governed
differently — the situation this suite exists to make impossible.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pdp import evaluate_against_bundle

VECTOR_PATH = Path(__file__).resolve().parent.parent / "conformance" / "vectors.json"
SUPPORTED_SCHEMA_VERSION = 1


def load_vectors() -> dict:
    with VECTOR_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class ConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = load_vectors()

    def test_schema_version(self) -> None:
        self.assertEqual(self.vectors["schema_version"], SUPPORTED_SCHEMA_VERSION)

    def test_no_duplicate_case_ids(self) -> None:
        ids = [case["id"] for case in self.vectors["cases"]]
        self.assertEqual(len(set(ids)), len(ids))

    def test_cases(self) -> None:
        bundles = self.vectors["bundles"]
        for case in self.vectors["cases"]:
            with self.subTest(case=case["id"]):
                payload = bundles.get(case["bundle"])
                self.assertIsNotNone(
                    payload,
                    f'vectors.json references unknown bundle "{case["bundle"]}"',
                )
                result = evaluate_against_bundle(
                    payload,
                    case["tool_name"],
                    pdp_state=case["pdp_state"],
                )
                actual = {
                    "decision": result["decision"],
                    "tier": result["tier"],
                    "requires_approval": result["requires_approval"],
                    "reasons": result["reasons"],
                    "category": result["category"],
                }
                self.assertEqual(actual, case["expect"], msg=case["why"])


if __name__ == "__main__":
    unittest.main()
