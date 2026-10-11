"""Synthetic result-consistency regressions, not new crash observations."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "scientific_result_checker", ROOT / "scripts/check_scientific_invariants.py")
checker = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(checker)


class ScientificInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use the complete retained evidence for all unrelated obligations.
        # Cache it once; every mutation below is confined to fresh copies.
        cls.documents = {
            p: checker.read(p) for p in (
                ROOT / "results/summary/summary.json",
                ROOT / "results/summary/execution-mode.json",
                ROOT / "results/pilots/unit-tests.json",
                ROOT / "results/posix-smoke.json",
            )
        }
        cls.primary = {
            name: checker.rows(ROOT, name) for name in (
                "census/graphs.jsonl", "heldout/variants.jsonl",
                "fallback/truth-table.jsonl", "fallback/fixed-candidate.jsonl",
                "scaling/policy-metrics.jsonl", "public/policy-metrics.jsonl",
                "scaling/recovery.jsonl", "public/recovery.jsonl",
            )
        }

    def evidence(self, total):
        documents = dict(self.documents)
        summary_path = ROOT / "results/summary/summary.json"
        documents[summary_path] = copy.deepcopy(documents[summary_path])
        primary = dict(self.primary)
        names = ("scaling/recovery.jsonl", "public/recovery.jsonl")
        count = sum(len(primary[name]) for name in names)
        quotient, remainder = divmod(total, count)
        index = 0
        for name in names:
            primary[name] = [dict(row) for row in primary[name]]
            for row in primary[name]:
                row["unreachable_slots"] = quotient + int(index < remainder)
                index += 1
        documents[summary_path]["recovery"]["unreachable_slots_total"] = total
        return documents, primary

    def report(self, documents, primary):
        # Exercise the real complete checker, replacing only its file readers.
        with patch.object(checker, "read", side_effect=documents.__getitem__), \
             patch.object(checker, "rows", side_effect=lambda root, name: primary[name]):
            return checker.check(ROOT)

    def test_changed_valid_occupancy_is_not_a_source_era_constant(self):
        retained = self.documents[ROOT / "results/summary/summary.json"]
        for total in (retained["recovery"]["unreachable_slots_total"], 5382, 5383):
            with self.subTest(total=total):
                documents, primary = self.evidence(total)
                self.assertEqual(self.report(documents, primary)["errors"], [])

    def test_wrong_aggregate_is_rejected(self):
        documents, primary = self.evidence(5382)
        documents[ROOT / "results/summary/summary.json"]["recovery"]["unreachable_slots_total"] = 3765
        self.assertIn("recovery count and occupancy", self.report(documents, primary)["errors"])

    def test_invalid_primary_occupancy_is_rejected_even_when_sum_matches(self):
        for invalid in (-1, True, 7.0):
            with self.subTest(invalid=invalid):
                documents, primary = self.evidence(5382)
                primary["scaling/recovery.jsonl"][0]["unreachable_slots"] = invalid
                total = sum(row["unreachable_slots"] for name in
                            ("scaling/recovery.jsonl", "public/recovery.jsonl")
                            for row in primary[name])
                # Keep a valid integer aggregate so only the primary-row
                # domain check can reject the integer-valued float case.
                documents[ROOT / "results/summary/summary.json"]["recovery"]["unreachable_slots_total"] = int(total)
                self.assertIn("recovery count and occupancy", self.report(documents, primary)["errors"])

    def test_matching_float_total_is_rejected(self):
        documents, primary = self.evidence(5382)
        documents[ROOT / "results/summary/summary.json"]["recovery"]["unreachable_slots_total"] = 5382.0
        self.assertIn("recovery count and occupancy", self.report(documents, primary)["errors"])

    def test_missing_primary_row_is_rejected_even_when_sum_matches(self):
        documents, primary = self.evidence(5382)
        removed = primary["scaling/recovery.jsonl"].pop()
        documents[ROOT / "results/summary/summary.json"]["recovery"]["unreachable_slots_total"] -= removed["unreachable_slots"]
        self.assertIn("recovery count and occupancy", self.report(documents, primary)["errors"])


if __name__ == "__main__":
    unittest.main()
