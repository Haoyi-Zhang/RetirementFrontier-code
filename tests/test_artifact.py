from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from itertools import product

from checker import analyze_model, pair_analyze_model
from engine import (
    build_trace,
    census_template,
    edge_free_trace,
    heldout_operations,
    strip_build_metrics,
    unanchor_first_ack,
    weaken_trace,
)
from fallback import assignment_cut, evaluates_formula, fixed_candidate_model, formula_trace
from medium import erase_slots, materialize, recover
from model import AdmissionError, admit_trace, canonical_manifest, make_ref
from oracle import evaluate_cut, exhaustive_model
from solver import SolverUnavailable, solve_model
from verifier import VerificationError, _parse as verifier_parse, verify_certificate


class ArtifactTests(unittest.TestCase):
    def safe_trace(self):
        return strip_build_metrics(build_trace(heldout_operations(10000), deduplicate=True, batch_size=2))

    def test_01_ordered_trace_admits(self):
        model = admit_trace(self.safe_trace())
        self.assertGreater(model.n, 0)

    def test_02_duplicate_incarnation_rejected(self):
        trace = self.safe_trace()
        data = [e for e in trace["events"] if e["kind"] == "data"]
        data[1]["slot"] = data[0]["slot"]
        data[1]["generation"] = data[0]["generation"]
        with self.assertRaises(AdmissionError):
            admit_trace(trace)

    def test_03_bad_manifest_binding_rejected(self):
        trace = self.safe_trace()
        root = next(e for e in trace["events"] if e["kind"] == "root")
        root["manifest_ref"]["length"] += 1
        with self.assertRaises(AdmissionError):
            admit_trace(trace)

    def test_04_ghost_prefix_rejected(self):
        trace = self.safe_trace()
        root = next(e for e in trace["events"] if e["kind"] == "root")
        root["objects_hex"] = {"fake": "00"}
        with self.assertRaises(AdmissionError):
            admit_trace(trace)

    def test_05_legal_cut_closure(self):
        model = admit_trace(self.safe_trace())
        root = model.roots[-1]
        self.assertTrue(model.is_legal_cut(model.closure_ids(root)))
        self.assertFalse(model.is_legal_cut([root]))

    def test_06_strict_full_recovery(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        result = recover(image)
        self.assertTrue(result["ok"])
        self.assertEqual(result["prefix"], len(model.operations))

    def test_07_erased_manifest_fails_strict(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        latest = model.events[model.roots[-1]]["manifest_ref"]["slot"]
        result = recover(erase_slots(image, [latest]))
        self.assertFalse(result["ok"])

    def test_08_fallback_can_use_older_root(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        latest = model.events[model.roots[-1]]["manifest_ref"]["slot"]
        strict = recover(erase_slots(image, [latest]))
        fallback = recover(erase_slots(image, [latest]), fallback=True)
        self.assertFalse(strict["ok"])
        self.assertTrue(fallback["ok"])
        self.assertLess(fallback["prefix"], len(model.operations))

    def test_09_frontier_safe_matches_oracle(self):
        model = admit_trace(self.safe_trace())
        self.assertTrue(analyze_model(model)["safe"])
        self.assertTrue(exhaustive_model(model)["safe"])

    def test_10_edge_free_unsafe_matches_oracle(self):
        model = admit_trace(edge_free_trace(self.safe_trace()))
        checked = analyze_model(model, minimum=True)
        exact = exhaustive_model(model)
        self.assertFalse(checked["safe"])
        self.assertEqual(checked["safe"], exact["safe"])
        self.assertIsNotNone(checked["minimum_bad_cut"])

    def test_11_unanchored_ack_unsafe(self):
        model = admit_trace(unanchor_first_ack(self.safe_trace()))
        self.assertFalse(analyze_model(model)["safe"])

    def test_12_pair_agrees_small(self):
        for seed in range(10000, 10006):
            model = admit_trace(weaken_trace(build_trace(heldout_operations(seed), deduplicate=True, batch_size=2), seed=seed + 1))
            self.assertEqual(analyze_model(model)["safe"], pair_analyze_model(model)["safe"])

    def test_13_certificate_consumer_accepts(self):
        model = admit_trace(self.safe_trace())
        cert = analyze_model(model)["certificate"]
        self.assertTrue(verify_certificate(model.trace, cert)["accepted"])

    def test_14_certificate_consumer_rejects_mutation(self):
        model = admit_trace(self.safe_trace())
        cert = copy.deepcopy(analyze_model(model)["certificate"])
        cert["publication"] = cert["publication"][:-1]
        with self.assertRaises(VerificationError):
            verify_certificate(model.trace, cert)

    def test_15_census_known_safe_graph(self):
        # Required edges 0,1 -> root 2; manifest 3 -> root 4; root 4 -> free 5.
        pairs = [(i, j) for i in range(6) for j in range(i)]
        required = {(2, 0), (2, 1), (4, 3), (5, 4)}
        mask = sum(1 << pairs.index(pair) for pair in required)
        model = admit_trace(census_template("retired-free", mask))
        self.assertTrue(analyze_model(model)["safe"])
        self.assertTrue(exhaustive_model(model, max_events=6)["safe"])

    def test_16_unretired_census_never_safe_example(self):
        model = admit_trace(census_template("unretired-reuse", (1 << 10) - 1))
        self.assertFalse(analyze_model(model)["safe"])

    def test_17_formula_reduction_truth_table(self):
        clauses = [(1, 2, 3), (-1, -2, -3)]
        model = admit_trace(formula_trace(clauses))
        for assignment in product([False, True], repeat=3):
            outcome = recover(materialize(model, assignment_cut(model, assignment)), fallback=True)
            self.assertEqual(outcome["prefix"] == 0, evaluates_formula(clauses, assignment))

    def test_18_fixed_candidate_finds_violation(self):
        model = admit_trace(formula_trace([(1, 2, 3)]))
        self.assertFalse(fixed_candidate_model(model)["safe"])

    def test_19_fixed_candidate_unknown_budget(self):
        model = admit_trace(formula_trace([(1, 2, 3), (-1, -2, -3)]))
        self.assertIsNone(fixed_candidate_model(model, max_choices=1)["safe"])

    def test_20_solver_agrees_when_available(self):
        try:
            safe = admit_trace(self.safe_trace())
            unsafe = admit_trace(edge_free_trace(self.safe_trace()))
            self.assertTrue(solve_model(safe)["safe"])
            self.assertFalse(solve_model(unsafe)["safe"])
        except SolverUnavailable as exc:
            self.skipTest(str(exc))


    def test_21_consumer_rejects_duplicate_slot_generation(self):
        model = admit_trace(self.safe_trace())
        cert = analyze_model(model)["certificate"]
        trace = copy.deepcopy(model.trace)
        data = [event for event in trace["events"] if event["kind"] == "data"]
        data[1]["slot"] = data[0]["slot"]
        data[1]["generation"] = data[0]["generation"]
        with self.assertRaises(VerificationError):
            verify_certificate(trace, cert)

    def test_22_consumer_rejects_invalid_role(self):
        model = admit_trace(self.safe_trace())
        cert = analyze_model(model)["certificate"]
        trace = copy.deepcopy(model.trace)
        next(event for event in trace["events"] if event["kind"] == "data")["role"] = "unknown"
        with self.assertRaises(VerificationError):
            verify_certificate(trace, cert)

    def test_23_manifest_role_binding_rejected_by_both_parsers(self):
        trace = self.safe_trace()
        root = next(event for event in trace["events"] if event["kind"] == "root")
        key = (
            root["manifest_ref"]["slot"],
            root["manifest_ref"]["generation"],
            root["manifest_ref"]["digest"],
            root["manifest_ref"]["length"],
        )
        allocation = next(
            event for event in trace["events"]
            if event["kind"] == "data"
            and (event["slot"], event["generation"], event["digest"], event["length"]) == key
        )
        allocation["role"] = "chunk"
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_24_complete_object_reconstruction_is_checked(self):
        trace = strip_build_metrics(build_trace([
            {"op": "put", "name": "a", "chunks_hex": ["41"]},
            {"op": "put", "name": "b", "chunks_hex": ["42"]},
        ], deduplicate=True, batch_size=2))
        root = next(event for event in trace["events"] if event["kind"] == "root")
        root["namespace"]["a"] = copy.deepcopy(root["namespace"]["b"])
        manifest = canonical_manifest(root["namespace"])
        manifest_event = next(
            event for event in trace["events"]
            if event["kind"] == "data" and event["slot"] == root["manifest_ref"]["slot"]
        )
        manifest_event["data_hex"] = manifest.hex()
        manifest_event["digest"] = make_ref(manifest_event["slot"], manifest_event["generation"], manifest)["digest"]
        manifest_event["length"] = len(manifest)
        root["manifest_ref"] = make_ref(manifest_event["slot"], manifest_event["generation"], manifest)
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_25_certificate_extra_field_rejected(self):
        model = admit_trace(self.safe_trace())
        cert = copy.deepcopy(analyze_model(model)["certificate"])
        cert["unexpected"] = []
        with self.assertRaises(VerificationError):
            verify_certificate(model.trace, cert)

    def test_26_certificate_boolean_identifier_rejected(self):
        model = admit_trace(self.safe_trace())
        cert = copy.deepcopy(analyze_model(model)["certificate"])
        cert["publication"][0]["root"] = True
        with self.assertRaises(VerificationError):
            verify_certificate(model.trace, cert)

    def test_27_consumer_accepts_uppercase_operation_hex(self):
        trace = strip_build_metrics(build_trace([
            {"op": "put", "name": "x", "chunks_hex": ["AB"]},
        ], deduplicate=True, batch_size=1))
        model = admit_trace(trace)
        cert = analyze_model(model)["certificate"]
        self.assertTrue(verify_certificate(trace, cert)["accepted"])

    def test_28_model_and_consumer_admission_agree_on_mutations(self):
        base = self.safe_trace()
        mutations = []

        def add(label, mutate):
            trace = copy.deepcopy(base)
            mutate(trace)
            mutations.append((label, trace))

        add("operation-not-object", lambda t: t["operations"].__setitem__(0, 7))
        add("empty-name", lambda t: t["operations"][0].__setitem__("name", ""))
        add("chunks-not-list", lambda t: t["operations"][0].__setitem__("chunks_hex", "61"))
        add("bad-operation-hex", lambda t: t["operations"][0].__setitem__("chunks_hex", ["zz"]))
        delete_index = next((i for i, op in enumerate(base["operations"]) if op["op"] == "delete"), None)
        if delete_index is not None:
            add("delete-extra", lambda t, i=delete_index: t["operations"][i].__setitem__("extra", 1))
        add("event-not-object", lambda t: t["events"].__setitem__(0, 7))
        add("wrong-event-id", lambda t: t["events"][0].__setitem__("id", 9))
        add("deps-not-list", lambda t: t["events"][0].__setitem__("deps", 1))
        add("future-dependency", lambda t: t["events"][0].__setitem__("deps", [1]))
        first_data = next(i for i, event in enumerate(base["events"]) if event["kind"] == "data")
        second_data = next(i for i, event in enumerate(base["events"]) if event["kind"] == "data" and i != first_data)
        add("negative-slot", lambda t, i=first_data: t["events"][i].__setitem__("slot", -1))
        add("zero-generation", lambda t, i=first_data: t["events"][i].__setitem__("generation", 0))
        add("bad-digest", lambda t, i=first_data: t["events"][i].__setitem__("digest", "0" * 64))
        add("bad-length", lambda t, i=first_data: t["events"][i].__setitem__("length", 999))
        add("bad-role", lambda t, i=first_data: t["events"][i].__setitem__("role", "bad"))
        add("duplicate-incarnation", lambda t, a=first_data, b=second_data: (
            t["events"][b].__setitem__("slot", t["events"][a]["slot"]),
            t["events"][b].__setitem__("generation", t["events"][a]["generation"]),
        ))
        free_index = next((i for i, event in enumerate(base["events"]) if event["kind"] == "free"), None)
        if free_index is not None:
            add("negative-free-slot", lambda t, i=free_index: t["events"][i].__setitem__("slot", -1))
        root_index = next(
            i for i, event in enumerate(base["events"])
            if event["kind"] == "root" and event["objects_hex"]
        )
        add("root-prefix-high", lambda t, i=root_index: t["events"][i].__setitem__("prefix", len(t["operations"]) + 1))
        add("manifest-ref-extra", lambda t, i=root_index: t["events"][i]["manifest_ref"].__setitem__("extra", 1))
        add("namespace-nonstring-name", lambda t, i=root_index: t["events"][i]["namespace"].__setitem__(1, []))
        add("objects-bad-hex", lambda t, i=root_index: t["events"][i]["objects_hex"].__setitem__(next(iter(t["events"][i]["objects_hex"])), "zz"))
        add("objects-prefix-mismatch", lambda t, i=root_index: t["events"][i].__setitem__("objects_hex", {"wrong": "00"}))
        add("ack-not-object", lambda t: t["acknowledgements"].__setitem__(0, 7))
        add("ack-anchors-not-list", lambda t: t["acknowledgements"][0].__setitem__("anchors", 1))
        add("ack-issued-high", lambda t: t["acknowledgements"][0].__setitem__("issued", len(t["events"]) + 1))
        add("ack-future-anchor", lambda t: t["acknowledgements"][0].__setitem__("anchors", [t["acknowledgements"][0]["issued"]]))

        for label, trace in mutations:
            try:
                admit_trace(trace)
                producer_accepts = True
            except AdmissionError:
                producer_accepts = False
            try:
                verifier_parse(trace)
                consumer_accepts = True
            except VerificationError:
                consumer_accepts = False
            self.assertEqual(producer_accepts, consumer_accepts, label)
            self.assertFalse(producer_accepts, label)

    def test_29_malformed_operation_raises_admission_error(self):
        trace = self.safe_trace()
        trace["operations"][0] = 7
        with self.assertRaises(AdmissionError):
            admit_trace(trace)

    def test_30_nonlist_ack_anchors_raise_controlled_errors(self):
        trace = self.safe_trace()
        trace["acknowledgements"][0]["anchors"] = 1
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_31_oracle_duplicate_cut_preserves_ack_semantics(self):
        trace = self.safe_trace()
        first_root = next(event["id"] for event in trace["events"] if event["kind"] == "root")
        trace["acknowledgements"] = [{
            "issued": first_root + 1,
            "prefix": len(trace["operations"]),
            "anchors": [first_root],
        }]
        model = admit_trace(trace)
        cut = sorted(model.closure_ids(first_root))
        verdict = evaluate_cut(model, cut + [first_root])
        self.assertFalse(verdict["safe"])
        self.assertEqual(verdict["reason"], "acknowledgement")

    def test_32_recovery_rejects_malformed_image_without_throwing(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        image["roots"][0]["manifest_ref"]["extra"] = 1
        result = recover(image)
        self.assertFalse(result["ok"])
        self.assertIn("reference fields", result["reason"])

    def test_33_recovery_checks_data_role(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        latest = max(image["roots"], key=lambda root: root["event"])
        slot = str(latest["manifest_ref"]["slot"])
        image["slots"][slot]["role"] = "chunk"
        result = recover(image)
        self.assertFalse(result["ok"])
        self.assertIn("wrong data role", result["reason"])

    def test_34_duplicate_certificate_row_rejected(self):
        model = admit_trace(self.safe_trace())
        cert = copy.deepcopy(analyze_model(model)["certificate"])
        cert["publication"].append(copy.deepcopy(cert["publication"][0]))
        with self.assertRaises(VerificationError):
            verify_certificate(model.trace, cert)


    def test_35_fixed_candidate_matches_fallback_oracle(self):
        operations = [
            {"op": "put", "name": "a", "chunks_hex": ["41"]},
            {"op": "put", "name": "b", "chunks_hex": ["42"]},
            {"op": "put", "name": "a", "chunks_hex": ["43"]},
            {"op": "put", "name": "b", "chunks_hex": ["44"]},
        ]
        base = strip_build_metrics(build_trace(operations, deduplicate=True, batch_size=2))
        for trace in (base, edge_free_trace(base), unanchor_first_ack(base)):
            model = admit_trace(trace)
            fixed = fixed_candidate_model(model, max_choices=1_000_000)
            exact = exhaustive_model(model, fallback=True, max_events=22)
            self.assertIsNotNone(fixed["safe"])
            self.assertEqual(fixed["safe"], exact["safe"])

    def test_36_noncanonical_numeric_slot_is_rejected_before_certification(self):
        trace = strip_build_metrics(build_trace([
            {"op": "put", "name": "x", "chunks_hex": ["41"]},
        ], deduplicate=True, batch_size=1))
        chunk = next(event for event in trace["events"] if event["kind"] == "data" and event["role"] == "chunk")
        old_slot = chunk["slot"]
        chunk["slot"] = "00"
        for root in (event for event in trace["events"] if event["kind"] == "root"):
            for refs in root["namespace"].values():
                for ref in refs:
                    if ref["slot"] == old_slot:
                        ref["slot"] = "00"
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_37_boolean_and_float_identifiers_are_rejected(self):
        for value in (True, 0.0, "0"):
            trace = self.safe_trace()
            trace["events"][0]["id"] = value
            with self.assertRaises(AdmissionError):
                admit_trace(trace)
            with self.assertRaises(VerificationError):
                verifier_parse(trace)

    def test_38_recovery_validates_unreachable_slot_records(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        image["slots"]["999"] = {
            "kind": "data",
            "event": 999,
            "slot": 999,
            "generation": 1,
            "digest": "0" * 64,
            "length": 1,
            "data_hex": "41",
            "role": "chunk",
        }
        result = recover(image)
        self.assertFalse(result["ok"])
        self.assertIn("length/digest", result["reason"])

    def test_39_recovery_rejects_nonmonotone_root_prefixes(self):
        model = admit_trace(self.safe_trace())
        image = materialize(model, range(model.n))
        if len(image["roots"]) < 2:
            self.skipTest("fixture has fewer than two roots")
        roots = sorted(image["roots"], key=lambda root: root["event"])
        roots[-1]["prefix"] = roots[0]["prefix"]
        result = recover(image)
        self.assertFalse(result["ok"])
        self.assertIn("root prefixes", result["reason"])

    def test_40_generated_safe_traces_recover_after_certification(self):
        for seed in range(10100, 10132):
            trace = strip_build_metrics(build_trace(
                heldout_operations(seed), deduplicate=bool(seed & 1), batch_size=1 + seed % 3
            ))
            model = admit_trace(trace)
            checked = analyze_model(model)
            self.assertTrue(checked["safe"], seed)
            self.assertTrue(verify_certificate(model.trace, checked["certificate"])["accepted"], seed)
            outcome = recover(materialize(model, range(model.n)))
            self.assertTrue(outcome["ok"], (seed, outcome))
            self.assertEqual(outcome["prefix"], len(model.operations), seed)

    def test_41_operation_field_and_hex_coercions_are_rejected(self):
        mutations = []
        trace = self.safe_trace()
        trace["operations"][0]["extra"] = 1
        mutations.append(trace)
        trace = self.safe_trace()
        put = next(op for op in trace["operations"] if op["op"] == "put")
        put["chunks_hex"][0] = "4 1"
        mutations.append(trace)
        trace = self.safe_trace()
        put = next(op for op in trace["operations"] if op["op"] == "put")
        put["chunks_hex"][0] = 41
        mutations.append(trace)
        for mutated in mutations:
            with self.assertRaises(AdmissionError):
                admit_trace(mutated)
            with self.assertRaises(VerificationError):
                verifier_parse(mutated)


    def test_42_trace_extra_field_is_rejected_by_both_parsers(self):
        trace = self.safe_trace()
        trace["extra"] = 1
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_43_event_extra_field_is_rejected_by_both_parsers(self):
        trace = self.safe_trace()
        trace["events"][0]["extra"] = 1
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)

    def test_44_acknowledgement_extra_field_is_rejected_by_both_parsers(self):
        trace = self.safe_trace()
        trace["acknowledgements"][0]["extra"] = 1
        with self.assertRaises(AdmissionError):
            admit_trace(trace)
        with self.assertRaises(VerificationError):
            verifier_parse(trace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
