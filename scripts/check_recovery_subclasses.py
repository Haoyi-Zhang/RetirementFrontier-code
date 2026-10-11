#!/usr/bin/env python3
"""Bounded local finite-model/storage checks; no network or POSIX emulation.

Outputs are separate from retained timing campaigns. Temporary image files are
ordinary local serialization checks, not power-loss or persistence experiments.
"""
from __future__ import annotations

import json
import gzip
import io
from contextlib import contextmanager
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from engine import build_trace, census_template, scaling_operations, strip_build_metrics
from fallback import publication_fallback_model
from medium import erase_slots, materialize, recover
from model import admit_trace, ref_key
from sampling import sample_cuts, cut_metadata
from selector_bridge import boundary_trace, boundary_cut
from test_recovery_subclasses import late_cut_regression, legacy_root_prefix_cuts

OUT = ROOT / "results" / "recovery-subclasses"


@contextmanager
def compressed_rows(name):
    with (OUT / (name + ".gz")).open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as text:
                yield text


def publication_census():
    graphs = eligible = checks = legal_images = unsafe = 0
    with compressed_rows("publication-fallback.jsonl") as handle:
        for family, bits in (("unretired-reuse", 10), ("retired-free", 15)):
            for mask in range(1 << bits):
                trace = census_template(family, mask)
                model = admit_trace(trace)
                graphs += 1
                if any((a := model.data_by_key.get(ref_key(ref))) is None or
                       not (model.closures[r] & (1 << a))
                       for r in model.roots for ref in model.refs_by_root[r]):
                    continue
                eligible += 1
                # Explicitly enumerate every legal image once per physical graph.
                images = []
                for bitset in range(1 << model.n):
                    cut = [i for i in range(model.n) if bitset & (1 << i)]
                    if model.is_legal_cut(cut):
                        outcome = recover(materialize(model, cut), fallback=True)
                        assert outcome["ok"]
                        images.append((set(cut), outcome["prefix"]))
                legal_images += len(images)
                configurations = [(1, []), (1, [2])]
                if family == "retired-free":
                    configurations.append((2, [4]))
                for prefix, anchors in configurations:
                    trace["acknowledgements"] = [{"issued": model.n, "prefix": prefix, "anchors": anchors}]
                    checked = publication_fallback_model(admit_trace(trace))
                    fault_cuts = [c for c, p in images if p < prefix]
                    greatest = sorted(set().union(*fault_cuts))
                    violates = any(set(anchors) <= c for c in fault_cuts)
                    assert checked["safe"] == (not violates)
                    row = checked["acknowledgement_checks"][0]
                    assert row["greatest_fault_cut"] == greatest
                    if violates:
                        assert checked["bad_cut"] == greatest
                    checks += 1
                    unsafe += violates
                    handle.write(json.dumps({"family": family, "edge_mask": mask,
                        "prefix": prefix, "anchors": anchors, "safe": checked["safe"],
                        "greatest_fault_cut": greatest, "legal_cuts": len(images),
                        "oracle_agrees": True}, sort_keys=True) + "\n")
    return {"graphs_examined": graphs, "publication_valid_graphs": eligible,
            "acknowledgement_checks": checks, "legal_images": legal_images,
            "unsafe_checks": unsafe, "disagreements": 0}


def full_history_storage():
    traces = images = 0
    max_old = 0
    min_new = 512
    with compressed_rows("full-history.jsonl") as handle:
        with tempfile.TemporaryDirectory(prefix="image-roundtrip-", dir=OUT) as temp:
            image_path = Path(temp) / "image.json"
            for family in ("shared", "unique", "churn"):
                for seed in range(20000, 20005):
                    operations = scaling_operations(512, family, seed)
                    for policy, dedup, batch in (("no-dedup", False, 4),
                            ("sync-metadata", True, 1), ("unchecked-dedup", True, 4)):
                        model = admit_trace(strip_build_metrics(build_trace(
                            operations, deduplicate=dedup, batch_size=batch)))
                        traces += 1
                        cuts = sample_cuts(model, 16, seed + 512 + len(family) + len(policy))
                        prefixes = [cut_metadata(model, c)["selected_root_prefix"] for c in cuts]
                        old = legacy_root_prefix_cuts(model)
                        old_max = max(cut_metadata(model, c)["selected_root_prefix"] for c in old)
                        new_max = max(prefixes)
                        max_old = max(max_old, old_max)
                        min_new = min(min_new, new_max)
                        assert all(any(lo <= p <= hi for p in prefixes) for lo, hi in
                                   ((1, 128), (129, 256), (257, 384), (385, 512)))
                        # The publication-valid decision must handle all retained
                        # candidates, rather than selecting a fixed-k subset.
                        assert publication_fallback_model(model)["safe"]
                        for index, cut in enumerate(cuts + [list(range(model.n))]):
                            image_path.write_text(json.dumps(materialize(model, cut),
                                sort_keys=True), encoding="utf-8")
                            image = json.loads(image_path.read_text(encoding="utf-8"))
                            outcome = recover(image)
                            present = set(cut)
                            selected = max((r for r in model.roots if r in present), default=None)
                            expected = {} if selected is None else model.events[selected]["objects_hex"]
                            prefix = 0 if selected is None else model.events[selected]["prefix"]
                            assert outcome["ok"] and outcome["root_event"] == selected
                            assert outcome["prefix"] == prefix and outcome["objects_hex"] == expected
                            assert recover(image) == outcome
                            multiplicities = {}
                            if selected is not None:
                                for refs in model.events[selected]["namespace"].values():
                                    for ref in refs:
                                        key = ":".join(map(str, ref_key(ref)))
                                        multiplicities[key] = multiplicities.get(key, 0) + 1
                            assert outcome["reference_counts"] == multiplicities
                            erased = recover(erase_slots(image, outcome["unreachable_slots"]))
                            assert all(erased[k] == outcome[k] for k in
                                       ("ok", "prefix", "root_event", "objects_hex", "reference_counts"))
                            images += 1
                            handle.write(json.dumps({"family": family, "seed": seed,
                                "policy": policy, "operations": 512, "events": model.n,
                                "sample": "full" if index == 16 else index,
                                **cut_metadata(model, cut), "legacy_sample_max_prefix": old_max,
                                "stratified_sample_max_prefix": new_max,
                                "specification_bytes_checked": True,
                                "file_roundtrip_checked": True, "idempotence_checked": True,
                                "multiplicity_checked": True, "reclamation_checked": True},
                                sort_keys=True) + "\n")
                        print(f"history {family} seed={seed} policy={policy} old={old_max} new={new_max}", flush=True)
    return {"traces": traces, "images": images, "legacy_max_sampled_prefix": max_old,
            "minimum_stratified_max_prefix": min_new, "all_four_prefix_quarters_covered": True,
            "publication_fallback_safe_traces": traces, "disagreements": 0,
            "storage_scope": "local image-file roundtrips, no crash or fsync durability claim"}


def selector_projection():
    rows = []
    for protocol, boundaries in (("unsafe", 3), ("ordered", 4)):
        model = admit_trace(boundary_trace(protocol))
        for after in range(boundaries + 2):
            cut = boundary_cut(protocol, after)
            outcome = recover(materialize(model, cut))
            rows.append({"protocol": protocol, "after": after, "cut": cut,
                         "recoverable": outcome["ok"], "outcome": outcome})
    (OUT / "selector-projection.json").write_text(json.dumps({
        "scope": "abstract completed-helper projection only; POSIX syscalls not executed",
        "rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"abstract_observations": len(rows), "posix_syscalls_executed": False,
            "ordered_failures": sum(not r["recoverable"] for r in rows if r["protocol"] == "ordered"),
            "unsafe_failures": sum(not r["recoverable"] for r in rows if r["protocol"] == "unsafe")}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    result = {"schema": 1, "platform": platform.platform(), "python": platform.python_version(),
              "publication_fallback": publication_census(),
              "full_history": full_history_storage(), "late_cut_regression": late_cut_regression(),
              "selector_projection": selector_projection(),
              "wall_seconds": time.perf_counter() - start}
    (OUT / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
