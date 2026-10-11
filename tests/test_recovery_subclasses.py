"""Benign finite-model regressions; these tests perform no POSIX syscalls."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from checker import analyze_model
from engine import build_trace, scaling_operations, strip_build_metrics
from fallback import fixed_candidate_model, formula_trace, publication_fallback_model
from medium import materialize, recover
from model import admit_trace
from oracle import exhaustive_model
from sampling import sample_cuts, cut_metadata
from selector_bridge import boundary_trace, boundary_cut


def legacy_root_prefix_cuts(model, count=16):
    """The exhausted first branch of the old sampler for this regression."""
    cuts = [[]]
    seen = {()}
    for r in model.roots:
        cut = tuple(sorted(model.closure_ids(r)))
        if len(cut) == model.n or cut in seen:
            continue
        seen.add(cut)
        cuts.append(list(cut))
        if len(cuts) == count:
            return cuts
    raise ValueError("regression needs enough distinct root closures")


def late_cut_regression():
    """Corrupt a materialized selected manifest only after prefix 400.

    This injected-image regression checks coverage sensitivity; it is not a
    legal-cut counterexample of the unmodified safe trace or a device crash.
    """
    model = admit_trace(strip_build_metrics(build_trace(
        scaling_operations(512, "unique", 20000), batch_size=4)))
    old = legacy_root_prefix_cuts(model)
    new = sample_cuts(model, 16, 20515)

    def detects(cuts):
        detections = 0
        for cut in cuts:
            image = materialize(model, cut)
            selected = max(image["roots"], key=lambda r: r["event"], default=None)
            if selected is not None and selected["prefix"] >= 400:
                image["slots"].pop(str(selected["manifest_ref"]["slot"]))
                detections += not recover(image)["ok"]
        return detections

    return {"legacy_max_prefix": max(cut_metadata(model, c)["selected_root_prefix"] for c in old),
            "stratified_max_prefix": max(cut_metadata(model, c)["selected_root_prefix"] for c in new),
            "legacy_late_fault_detections": detects(old),
            "stratified_late_fault_detections": detects(new),
            "fault_scope": "injected image missing manifest at selected prefix >=400"}


class RecoverySubclassTests(unittest.TestCase):
    def test_boundary_projection_exact_named_prefixes(self):
        for protocol in ("ordered", "unsafe"):
            model = admit_trace(boundary_trace(protocol))
            n = 4 if protocol == "ordered" else 3
            for after in range(n + 2):
                cut = boundary_cut(protocol, after)
                self.assertTrue(model.is_legal_cut(cut))
                outcome = recover(materialize(model, cut))
                self.assertEqual(outcome["ok"], protocol == "ordered" or after not in (1, 2))
                if outcome["ok"]:
                    payload = b"A" * 32 if after < 3 else b"B" * 32
                    self.assertEqual(outcome["objects_hex"], {"object": payload.hex()})
            self.assertEqual(analyze_model(model)["safe"], protocol == "ordered")

    def test_prepared_manifest_is_not_published_root(self):
        model = admit_trace(boundary_trace("ordered"))
        cut = boundary_cut("ordered", 2)
        self.assertEqual(model.events[4]["role"], "manifest")
        self.assertEqual(recover(materialize(model, cut))["root_event"], 2)
        self.assertEqual(recover(materialize(model, boundary_cut("ordered", 3)))["root_event"], 5)

    def test_publication_fallback_matches_exact_small_protocols(self):
        for protocol in ("ordered", "unsafe"):
            trace = boundary_trace(protocol)
            for anchors in ([], [2], [len(trace["events"]) - 1]):
                trace["acknowledgements"] = [{"issued": len(trace["events"]), "prefix": 1, "anchors": anchors}]
                model = admit_trace(trace)
                result = publication_fallback_model(model)
                self.assertEqual(result["safe"], exhaustive_model(model, fallback=True)["safe"])
                self.assertEqual(result["safe"], fixed_candidate_model(model)["safe"])
                self.assertIsNone(result["minimum_bad_cut"])

    def test_publication_precondition_is_not_general_unsafety(self):
        model = admit_trace(formula_trace([(1, 2, 3)]))
        result = publication_fallback_model(model)
        self.assertEqual(result["status"], "not-applicable")
        self.assertIsNone(result["safe"])

    def test_worklist_propagates_root_to_writer_dependency(self):
        # In ordered, root 5 cannot be killed. Its removal also removes writer
        # 6, the only killer of root 2; root 2 is then removed as well.
        model = admit_trace(boundary_trace("ordered"))
        result = publication_fallback_model(model)
        self.assertTrue(result["safe"])
        self.assertEqual(result["acknowledgement_checks"][0]["greatest_fault_cut"], [0, 1])

    def test_multiple_acknowledgements_and_zero_prefix(self):
        trace = boundary_trace("ordered")
        trace["acknowledgements"].append({"issued": len(trace["events"]), "prefix": 2, "anchors": []})
        result = publication_fallback_model(admit_trace(trace))
        self.assertFalse(result["safe"])
        self.assertEqual(result["ack_index"], 1)
        empty = {"schema": trace["schema"], "events": [], "operations": [],
                 "acknowledgements": [{"issued": 0, "prefix": 0, "anchors": []}]}
        self.assertTrue(publication_fallback_model(admit_trace(empty))["safe"])
        no_root = copy.deepcopy(trace)
        no_root["events"] = []
        no_root["acknowledgements"] = [{"issued": 0, "prefix": 1, "anchors": []}]
        self.assertFalse(publication_fallback_model(admit_trace(no_root))["safe"])

    def test_512_history_all_quarters_and_late_regression(self):
        result = late_cut_regression()
        self.assertEqual(result["legacy_max_prefix"], 60)
        self.assertEqual(result["legacy_late_fault_detections"], 0)
        self.assertGreaterEqual(result["stratified_max_prefix"], 508)
        self.assertGreater(result["stratified_late_fault_detections"], 0)
        for family in ("shared", "unique", "churn"):
            model = admit_trace(strip_build_metrics(build_trace(
                scaling_operations(512, family, 20000), batch_size=4)))
            cuts = sample_cuts(model, 16, 20515)
            self.assertEqual(len({tuple(c) for c in cuts}), 16)
            self.assertEqual(cuts, sample_cuts(model, 16, 20515))
            self.assertTrue(all(model.is_legal_cut(c) and len(c) < model.n for c in cuts))
            prefixes = [cut_metadata(model, c)["selected_root_prefix"] for c in cuts]
            self.assertTrue(all(any(lo <= p <= hi for p in prefixes)
                                for lo, hi in ((1, 128), (129, 256), (257, 384), (385, 512))))

    def test_sampling_boundaries(self):
        model = admit_trace(boundary_trace("ordered"))
        self.assertEqual(sample_cuts(model, 0, 0), [])
        with self.assertRaises(ValueError):
            sample_cuts(model, -1, 0)
        with self.assertRaises(RuntimeError):
            sample_cuts(model, 100, 0)

    def test_greatest_witness_does_not_claim_minimality(self):
        trace = boundary_trace("unsafe")
        trace["acknowledgements"] = [{"issued": 6, "prefix": 1, "anchors": []}]
        model = admit_trace(trace)
        result = publication_fallback_model(model)
        self.assertEqual(result["bad_cut"], [0, 1, 2, 3, 4])
        self.assertEqual(exhaustive_model(model, fallback=True)["minimum_bad_cut"], [])
        self.assertEqual(result["witness_kind"], "greatest-not-minimum")
        self.assertIsNone(result["minimum_bad_cut"])

    def test_publication_fallback_cli_statuses(self):
        cli = Path(__file__).resolve().parents[1] / "src" / "cli.py"
        cases = [(boundary_trace("ordered"), "safe", 0),
                 (boundary_trace("unsafe"), "unsafe", 1),
                 (formula_trace([(1, 2, 3)]), "not-applicable", 2)]
        with tempfile.TemporaryDirectory(prefix="subclass-cli-") as directory:
            path = Path(directory) / "trace.json"
            for trace, status, code in cases:
                path.write_text(json.dumps(trace), encoding="utf-8")
                proc = subprocess.run([sys.executable, "-B", str(cli),
                    "fallback-publication", str(path)], capture_output=True,
                    text=True, timeout=20, check=False)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["status"], status)


if __name__ == "__main__":
    unittest.main()
