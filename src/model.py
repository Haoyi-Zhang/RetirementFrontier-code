"""Finite trace model and admission checks for retirement-frontier certificates."""
from __future__ import annotations
from strictjson import loads as strict_json_loads

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

MAX_ID = (1 << 63) - 1


class AdmissionError(ValueError):
    """Raised when a trace is malformed rather than merely unsafe."""


def _require_int(value: Any, where: str, *, minimum: int = 0, maximum: int = MAX_ID) -> int:
    """Parse a JSON integer without accepting booleans, floats, or numeric strings."""
    if type(value) is not int:
        raise AdmissionError(f"{where}: must be an integer")
    if value < minimum or value > maximum:
        raise AdmissionError(f"{where}: integer out of range")
    return value


def _decode_hex(value: Any, where: str) -> bytes:
    """Decode even-length hexadecimal while rejecting whitespace/coercions."""
    if not isinstance(value, str) or len(value) % 2 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise AdmissionError(f"{where}: invalid hexadecimal string")
    return bytes.fromhex(value)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ref_key(ref: dict[str, Any]) -> tuple[int, int, str, int]:
    return (int(ref["slot"]), int(ref["generation"]), str(ref["digest"]), int(ref["length"]))


def make_ref(slot: int, generation: int, data: bytes) -> dict[str, Any]:
    return {
        "slot": int(slot),
        "generation": int(generation),
        "digest": digest_hex(data),
        "length": len(data),
    }


def canonical_manifest(namespace: dict[str, list[dict[str, Any]]]) -> bytes:
    serial: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(namespace):
        serial[name] = [
            {
                "slot": int(r["slot"]),
                "generation": int(r["generation"]),
                "digest": str(r["digest"]),
                "length": int(r["length"]),
            }
            for r in namespace[name]
        ]
    return canonical_json_bytes(serial)


def _check_ref(ref: Any, where: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise AdmissionError(f"{where}: reference must be an object")
    required = {"slot", "generation", "digest", "length"}
    if set(ref) != required:
        raise AdmissionError(f"{where}: reference fields must be {sorted(required)}")
    slot = _require_int(ref["slot"], f"{where} slot")
    generation = _require_int(ref["generation"], f"{where} generation", minimum=1)
    length = _require_int(ref["length"], f"{where} length")
    digest = ref["digest"]
    if not isinstance(digest, str):
        raise AdmissionError(f"{where}: digest must be a string")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise AdmissionError(f"{where}: digest is not lower-case SHA-256 hex")
    return {"slot": slot, "generation": generation, "digest": digest, "length": length}


@dataclass(frozen=True)
class Model:
    trace: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    deps: tuple[tuple[int, ...], ...]
    closures: tuple[int, ...]
    roots: tuple[int, ...]
    data_by_identity: dict[tuple[int, int], int]
    data_by_key: dict[tuple[int, int, str, int], int]
    writers_by_slot: dict[int, tuple[int, ...]]
    refs_by_root: dict[int, tuple[dict[str, Any], ...]]
    operations: tuple[dict[str, Any], ...]
    acknowledgements: tuple[dict[str, Any], ...]

    @property
    def n(self) -> int:
        return len(self.events)

    def closure_ids(self, event_or_events: int | Iterable[int]) -> set[int]:
        if isinstance(event_or_events, int):
            bits = self.closures[event_or_events]
        else:
            bits = 0
            for event_id in event_or_events:
                bits |= self.closures[int(event_id)]
        return {i for i in range(self.n) if bits & (1 << i)}

    def is_legal_cut(self, cut: Iterable[int]) -> bool:
        bits = 0
        for raw in cut:
            if type(raw) is not int:
                return False
            i = raw
            if i < 0 or i >= self.n:
                return False
            bits |= 1 << i
        required = 0
        for i in range(self.n):
            if bits & (1 << i):
                required |= self.closures[i]
        return (required & ~bits) == 0


def _compute_closures(deps: list[tuple[int, ...]]) -> tuple[int, ...]:
    closures: list[int] = []
    for i, direct in enumerate(deps):
        bits = 1 << i
        for parent in direct:
            bits |= closures[parent]
        closures.append(bits)
    return tuple(closures)


def _client_prefix_objects(operations: list[dict[str, Any]]) -> list[dict[str, str]]:
    state: dict[str, str] = {}
    snapshots: list[dict[str, str]] = [dict(state)]
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or "op" not in op or "name" not in op:
            raise AdmissionError(f"operation {index}: malformed")
        name = op["name"]
        if not isinstance(name, str) or not name:
            raise AdmissionError(f"operation {index}: invalid name")
        kind = op["op"]
        if kind == "put":
            chunks = op.get("chunks_hex")
            if not isinstance(chunks, list):
                raise AdmissionError(f"operation {index}: put requires chunks_hex")
            if set(op) != {"op", "name", "chunks_hex"}:
                raise AdmissionError(f"operation {index}: put has missing or extra fields")
            data = b"".join(_decode_hex(value, f"operation {index} chunk {chunk_index}") for chunk_index, value in enumerate(chunks))
            state[name] = data.hex()
        elif kind == "delete":
            if set(op) != {"op", "name"}:
                raise AdmissionError(f"operation {index}: delete has extra fields")
            state.pop(name, None)
        else:
            raise AdmissionError(f"operation {index}: unknown op {kind!r}")
        snapshots.append(dict(state))
    return snapshots



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
        manifest_id = data_by_key.get(ref_key(root["manifest_ref"]))
        if manifest_id is not None:
            manifest = events[manifest_id]
            if manifest["role"] != "manifest":
                raise AdmissionError(f"event {root_id}: manifest reference names a non-manifest allocation")
            if bytes.fromhex(manifest["data_hex"]) != canonical_manifest(namespace):
                raise AdmissionError(f"event {root_id}: manifest allocation bytes are noncanonical")
        for name, refs in namespace.items():
            chunks: list[bytes] = []
            complete = True
            for ref in refs:
                allocation_id = data_by_key.get(ref_key(ref))
                if allocation_id is None:
                    complete = False
                    continue
                allocation = events[allocation_id]
                if allocation["role"] != "chunk":
                    raise AdmissionError(f"event {root_id}: object reference names a non-chunk allocation")
                # Allocation admission has already checked its digest/length;
                # the complete key lookup binds both to this reference.
                chunks.append(bytes.fromhex(allocation["data_hex"]))
            if complete and b"".join(chunks).hex() != root["objects_hex"][name]:
                raise AdmissionError(f"event {root_id}: references do not reconstruct ghost object {name}")

def admit_trace(trace: dict[str, Any]) -> Model:
    if not isinstance(trace, dict):
        raise AdmissionError("trace must be a JSON object")
    if trace.get("schema") != "retirement-frontier-trace-v1":
        raise AdmissionError("unsupported or missing schema")
    trace_fields = {"schema", "operations", "events", "acknowledgements"}
    if set(trace) != trace_fields:
        raise AdmissionError(f"trace fields must be {sorted(trace_fields)}")
    events_raw = trace.get("events")
    operations_raw = trace.get("operations", [])
    acks_raw = trace.get("acknowledgements", [])
    if not isinstance(events_raw, list) or not isinstance(operations_raw, list) or not isinstance(acks_raw, list):
        raise AdmissionError("events, operations, and acknowledgements must be lists")

    operations: list[dict[str, Any]] = []
    for index, raw in enumerate(operations_raw):
        if not isinstance(raw, dict):
            raise AdmissionError(f"operation {index}: malformed")
        operations.append(dict(raw))
    snapshots = _client_prefix_objects(operations)

    events: list[dict[str, Any]] = []
    deps: list[tuple[int, ...]] = []
    data_by_identity: dict[tuple[int, int], int] = {}
    data_by_key: dict[tuple[int, int, str, int], int] = {}
    writers_by_slot_mut: dict[int, list[int]] = {}
    roots: list[int] = []
    refs_by_root: dict[int, tuple[dict[str, Any], ...]] = {}
    previous_root_prefix = -1
    actual_bytes_by_digest: dict[str, bytes] = {}

    for i, raw in enumerate(events_raw):
        if not isinstance(raw, dict):
            raise AdmissionError(f"event {i}: must be an object")
        event = dict(raw)
        if type(event.get("id")) is not int or event["id"] != i:
            raise AdmissionError(f"event {i}: id must be an integer equal to issue position")
        direct = event.get("deps", [])
        if not isinstance(direct, list):
            raise AdmissionError(f"event {i}: deps must be a list")
        try:
            direct_values = [_require_int(x, f"event {i} dependency", maximum=max(0, i - 1)) for x in direct]
        except AdmissionError as exc:
            raise AdmissionError(f"event {i}: invalid dependency: {exc}") from exc
        direct_ids = tuple(sorted(set(direct_values)))
        if any(x < 0 or x >= i for x in direct_ids):
            raise AdmissionError(f"event {i}: dependency must name an earlier event")
        event["deps"] = list(direct_ids)
        deps.append(direct_ids)
        kind = event.get("kind")
        if not isinstance(kind, str):
            raise AdmissionError(f"event {i}: kind must be a string")
        if kind == "data":
            required_fields = {"id", "kind", "slot", "generation", "digest", "length", "data_hex", "role", "deps"}
            if set(event) != required_fields:
                raise AdmissionError(f"event {i}: data fields must be {sorted(required_fields)}")
            try:
                slot = _require_int(event["slot"], f"event {i} slot")
                generation = _require_int(event["generation"], f"event {i} generation", minimum=1)
                data = _decode_hex(event["data_hex"], f"event {i} data_hex")
                length = _require_int(event["length"], f"event {i} length")
            except (KeyError, AdmissionError) as exc:
                raise AdmissionError(f"event {i}: malformed data record: {exc}") from exc
            identity = (slot, generation)
            if identity in data_by_identity:
                raise AdmissionError(f"event {i}: duplicate slot-generation incarnation")
            if event.get("digest") != digest_hex(data) or length != len(data):
                raise AdmissionError(f"event {i}: data digest or length mismatch")
            role = event.get("role")
            if not isinstance(role, str) or role not in {"chunk", "manifest"}:
                raise AdmissionError(f"event {i}: invalid data role")
            old = actual_bytes_by_digest.get(event["digest"])
            if old is not None and old != data:
                raise AdmissionError(f"event {i}: digest collision among consumed bytes")
            event["slot"] = slot
            event["generation"] = generation
            event["length"] = length
            event["data_hex"] = data.hex()
            actual_bytes_by_digest[event["digest"]] = data
            data_by_identity[identity] = i
            data_by_key[(slot, generation, event["digest"], len(data))] = i
            writers_by_slot_mut.setdefault(slot, []).append(i)
        elif kind == "free":
            required_fields = {"id", "kind", "slot", "generation", "deps"}
            if set(event) != required_fields:
                raise AdmissionError(f"event {i}: free fields must be {sorted(required_fields)}")
            try:
                slot = _require_int(event["slot"], f"event {i} slot")
                generation = _require_int(event.get("generation", 0), f"event {i} generation")
            except (KeyError, AdmissionError) as exc:
                raise AdmissionError(f"event {i}: malformed free record: {exc}") from exc
            event["slot"] = slot
            event["generation"] = generation
            writers_by_slot_mut.setdefault(slot, []).append(i)
        elif kind == "root":
            required_fields = {"id", "kind", "prefix", "manifest_ref", "namespace", "objects_hex", "deps"}
            if set(event) != required_fields:
                raise AdmissionError(f"event {i}: root fields must be {sorted(required_fields)}")
            try:
                prefix = _require_int(event["prefix"], f"event {i} prefix", maximum=len(operations))
            except (KeyError, AdmissionError) as exc:
                raise AdmissionError(f"event {i}: invalid root prefix: {exc}") from exc
            if prefix <= previous_root_prefix:
                raise AdmissionError(f"event {i}: root prefixes must increase and name a client prefix")
            previous_root_prefix = prefix
            manifest_ref = _check_ref(event.get("manifest_ref"), f"event {i} manifest")
            namespace_raw = event.get("namespace")
            objects_hex = event.get("objects_hex")
            if not isinstance(namespace_raw, dict) or not isinstance(objects_hex, dict):
                raise AdmissionError(f"event {i}: root requires namespace and objects_hex")
            if any(not isinstance(name, str) for name in namespace_raw):
                raise AdmissionError(f"event {i}: malformed namespace name")
            if any(not isinstance(name, str) for name in objects_hex):
                raise AdmissionError(f"event {i}: malformed objects_hex name")
            namespace: dict[str, list[dict[str, Any]]] = {}
            for name in sorted(namespace_raw):
                if not isinstance(namespace_raw[name], list):
                    raise AdmissionError(f"event {i}: malformed namespace")
                namespace[name] = [
                    _check_ref(ref, f"event {i} object {name}") for ref in namespace_raw[name]
                ]
            if set(objects_hex) != set(namespace):
                raise AdmissionError(f"event {i}: objects_hex names differ from namespace")
            normalized_objects: dict[str, str] = {}
            for name, value in objects_hex.items():
                normalized_objects[name] = _decode_hex(value, f"event {i} object {name!r}").hex()
            if normalized_objects != snapshots[prefix]:
                raise AdmissionError(f"event {i}: ghost namespace differs from client prefix")
            manifest_bytes = canonical_manifest(namespace)
            expected_manifest_ref = make_ref(manifest_ref["slot"], manifest_ref["generation"], manifest_bytes)
            if ref_key(expected_manifest_ref) != ref_key(manifest_ref):
                raise AdmissionError(f"event {i}: manifest reference does not bind canonical bytes")
            all_refs: list[dict[str, Any]] = [manifest_ref]
            seen = {ref_key(manifest_ref)}
            for name in sorted(namespace):
                for ref in namespace[name]:
                    key = ref_key(ref)
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
            raise AdmissionError(f"event {i}: unknown kind {kind!r}")
        events.append(event)

    _validate_complete_bindings(events, roots, data_by_key)

    closures = _compute_closures(deps)

    acknowledgements: list[dict[str, Any]] = []
    for index, raw in enumerate(acks_raw):
        if not isinstance(raw, dict):
            raise AdmissionError(f"acknowledgement {index}: malformed")
        required_fields = {"issued", "prefix", "anchors"}
        if set(raw) != required_fields:
            raise AdmissionError(f"acknowledgement {index}: fields must be {sorted(required_fields)}")
        try:
            issued = _require_int(raw["issued"], f"acknowledgement {index} issued", maximum=len(events))
            prefix = _require_int(raw["prefix"], f"acknowledgement {index} prefix", maximum=len(operations))
            anchors_raw = raw.get("anchors", [])
            if not isinstance(anchors_raw, list):
                raise AdmissionError("anchors must be a list")
            anchors = tuple(sorted(set(_require_int(x, f"acknowledgement {index} anchor", maximum=max(0, issued - 1)) for x in anchors_raw)))
        except (KeyError, AdmissionError) as exc:
            raise AdmissionError(f"acknowledgement {index}: malformed: {exc}") from exc
        if any(a < 0 or a >= issued for a in anchors):
            raise AdmissionError(f"acknowledgement {index}: anchor outside issue horizon")
        acknowledgements.append({"issued": issued, "prefix": prefix, "anchors": list(anchors)})

    return Model(
        trace={"schema": trace["schema"], "operations": operations, "events": events, "acknowledgements": acknowledgements},
        events=tuple(events),
        deps=tuple(deps),
        closures=closures,
        roots=tuple(roots),
        data_by_identity=data_by_identity,
        data_by_key=data_by_key,
        writers_by_slot={slot: tuple(ids) for slot, ids in writers_by_slot_mut.items()},
        refs_by_root=refs_by_root,
        operations=tuple(operations),
        acknowledgements=tuple(acknowledgements),
    )


def load_trace(path: str) -> dict[str, Any]:
    with open(path, "rb") as handle:
        raw = handle.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise AdmissionError("input exceeds 64 MiB")
    try:
        value = strict_json_loads(raw)
    except json.JSONDecodeError as exc:
        raise AdmissionError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AdmissionError("top-level JSON must be an object")
    return value


def save_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
