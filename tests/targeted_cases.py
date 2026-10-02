"""Small explicit fixtures; no dependency on the trace generator or analyzer."""
from copy import deepcopy
import hashlib
import json


def ref(slot, payload, generation=1):
    return {"slot": slot, "generation": generation,
            "digest": hashlib.sha256(payload).hexdigest(), "length": len(payload)}


def data(i, slot, payload, role="chunk", deps=(), generation=1):
    return {"id": i, "kind": "data", **ref(slot, payload, generation),
            "data_hex": payload.hex(), "role": role, "deps": list(deps)}


def root(i, prefix, namespace, manifest_ref, objects=None, deps=()):
    return {"id": i, "kind": "root", "prefix": prefix,
            "namespace": namespace, "manifest_ref": manifest_ref,
            "objects_hex": objects or {}, "deps": list(deps)}


def trace(operations, events, acks):
    return {"schema": "retirement-frontier-trace-v1", "operations": operations,
            "events": events, "acknowledgements": acks}


def ack(issued, prefix, anchors):
    return {"issued": issued, "prefix": prefix, "anchors": anchors}


def late_manifest(role="chunk"):
    return trace([{"op": "delete", "name": "x"}],
                 [root(0, 1, {}, ref(0, b"{}")), data(1, 0, b"{}", role)],
                 [ack(2, 1, [0, 1])])


def late_chunk(role="chunk", object_bytes=b"a", allocation_bytes=b"a"):
    namespace = {"x": [ref(1, allocation_bytes)]}
    manifest = json.dumps(namespace, sort_keys=True, separators=(",", ":")).encode()
    return trace([{"op": "put", "name": "x", "chunks_hex": [object_bytes.hex()]}],
                 [data(0, 0, manifest, "manifest"),
                  root(1, 1, namespace, ref(0, manifest), {"x": object_bytes.hex()}, [0]),
                  data(2, 1, allocation_bytes, role)], [ack(3, 1, [0, 1, 2])])


def missing_manifest():
    return trace([{"op": "delete", "name": "x"}],
                 [root(0, 1, {}, ref(0, b"{}"))], [ack(1, 1, [0])])


def missing_chunk():
    t=late_chunk(); t["events"].pop()
    t["acknowledgements"]=[ack(2, 1, [0, 1])]
    return t


def no_root_multi_ack():
    return trace([{"op": "delete", "name": "x"}],
                 [{"id": 0, "kind": "free", "slot": 0, "generation": 0, "deps": []}],
                 [ack(1, 1, [0]), ack(1, 1, [])])


def mixed_multi_ack():
    return trace([{"op": "delete", "name": "x"}, {"op": "delete", "name": "x"}],
                 [data(0, 0, b"{}", "manifest"), root(1, 1, {}, ref(0, b"{}"), deps=[0])],
                 [ack(2, 1, []), ack(2, 2, [0])])


def canonical_witness_trace():
    # Root 1 uses allocation 0. Roots 3 and 5 retire it; writer 6 forces both.
    # Both 3 and 5 are semantically sufficient witnesses, but 5 is canonical.
    operations=[{"op": "delete", "name": "x"} for _ in range(3)]
    events=[data(0, 0, b"{}", "manifest"), root(1, 1, {}, ref(0, b"{}"), deps=[0]),
            data(2, 1, b"{}", "manifest"), root(3, 2, {}, ref(1, b"{}"), deps=[1, 2]),
            data(4, 2, b"{}", "manifest"), root(5, 3, {}, ref(2, b"{}"), deps=[3, 4]),
            {"id": 6, "kind": "free", "slot": 0, "generation": 1, "deps": [5]}]
    return trace(operations, events, [ack(7, 2, [6]), ack(7, 0, [6])])


def cases():
    noncanonical=late_manifest("manifest")
    noncanonical["events"][0]["manifest_ref"]=ref(0, b"{ }")
    noncanonical["events"][1]=data(1, 0, b"{ }", "manifest")
    return {
        "late-manifest-wrong-role": late_manifest(),
        "late-manifest-valid": late_manifest("manifest"),
        "late-chunk-wrong-role": late_chunk("manifest"),
        "late-chunk-wrong-object": late_chunk(allocation_bytes=b"b"),
        "late-chunk-valid": late_chunk(),
        "late-manifest-noncanonical": noncanonical,
        "missing-manifest": missing_manifest(),
        "missing-chunk": missing_chunk(),
        "no-root-multiple-acks": no_root_multi_ack(),
        "mixed-multiple-acks": mixed_multi_ack(),
        "canonical-witnesses": canonical_witness_trace(),
    }
