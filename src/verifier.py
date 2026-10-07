"""Code-separated consumer for retirement-frontier certificates.

This module intentionally imports none of the producer, engine, model, oracle,
or solver modules. It independently re-parses the finite JSON object, repeats
all admission checks needed by the certificate semantics, and derives the exact
canonical witness rows required by the declared trace.

Unlike the producer, the consumer stores only direct predecessor lists. It uses
fresh backward searches for publication and a topological greatest-root table
for retirement and acknowledgement checks. Its auxiliary graph state is linear
in the admitted trace; it does not retain the producer's closure bitsets.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

MAX_ID = (1 << 63) - 1


class VerificationError(ValueError):
    """Raised for malformed traces, unsafe traces, or invalid certificates."""


class TraceFormatError(VerificationError):
    """The input trace failed parsing, rather than certificate checking."""


def _require_int(value: Any, where: str, *, minimum: int = 0, maximum: int = MAX_ID) -> int:
    """Parse a JSON integer without accepting booleans, floats, or numeric strings."""
    if type(value) is not int:
        raise VerificationError(f"{where}: must be an integer")
    if value < minimum or value > maximum:
        raise VerificationError(f"{where}: integer out of range")
    return value


def _decode_hex(value: Any, where: str) -> bytes:
    """Decode even-length hexadecimal while rejecting whitespace/coercions."""
    if not isinstance(value, str) or len(value) % 2 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise VerificationError(f"{where}: invalid hexadecimal string")
    return bytes.fromhex(value)


def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_ref(ref: Any, where: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise VerificationError(f"{where}: reference must be an object")
    required = {"slot", "generation", "digest", "length"}
    if set(ref) != required:
        raise VerificationError(f"{where}: reference fields must be {sorted(required)}")
    slot = _require_int(ref["slot"], f"{where} slot")
    generation = _require_int(ref["generation"], f"{where} generation", minimum=1)
    length = _require_int(ref["length"], f"{where} length")
    digest = ref["digest"]
    if not isinstance(digest, str):
        raise VerificationError(f"{where}: digest must be a string")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise VerificationError(f"{where}: digest is not lower-case SHA-256 hex")
    return {"slot": slot, "generation": generation, "digest": digest, "length": length}


def _ref_key(ref: dict[str, Any]) -> tuple[int, int, str, int]:
    return (int(ref["slot"]), int(ref["generation"]), str(ref["digest"]), int(ref["length"]))


def _prefix_snapshots(operations_raw: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    operations: list[dict[str, Any]] = []
    snapshots: list[dict[str, str]] = [{}]
    state: dict[str, str] = {}
    for index, raw in enumerate(operations_raw):
        if not isinstance(raw, dict):
            raise VerificationError(f"operation {index}: malformed")
        op = dict(raw)
        if "op" not in op or "name" not in op:
            raise VerificationError(f"operation {index}: malformed")
        name = op["name"]
        if not isinstance(name, str) or not name:
            raise VerificationError(f"operation {index}: invalid name")
        if op["op"] == "put":
            chunks = op.get("chunks_hex")
            if not isinstance(chunks, list):
                raise VerificationError(f"operation {index}: put requires chunks_hex")
            if set(op) != {"op", "name", "chunks_hex"}:
                raise VerificationError(f"operation {index}: put has missing or extra fields")
            data = b"".join(_decode_hex(value, f"operation {index} chunk {chunk_index}") for chunk_index, value in enumerate(chunks))
            state[name] = data.hex()
        elif op["op"] == "delete":
            if set(op) != {"op", "name"}:
                raise VerificationError(f"operation {index}: delete has extra fields")
            state.pop(name, None)
        else:
            raise VerificationError(f"operation {index}: unknown op {op['op']!r}")
        operations.append(op)
        snapshots.append(dict(state))
    return operations, snapshots


def _closure_ids(deps: list[tuple[int, ...]], seeds: Iterable[int]) -> set[int]:
    """Return the backward closure using linear temporary state."""
    pending = [int(seed) for seed in seeds]
    seen: set[int] = set()
    while pending:
        event_id = pending.pop()
        if event_id in seen:
            continue
        seen.add(event_id)
        pending.extend(parent for parent in deps[event_id] if parent not in seen)
    return seen


def _maximum_root_ancestor(events: tuple[dict[str, Any], ...], deps: list[tuple[int, ...]]) -> list[int]:
    """Compute the greatest root in each event's closure in one topological pass.

    Event identifiers are issue positions and every dependency names an earlier
    event. Root prefixes are strictly increasing, so the greatest root identifier
    in a closure is also the strongest root witness needed by retirement and
    acknowledgement checks. The table uses O(n) auxiliary memory.
    """
    greatest: list[int] = []
    for event_id, parents in enumerate(deps):
        best = event_id if events[event_id]["kind"] == "root" else -1
        for parent in parents:
            if greatest[parent] > best:
                best = greatest[parent]
        greatest.append(best)
    return greatest



def _validate_complete_bindings(
    events: list[dict[str, Any]], roots: list[int],
    data_by_key: dict[tuple[int, int, str, int], int],
) -> None:
    """Validate references against the complete trace, not the issue prefix.

    Forward allocations are permitted by admission (publication may fail).
    A genuinely absent allocation is left to safety analysis. Every present
    allocation is role/byte checked even if another object reference is absent.
    """
    for root_id in roots:
        root = events[root_id]
        namespace = root["namespace"]
        manifest_id = data_by_key.get(_ref_key(root["manifest_ref"]))
        if manifest_id is not None:
            manifest = events[manifest_id]
            if manifest["role"] != "manifest":
                raise VerificationError(f"event {root_id}: manifest reference names a non-manifest allocation")
            if bytes.fromhex(manifest["data_hex"]) != _canon(namespace):
                raise VerificationError(f"event {root_id}: manifest allocation bytes are noncanonical")
        for name, refs in namespace.items():
            chunks: list[bytes] = []
            complete = True
            for ref in refs:
                allocation_id = data_by_key.get(_ref_key(ref))
                if allocation_id is None:
                    complete = False
                    continue
                allocation = events[allocation_id]
                if allocation["role"] != "chunk":
                    raise VerificationError(f"event {root_id}: object reference names a non-chunk allocation")
                # Allocation admission has already checked its digest/length;
                # the complete key lookup binds both to this reference.
                chunks.append(bytes.fromhex(allocation["data_hex"]))
            if complete and b"".join(chunks).hex() != root["objects_hex"][name]:
                raise VerificationError(f"event {root_id}: references do not reconstruct ghost object {name}")

def _parse(trace: dict[str, Any]) -> dict[str, Any]:
    """Independently admit and normalize a trace for certificate checking."""
    if not isinstance(trace, dict) or trace.get("schema") != "retirement-frontier-trace-v1":
        raise VerificationError("unsupported trace schema")
    trace_fields = {"schema", "operations", "events", "acknowledgements"}
    if set(trace) != trace_fields:
        raise VerificationError(f"trace fields must be {sorted(trace_fields)}")
    events_raw = trace.get("events")
    operations_raw = trace.get("operations", [])
    acks_raw = trace.get("acknowledgements", [])
    if not isinstance(events_raw, list) or not isinstance(operations_raw, list) or not isinstance(acks_raw, list):
        raise VerificationError("events, operations, and acknowledgements must be lists")

    operations, snapshots = _prefix_snapshots(operations_raw)

    events: list[dict[str, Any]] = []
    deps: list[tuple[int, ...]] = []
    roots: list[int] = []
    refs_by_root: dict[int, tuple[dict[str, Any], ...]] = {}
    data_by_identity: dict[tuple[int, int], int] = {}
    data_by_key: dict[tuple[int, int, str, int], int] = {}
    writers_by_slot: dict[int, list[int]] = {}
    actual_bytes_by_digest: dict[str, bytes] = {}
    previous_prefix = -1

    for i, raw in enumerate(events_raw):
        if not isinstance(raw, dict):
            raise VerificationError(f"event {i}: must be an object")
        event = dict(raw)
        if type(event.get("id")) is not int or event["id"] != i:
            raise VerificationError(f"event {i}: id must be an integer equal to issue position")
        direct = event.get("deps", [])
        if not isinstance(direct, list):
            raise VerificationError(f"event {i}: deps must be a list")
        try:
            direct_values = [_require_int(value, f"event {i} dependency", maximum=max(0, i - 1)) for value in direct]
        except VerificationError as exc:
            raise VerificationError(f"event {i}: invalid dependency: {exc}") from exc
        direct_ids = tuple(sorted(set(direct_values)))
        if any(parent < 0 or parent >= i for parent in direct_ids):
            raise VerificationError(f"event {i}: dependency must name an earlier event")
        event["deps"] = list(direct_ids)
        deps.append(direct_ids)

        kind = event.get("kind")
        if not isinstance(kind, str):
            raise VerificationError(f"event {i}: kind must be a string")
        if kind == "data":
            required_fields = {"id", "kind", "slot", "generation", "digest", "length", "data_hex", "role", "deps"}
            if set(event) != required_fields:
                raise VerificationError(f"event {i}: data fields must be {sorted(required_fields)}")
            try:
                slot = _require_int(event["slot"], f"event {i} slot")
                generation = _require_int(event["generation"], f"event {i} generation", minimum=1)
                data = _decode_hex(event["data_hex"], f"event {i} data_hex")
                length = _require_int(event["length"], f"event {i} length")
            except (KeyError, VerificationError) as exc:
                raise VerificationError(f"event {i}: malformed data record: {exc}") from exc
            identity = (slot, generation)
            if identity in data_by_identity:
                raise VerificationError(f"event {i}: duplicate slot-generation incarnation")
            digest = event.get("digest")
            if digest != _digest(data) or length != len(data):
                raise VerificationError(f"event {i}: data digest or length mismatch")
            role = event.get("role")
            if not isinstance(role, str) or role not in {"chunk", "manifest"}:
                raise VerificationError(f"event {i}: invalid data role")
            old = actual_bytes_by_digest.get(digest)
            if old is not None and old != data:
                raise VerificationError(f"event {i}: digest collision among consumed bytes")
            event["slot"] = slot
            event["generation"] = generation
            event["length"] = length
            event["data_hex"] = data.hex()
            actual_bytes_by_digest[digest] = data
            key = (slot, generation, str(digest), len(data))
            data_by_identity[identity] = i
            data_by_key[key] = i
            writers_by_slot.setdefault(slot, []).append(i)

        elif kind == "free":
            required_fields = {"id", "kind", "slot", "generation", "deps"}
            if set(event) != required_fields:
                raise VerificationError(f"event {i}: free fields must be {sorted(required_fields)}")
            try:
                slot = _require_int(event["slot"], f"event {i} slot")
                generation = _require_int(event.get("generation", 0), f"event {i} generation")
            except (KeyError, VerificationError) as exc:
                raise VerificationError(f"event {i}: malformed free record: {exc}") from exc
            event["slot"] = slot
            event["generation"] = generation
            writers_by_slot.setdefault(slot, []).append(i)

        elif kind == "root":
            required_fields = {"id", "kind", "prefix", "manifest_ref", "namespace", "objects_hex", "deps"}
            if set(event) != required_fields:
                raise VerificationError(f"event {i}: root fields must be {sorted(required_fields)}")
            try:
                prefix = _require_int(event["prefix"], f"event {i} prefix", maximum=len(operations))
            except (KeyError, VerificationError) as exc:
                raise VerificationError(f"event {i}: invalid root prefix: {exc}") from exc
            if prefix <= previous_prefix:
                raise VerificationError(f"event {i}: root prefixes must increase and name a client prefix")
            previous_prefix = prefix

            manifest_ref = _check_ref(event.get("manifest_ref"), f"event {i} manifest")
            namespace_raw = event.get("namespace")
            objects_raw = event.get("objects_hex")
            if not isinstance(namespace_raw, dict) or not isinstance(objects_raw, dict):
                raise VerificationError(f"event {i}: root requires namespace and objects_hex")
            if any(not isinstance(name, str) for name in namespace_raw):
                raise VerificationError(f"event {i}: malformed namespace name")
            if any(not isinstance(name, str) for name in objects_raw):
                raise VerificationError(f"event {i}: malformed objects_hex name")

            namespace: dict[str, list[dict[str, Any]]] = {}
            for name in sorted(namespace_raw):
                raw_refs = namespace_raw[name]
                if not isinstance(raw_refs, list):
                    raise VerificationError(f"event {i}: malformed namespace")
                namespace[name] = [
                    _check_ref(ref, f"event {i} object {name}") for ref in raw_refs
                ]
            if set(objects_raw) != set(namespace):
                raise VerificationError(f"event {i}: objects_hex names differ from namespace")
            normalized_objects: dict[str, str] = {}
            for name, value in objects_raw.items():
                normalized_objects[name] = _decode_hex(value, f"event {i} object {name!r}").hex()
            if normalized_objects != snapshots[prefix]:
                raise VerificationError(f"event {i}: ghost namespace differs from client prefix")

            manifest_bytes = _canon(namespace)
            expected_manifest = {
                "slot": manifest_ref["slot"],
                "generation": manifest_ref["generation"],
                "digest": _digest(manifest_bytes),
                "length": len(manifest_bytes),
            }
            if _ref_key(expected_manifest) != _ref_key(manifest_ref):
                raise VerificationError(f"event {i}: manifest reference does not bind canonical bytes")
            all_refs: list[dict[str, Any]] = [manifest_ref]
            seen = {_ref_key(manifest_ref)}
            for name in sorted(namespace):
                for ref in namespace[name]:
                    key = _ref_key(ref)
                    if key not in seen:
                        all_refs.append(ref)
                        seen.add(key)
            event["prefix"] = prefix
            event["manifest_ref"] = manifest_ref
            event["namespace"] = namespace
            event["objects_hex"] = normalized_objects
            roots.append(i)
            refs_by_root[i] = tuple(all_refs)
        else:
            raise VerificationError(f"event {i}: unknown kind {kind!r}")
        events.append(event)

    _validate_complete_bindings(events, roots, data_by_key)

    acknowledgements: list[dict[str, Any]] = []
    for index, raw in enumerate(acks_raw):
        if not isinstance(raw, dict):
            raise VerificationError(f"acknowledgement {index}: malformed")
        required_fields = {"issued", "prefix", "anchors"}
        if set(raw) != required_fields:
            raise VerificationError(f"acknowledgement {index}: fields must be {sorted(required_fields)}")
        try:
            issued = _require_int(raw["issued"], f"acknowledgement {index} issued", maximum=len(events))
            prefix = _require_int(raw["prefix"], f"acknowledgement {index} prefix", maximum=len(operations))
            anchors_raw = raw.get("anchors", [])
            if not isinstance(anchors_raw, list):
                raise VerificationError("anchors must be a list")
            anchors = tuple(sorted(set(_require_int(value, f"acknowledgement {index} anchor", maximum=max(0, issued - 1)) for value in anchors_raw)))
        except (KeyError, VerificationError) as exc:
            raise VerificationError(f"acknowledgement {index}: malformed: {exc}") from exc
        if any(anchor < 0 or anchor >= issued for anchor in anchors):
            raise VerificationError(f"acknowledgement {index}: anchor outside issue horizon")
        acknowledgements.append({"issued": issued, "prefix": prefix, "anchors": list(anchors)})

    return {
        "events": tuple(events),
        "deps": tuple(deps),
        "roots": tuple(roots),
        "refs": refs_by_root,
        "data": data_by_key,
        "writers": {slot: tuple(ids) for slot, ids in writers_by_slot.items()},
        "acks": tuple(acknowledgements),
    }


def _check_certificate_shape(certificate: Any) -> None:
    if not isinstance(certificate, dict):
        raise VerificationError("certificate must be an object")
    required = {"schema", "publication", "retirement", "acknowledgement"}
    if set(certificate) != required or certificate.get("schema") != "retirement-frontier-certificate-v1":
        raise VerificationError("unsupported or malformed certificate schema")
    row_fields = {
        "publication": {"root", "ref_index", "allocation"},
        "retirement": {"writer", "guard_root"},
        "acknowledgement": {"ack_index", "root"},
    }
    for section, fields in row_fields.items():
        rows = certificate.get(section)
        if not isinstance(rows, list):
            raise VerificationError(f"certificate {section} must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != fields:
                raise VerificationError(f"certificate {section} row {index} has invalid fields")
            if any(type(row[field]) is not int for field in fields):
                raise VerificationError(f"certificate {section} row {index} has a non-integer value")


def verify_certificate(trace: dict[str, Any], certificate: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = _parse(trace)
    except VerificationError as exc:
        raise TraceFormatError(str(exc)) from exc
    _check_certificate_shape(certificate)

    events = parsed["events"]
    deps = list(parsed["deps"])
    greatest_root = _maximum_root_ancestor(events, deps)

    expected_pub: list[dict[str, int]] = []
    last_user: dict[int, int] = {event_id: -1 for event_id in parsed["data"].values()}
    for root in parsed["roots"]:
        closure = _closure_ids(deps, [root])
        for ref_index, ref in enumerate(parsed["refs"][root]):
            alloc = parsed["data"].get(_ref_key(ref))
            if alloc is None or alloc not in closure:
                raise VerificationError("trace fails publication; no positive certificate exists")
            expected_pub.append({"root": root, "ref_index": ref_index, "allocation": alloc})
            last_user[alloc] = max(last_user.get(alloc, -1), root)

    expected_ret: list[dict[str, int]] = []
    for _slot, slot_writers in sorted(parsed["writers"].items()):
        frontier = -1
        for writer in slot_writers:
            if frontier >= 0:
                guard = greatest_root[writer]
                if guard <= frontier:
                    raise VerificationError("trace fails retirement; no positive certificate exists")
                expected_ret.append({"writer": writer, "guard_root": guard})
            if events[writer]["kind"] == "data":
                frontier = max(frontier, last_user.get(writer, -1))

    expected_ack: list[dict[str, int]] = []
    for ack_index, ack in enumerate(parsed["acks"]):
        if ack["prefix"] == 0:
            expected_ack.append({"ack_index": ack_index, "root": -1})
            continue
        guard = max((greatest_root[anchor] for anchor in ack["anchors"]), default=-1)
        if guard < 0 or int(events[guard]["prefix"]) < ack["prefix"]:
            raise VerificationError("trace fails acknowledgement closure; no positive certificate exists")
        expected_ack.append({"ack_index": ack_index, "root": guard})

    supplied = {
        "publication": certificate["publication"],
        "retirement": certificate["retirement"],
        "acknowledgement": certificate["acknowledgement"],
    }
    expected = {
        "publication": expected_pub,
        "retirement": expected_ret,
        "acknowledgement": expected_ack,
    }
    if supplied != expected:
        raise VerificationError("certificate rows are incomplete, reordered, duplicated, or incorrect")
    return {
        "accepted": True,
        "publication_rows": len(expected_pub),
        "retirement_rows": len(expected_ret),
        "acknowledgement_rows": len(expected_ack),
        "certificate_bytes": len(_canon(certificate)),
    }
