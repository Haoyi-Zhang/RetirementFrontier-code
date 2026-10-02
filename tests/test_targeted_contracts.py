"""Directed admission, witness, cost, and command-line contract regressions."""
from __future__ import annotations

from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from checker import analyze_model
from engine import build_trace, strip_build_metrics
from fallback import fixed_candidate_model
from medium import materialize, recover
from model import AdmissionError, admit_trace
from oracle import exhaustive_model
from targeted_cases import (ack, cases, canonical_witness_trace, data, late_chunk,
                            late_manifest, missing_chunk, missing_manifest,
                            mixed_multi_ack, no_root_multi_ack, ref, root, trace)
from verifier import VerificationError, _parse, verify_certificate


class TargetedContractTests(unittest.TestCase):
    def reject_both(self, t):
        with self.assertRaises(AdmissionError): admit_trace(t)
        with self.assertRaises(VerificationError): _parse(t)

    def test_late_manifest_wrong_role_rejected_by_both_parsers(self):
        self.reject_both(late_manifest("chunk"))

    def test_late_chunk_wrong_role_rejected_by_both_parsers(self):
        self.reject_both(late_chunk("manifest"))

    def test_late_chunk_complete_object_bytes_rechecked(self):
        self.reject_both(late_chunk(allocation_bytes=b"b"))

    def test_noncanonical_late_manifest_is_malformed_not_missing(self):
        self.reject_both(cases()["late-manifest-noncanonical"])

    def test_legitimate_forward_allocations_admitted_not_publication_safe(self):
        for t in (late_manifest("manifest"), late_chunk()):
            with self.subTest(events=len(t["events"])):
                m = admit_trace(t); _parse(t)
                self.assertFalse(analyze_model(m, minimum=True)["safe"])
                self.assertTrue(fixed_candidate_model(m)["safe"])
                self.assertTrue(exhaustive_model(m, fallback=True)["safe"])
                self.assertFalse(exhaustive_model(m)["safe"])

    def test_genuinely_missing_allocations_remain_admitted_unsafe(self):
        for t in (missing_manifest(), missing_chunk()):
            m = admit_trace(t); _parse(t)
            result = fixed_candidate_model(m)
            oracle = exhaustive_model(m, fallback=True)
            self.assertFalse(result["safe"])
            self.assertEqual(result["minimum_bad_cut"], oracle["minimum_bad_cut"])

    def test_present_wrong_role_not_hidden_by_another_missing_reference(self):
        ns = {"x": [ref(8, b"m"), ref(1, b"a")]}
        payload = json.dumps(ns, sort_keys=True, separators=(",", ":")).encode()
        t = trace([{"op":"put", "name":"x", "chunks_hex":[b"ma".hex()]}],
                  [data(0, 0, payload, "manifest"),
                   root(1, 1, ns, ref(0, payload), {"x": b"ma".hex()}, [0]),
                   data(2, 1, b"a", "manifest")], [ack(3,1,[0,1,2])])
        self.reject_both(t)

    def test_no_root_multiple_ack_minimum_is_empty_in_any_order(self):
        t = no_root_multi_ack()
        for order in permutations(t["acknowledgements"]):
            t["acknowledgements"] = list(order)
            m = admit_trace(t)
            self.assertEqual(fixed_candidate_model(m)["minimum_bad_cut"], [])
            self.assertEqual(exhaustive_model(m, True)["minimum_bad_cut"], [])

    def test_no_candidate_ack_does_not_overwrite_better_existing_candidate(self):
        t = mixed_multi_ack()
        for order in permutations(t["acknowledgements"]):
            t["acknowledgements"] = list(order); m = admit_trace(t)
            self.assertEqual(fixed_candidate_model(m)["minimum_bad_cut"], [])
            self.assertEqual(exhaustive_model(m, True)["minimum_bad_cut"], [])

    def test_budget_unknown_never_labels_incumbent_minimum(self):
        t = mixed_multi_ack()
        for order in permutations(t["acknowledgements"]):
            t["acknowledgements"] = list(order)
            result = fixed_candidate_model(admit_trace(t), max_choices=0)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["safe"])
            self.assertIsNone(result["minimum_bad_cut"])
        t["acknowledgements"].sort(key=lambda a: -a["prefix"])
        result = fixed_candidate_model(admit_trace(t), max_choices=0)
        self.assertEqual(result["bad_cut"], [0])
        self.assertEqual(fixed_candidate_model(admit_trace(t), 100)["minimum_bad_cut"], [])

    def test_invalid_choice_budget_is_controlled_input_error(self):
        m = admit_trace(no_root_multi_ack())
        for limit in (-1, True, 0.5, "1", None, [], {}):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                fixed_candidate_model(m, max_choices=limit)

    def test_multiple_valid_roots_require_canonical_maximum_witnesses(self):
        t = canonical_witness_trace(); m = admit_trace(t)
        cert = analyze_model(m)["certificate"]
        self.assertEqual(cert["retirement"], [{"writer":6,"guard_root":5}])
        self.assertEqual(cert["acknowledgement"], [{"ack_index":0,"root":5}, {"ack_index":1,"root":-1}])
        self.assertTrue(verify_certificate(t, cert)["accepted"])
        self.assertTrue(exhaustive_model(m)["safe"])
        # Root 3 is forced and satisfies both inequalities but is not canonical.
        self.assertIn(3, m.closure_ids(6)); self.assertGreater(3, 1)
        for part, field in (("retirement", "guard_root"), ("acknowledgement", "root")):
            changed = deepcopy(cert); changed[part][0][field] = 3
            with self.assertRaises(VerificationError): verify_certificate(t, changed)
        changed = deepcopy(cert); changed["publication"].reverse()
        with self.assertRaises(VerificationError): verify_certificate(t, changed)

    def test_roles_and_kinds_are_strings_before_membership(self):
        for value in ([], {}, None, 0, True):
            for field in ("role", "kind"):
                t = late_manifest("manifest"); t["events"][1][field] = value
                self.reject_both(t)
                img = materialize(admit_trace(late_manifest("manifest")), [0, 1])
                img["slots"]["0"][field] = value
                result = recover(img)
                self.assertFalse(result["ok"]); self.assertEqual(result["status"], "malformed")

    def cli(self, command, value, *args):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"input.json"; path.write_text(json.dumps(value))
            cp = subprocess.run([sys.executable, str(ROOT/"src/cli.py"), command,
                                 str(path), *args], cwd=ROOT, capture_output=True,
                                text=True, timeout=20)
            self.assertNotIn("TypeError", cp.stderr)
            self.assertNotIn("Traceback", cp.stderr)
            return cp.returncode, json.loads(cp.stdout)

    def test_cli_malformed_trace_role_kind_do_not_escape(self):
        cert = analyze_model(admit_trace(canonical_witness_trace()))["certificate"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"certificate.json"; path.write_text(json.dumps(cert))
            for value in ([], {}, None):
                for field in ("role", "kind"):
                    t = late_manifest("manifest"); t["events"][1][field] = value
                    for command in ("certify", "fallback", "verify"):
                        args = [str(path)] if command == "verify" else []
                        rc, result = self.cli(command, t, *args)
                        self.assertEqual(rc, 2)
                        self.assertEqual(result["status"], "malformed")

    def test_cli_recovery_format_errors_are_distinct_from_unsafe(self):
        m = admit_trace(late_manifest("manifest"))
        for value in ([], {}, None):
            for field in ("role", "kind"):
                img = materialize(m, [0,1]); img["slots"]["0"][field] = value
                for args in ([], ["--fallback"]):
                    rc, result = self.cli("recover", img, *args)
                    self.assertEqual((rc, result["status"]), (2,"malformed"))
        rc, result = self.cli("recover", materialize(m,[0]))
        self.assertEqual((rc, result["status"]), (1,"unsafe"))

    def test_cli_safe_unsafe_unknown_and_certificate_rejection(self):
        rc, result = self.cli("fallback", late_manifest("manifest"))
        self.assertEqual((rc, result["status"]), (0,"safe"))
        rc, result = self.cli("fallback", missing_manifest())
        self.assertEqual((rc, result["status"]), (1,"unsafe"))
        rc, result = self.cli("fallback", late_manifest("manifest"), "--max-choices", "0")
        self.assertEqual((rc, result["status"]), (2,"unknown"))
        self.assertIsNone(result["minimum_bad_cut"])
        rc, result = self.cli("certify", late_manifest("manifest"), "--minimum")
        self.assertEqual((rc, result["status"]), (1,"unsafe"))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"bad-cert.json";path.write_text('{}')
            rc,result=self.cli("verify",canonical_witness_trace(),str(path))
            self.assertEqual((rc,result["status"]),(2,"rejected"))

    def test_declared_descriptor_plus_raw_payload_cost_matches_generator(self):
        operations=[{"op":"put","name":"x","chunks_hex":[b"ab".hex()]},
                    {"op":"delete","name":"x"}]
        t=build_trace(operations, deduplicate=True, batch_size=1)
        totals={"data":0,"root":0,"free":0}
        for e in t["events"]:
            fields={"data":["kind","slot","generation","digest","length","role"],
                    "root":["kind","prefix","manifest_ref"],
                    "free":["kind","slot","generation"]}[e["kind"]]
            descriptor={k:e[k] for k in fields}
            payload=bytes.fromhex(e["data_hex"]) if e["kind"]=="data" else b""
            totals[e["kind"]]+=len(json.dumps(descriptor,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())+1+len(payload)
            self.assertNotIn("id",descriptor);self.assertNotIn("deps",descriptor)
            self.assertNotIn("data_hex",descriptor)
        for kind,total in totals.items():
            self.assertEqual(t["build_metrics"][kind+"_record_bytes"],total)
        self.assertEqual(t["build_metrics"]["encoded_write_bytes"],sum(totals.values()))
        m=admit_trace(strip_build_metrics(t))
        self.assertTrue(analyze_model(m)["safe"])
        self.assertTrue(recover(materialize(m, range(m.n)))["ok"])


if __name__ == "__main__":
    unittest.main()
