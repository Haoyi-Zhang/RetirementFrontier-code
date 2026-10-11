"""Abstract helper-boundary projection of the fixed CURRENT protocols.

No syscalls are simulated here. Prepared root files become immutable manifest
allocations; CURRENT changes become logical retained ROOT markers. Only completed
helper prefixes after initialization are projected. This is not arbitrary POSIX
power-failure refinement, and ROOT-file presence alone is NOT publication.
"""
from __future__ import annotations

from model import canonical_manifest, make_ref


def boundary_trace(protocol: str) -> dict:
    if protocol not in ("ordered", "unsafe"):
        raise ValueError(protocol)
    events: list[dict] = []
    operations = [{"op": "put", "name": "object", "chunks_hex": [(b"A" * 32).hex()]},
                  {"op": "put", "name": "object", "chunks_hex": [(b"B" * 32).hex()]}]

    def data(slot: int, generation: int, payload: bytes, role: str) -> dict:
        ref = make_ref(slot, generation, payload)
        i = len(events)
        events.append({"id": i, "kind": "data", **ref, "data_hex": payload.hex(),
                       "role": role, "deps": [i - 1] if i else []})
        return ref

    def manifest(epoch: int, ref: dict) -> tuple[dict, dict]:
        namespace = {"object": [ref]}
        # Root files are distinct retained files; reserve distinct abstract
        # slots for their canonical decoded namespaces, separate from chunks.
        return data(2 + epoch, 1, canonical_manifest(namespace), "manifest"), namespace

    def select(prefix: int, mref: dict, namespace: dict, payload: bytes) -> None:
        i = len(events)
        events.append({"id": i, "kind": "root", "prefix": prefix,
                       "manifest_ref": mref, "namespace": namespace,
                       "objects_hex": {"object": payload.hex()}, "deps": [i - 1]})

    a = data(0, 1, b"A" * 32, "chunk")  # physical generation 0 maps to 1
    m, ns = manifest(0, a)
    select(1, m, ns, b"A" * 32)
    b = data(1 if protocol == "ordered" else 0,
             1 if protocol == "ordered" else 2, b"B" * 32, "chunk")
    m, ns = manifest(1, b)
    select(2, m, ns, b"B" * 32)
    if protocol == "ordered":
        data(0, 2, b"C" * 32, "chunk")
    return {"schema": "retirement-frontier-trace-v1", "events": events,
            "operations": operations,
            "acknowledgements": [{"issued": 3, "prefix": 1, "anchors": [2]}]}


def boundary_cut(protocol: str, after: int) -> list[int]:
    count = 4 if protocol == "ordered" else 3 if protocol == "unsafe" else -1
    if type(after) is not int or not 0 <= after <= count + 1:
        raise ValueError("invalid helper boundary")
    return list(range(3 + min(after, count)))
