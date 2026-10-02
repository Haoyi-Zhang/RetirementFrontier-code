"""Exact all-subset oracle for small admitted traces."""
from __future__ import annotations

import time
from typing import Any

from medium import materialize, recover
from model import Model, admit_trace


def evaluate_cut(model: Model, cut: list[int], fallback: bool = False) -> dict[str, Any]:
    image = materialize(model, cut)
    outcome = recover(image, fallback=fallback)
    if not outcome.get("ok"):
        return {"safe": False, "reason": "recovery", "outcome": outcome}
    root_event = outcome.get("root_event")
    if root_event is not None:
        expected = model.events[int(root_event)]["objects_hex"]
        if outcome.get("objects_hex") != expected:
            return {"safe": False, "reason": "wrong-bytes", "outcome": outcome}
    elif outcome.get("objects_hex") != {}:
        return {"safe": False, "reason": "nonempty-without-root", "outcome": outcome}
    bits = 0
    for i in cut:
        bits |= 1 << int(i)
    for ack_index, ack in enumerate(model.acknowledgements):
        if all(bits & (1 << int(anchor)) for anchor in ack["anchors"]):
            if int(outcome["prefix"]) < int(ack["prefix"]):
                return {"safe": False, "reason": "acknowledgement", "ack_index": ack_index, "outcome": outcome}
    return {"safe": True, "outcome": outcome}


def exhaustive_model(model: Model, fallback: bool = False, max_events: int = 22) -> dict[str, Any]:
    if model.n > max_events:
        raise ValueError(f"exact oracle limited to {max_events} events, got {model.n}")
    start = time.process_time()
    legal = 0
    bad = 0
    minimum: list[int] | None = None
    candidate_cuts = 1 << model.n
    for mask in range(candidate_cuts):
        cut = [i for i in range(model.n) if mask & (1 << i)]
        if not model.is_legal_cut(cut):
            continue
        legal += 1
        verdict = evaluate_cut(model, cut, fallback=fallback)
        if not verdict["safe"]:
            bad += 1
            if minimum is None or (len(cut), tuple(cut)) < (len(minimum), tuple(minimum)):
                minimum = cut
    return {
        "safe": bad == 0,
        "events": model.n,
        "candidate_cuts": candidate_cuts,
        "legal_cuts": legal,
        "bad_cuts": bad,
        "minimum_bad_cut": minimum,
        "oracle_cpu_seconds": time.process_time() - start,
    }


def exhaustive_trace(trace: dict[str, Any], fallback: bool = False, max_events: int = 22) -> dict[str, Any]:
    return exhaustive_model(admit_trace(trace), fallback=fallback, max_events=max_events)
