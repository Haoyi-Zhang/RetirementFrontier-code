"""Deterministic byte-bearing trace generator for the modeled store."""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
import random
from typing import Any, Iterable

from model import canonical_json_bytes, canonical_manifest, digest_hex, make_ref, ref_key


@dataclass
class BuildMetrics:
    logical_input_bytes: int = 0
    chunk_payload_bytes: int = 0
    manifest_payload_bytes: int = 0
    data_record_bytes: int = 0
    root_record_bytes: int = 0
    free_record_bytes: int = 0
    resident_payload_bytes: int = 0
    resident_payload_highwater: int = 0

    @property
    def encoded_write_bytes(self) -> int:
        return self.data_record_bytes + self.root_record_bytes + self.free_record_bytes


class TraceBuilder:
    def __init__(self, *, deduplicate: bool, batch_size: int) -> None:
        self.deduplicate = bool(deduplicate)
        self.batch_size = int(batch_size)
        self.operations: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.acks: list[dict[str, Any]] = []
        self.namespace: dict[str, list[dict[str, Any]]] = {}
        self.objects: dict[str, bytes] = {}
        self.prev_root: int | None = None
        self.current_manifest_id: int | None = None
        self.active_data: set[int] = set()
        self.content_index: dict[tuple[str, int, str], dict[str, Any]] = {}
        self.identity_to_event: dict[tuple[int, int], int] = {}
        self.slot_generation: dict[int, int] = {}
        self.slot_writer: dict[int, int] = {}
        self.slot_payload: dict[int, int] = {}
        self.free_slots: list[int] = []
        self.next_slot = 0
        self.metrics = BuildMetrics()

    def _content_key(self, data: bytes) -> tuple[str, int, str]:
        return (digest_hex(data), len(data), data.hex())

    def _new_event(self, event: dict[str, Any]) -> int:
        event_id = len(self.events)
        event = dict(event)
        event["id"] = event_id
        event["deps"] = sorted(set(int(x) for x in event.get("deps", [])))
        self.events.append(event)
        return event_id

    def _allocate_slot(self) -> tuple[int, int, list[int]]:
        if self.free_slots:
            slot = heapq.heappop(self.free_slots)
        else:
            slot = self.next_slot
            self.next_slot += 1
        generation = self.slot_generation.get(slot, 0) + 1
        self.slot_generation[slot] = generation
        deps = [self.slot_writer[slot]] if slot in self.slot_writer else []
        return slot, generation, deps

    def _record_data_bytes(self, event: dict[str, Any], data: bytes) -> None:
        descriptor = {
            "kind": "data",
            "slot": event["slot"],
            "generation": event["generation"],
            "digest": event["digest"],
            "length": event["length"],
            "role": event["role"],
        }
        encoded = len(canonical_json_bytes(descriptor)) + 1 + len(data)
        self.metrics.data_record_bytes += encoded
        if event["role"] == "chunk":
            self.metrics.chunk_payload_bytes += len(data)
        else:
            self.metrics.manifest_payload_bytes += len(data)
        slot = int(event["slot"])
        self.metrics.resident_payload_bytes -= self.slot_payload.get(slot, 0)
        self.slot_payload[slot] = len(data)
        self.metrics.resident_payload_bytes += len(data)
        self.metrics.resident_payload_highwater = max(
            self.metrics.resident_payload_highwater, self.metrics.resident_payload_bytes
        )

    def _allocate(self, data: bytes, role: str) -> tuple[dict[str, Any], int]:
        slot, generation, deps = self._allocate_slot()
        ref = make_ref(slot, generation, data)
        event = {
            "kind": "data",
            "slot": slot,
            "generation": generation,
            "digest": ref["digest"],
            "length": ref["length"],
            "data_hex": data.hex(),
            "role": role,
            "deps": deps,
        }
        event_id = self._new_event(event)
        self.slot_writer[slot] = event_id
        self.active_data.add(event_id)
        self.identity_to_event[(slot, generation)] = event_id
        self._record_data_bytes(self.events[event_id], data)
        return ref, event_id

    def _chunk_ref(self, data: bytes) -> dict[str, Any]:
        key = self._content_key(data)
        if self.deduplicate and key in self.content_index:
            return dict(self.content_index[key])
        ref, _ = self._allocate(data, "chunk")
        if self.deduplicate:
            self.content_index[key] = dict(ref)
        return ref

    def _free_data(self, data_event_id: int, root_id: int) -> int:
        data_event = self.events[data_event_id]
        slot = int(data_event["slot"])
        deps = [root_id]
        if slot in self.slot_writer:
            deps.append(self.slot_writer[slot])
        free_id = self._new_event({
            "kind": "free",
            "slot": slot,
            "generation": int(data_event["generation"]),
            "deps": deps,
        })
        self.slot_writer[slot] = free_id
        self.metrics.resident_payload_bytes -= self.slot_payload.get(slot, 0)
        self.slot_payload[slot] = 0
        descriptor = {"kind": "free", "slot": slot, "generation": int(data_event["generation"])}
        self.metrics.free_record_bytes += len(canonical_json_bytes(descriptor)) + 1
        heapq.heappush(self.free_slots, slot)
        self.active_data.discard(data_event_id)
        if data_event["role"] == "chunk":
            data = bytes.fromhex(data_event["data_hex"])
            key = self._content_key(data)
            existing = self.content_index.get(key)
            if existing is not None and ref_key(existing) == (
                slot,
                int(data_event["generation"]),
                data_event["digest"],
                int(data_event["length"]),
            ):
                self.content_index.pop(key, None)
        return free_id

    def _apply_operation(self, op: dict[str, Any]) -> None:
        name = op["name"]
        if op["op"] == "delete":
            self.namespace.pop(name, None)
            self.objects.pop(name, None)
            return
        chunks = [bytes.fromhex(x) for x in op["chunks_hex"]]
        refs = [self._chunk_ref(chunk) for chunk in chunks]
        self.namespace[name] = refs
        self.objects[name] = b"".join(chunks)
        self.metrics.logical_input_bytes += sum(len(c) for c in chunks)

    def commit_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        for op in batch:
            self.operations.append(dict(op))
            self._apply_operation(op)

        manifest_bytes = canonical_manifest(self.namespace)
        manifest_ref, manifest_id = self._allocate(manifest_bytes, "manifest")
        root_deps = [manifest_id]
        seen_allocs: set[int] = set()
        for name in sorted(self.namespace):
            for ref in self.namespace[name]:
                alloc_id = self.identity_to_event[(int(ref["slot"]), int(ref["generation"]))]
                if alloc_id not in seen_allocs:
                    root_deps.append(alloc_id)
                    seen_allocs.add(alloc_id)
        if self.prev_root is not None:
            root_deps.append(self.prev_root)
        root_event = {
            "kind": "root",
            "prefix": len(self.operations),
            "manifest_ref": manifest_ref,
            "namespace": {name: [dict(r) for r in self.namespace[name]] for name in sorted(self.namespace)},
            "objects_hex": {name: self.objects[name].hex() for name in sorted(self.objects)},
            "deps": root_deps,
        }
        root_id = self._new_event(root_event)
        root_descriptor = {"kind": "root", "prefix": len(self.operations), "manifest_ref": manifest_ref}
        self.metrics.root_record_bytes += len(canonical_json_bytes(root_descriptor)) + 1
        self.acks.append({"issued": root_id + 1, "prefix": len(self.operations), "anchors": [root_id]})
        self.prev_root = root_id

        keep: set[int] = {manifest_id}
        for refs in self.namespace.values():
            for ref in refs:
                keep.add(self.identity_to_event[(int(ref["slot"]), int(ref["generation"]))])
        obsolete = sorted(self.active_data - keep)
        for data_event_id in obsolete:
            self._free_data(data_event_id, root_id)
        self.current_manifest_id = manifest_id

    def finish(self, operations: Iterable[dict[str, Any]]) -> dict[str, Any]:
        batch: list[dict[str, Any]] = []
        for op in operations:
            batch.append(dict(op))
            if len(batch) == self.batch_size:
                self.commit_batch(batch)
                batch = []
        if batch:
            self.commit_batch(batch)
        return {
            "schema": "retirement-frontier-trace-v1",
            "operations": self.operations,
            "events": self.events,
            "acknowledgements": self.acks,
            "build_metrics": {
                "logical_input_bytes": self.metrics.logical_input_bytes,
                "chunk_payload_bytes": self.metrics.chunk_payload_bytes,
                "manifest_payload_bytes": self.metrics.manifest_payload_bytes,
                "data_record_bytes": self.metrics.data_record_bytes,
                "root_record_bytes": self.metrics.root_record_bytes,
                "free_record_bytes": self.metrics.free_record_bytes,
                "encoded_write_bytes": self.metrics.encoded_write_bytes,
                "resident_payload_highwater": self.metrics.resident_payload_highwater,
            },
        }


def build_trace(operations: Iterable[dict[str, Any]], *, deduplicate: bool = True, batch_size: int = 4) -> dict[str, Any]:
    return TraceBuilder(deduplicate=deduplicate, batch_size=batch_size).finish(operations)


def strip_build_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in trace.items() if k != "build_metrics"}


def heldout_operations(seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    names = ["alpha", "beta"]
    chunks = [b"a", b"b", b"aa"]
    result: list[dict[str, Any]] = []
    for _ in range(4):
        name = names[rng.randrange(len(names))]
        if rng.randrange(4) == 0:
            result.append({"op": "delete", "name": name})
        else:
            chunk = chunks[rng.randrange(len(chunks))]
            result.append({"op": "put", "name": name, "chunks_hex": [chunk.hex()]})
    return result


def weaken_trace(trace: dict[str, Any], *, seed: int, retention: float = 0.65) -> dict[str, Any]:
    copied = json.loads(json.dumps(strip_build_metrics(trace)))
    rng = random.Random(seed)
    for event in copied["events"]:
        event["deps"] = [dep for dep in event.get("deps", []) if rng.random() < retention]
    return copied


def edge_free_trace(trace: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(strip_build_metrics(trace)))
    for event in copied["events"]:
        event["deps"] = []
    return copied


def unanchor_first_ack(trace: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(strip_build_metrics(trace)))
    if copied["acknowledgements"]:
        copied["acknowledgements"][0]["anchors"] = []
    return copied


def scaling_operations(size: int, family: str, seed: int, chunk_size: int = 256) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    names = [f"obj-{i}" for i in range(8)]
    shared_pool = [bytes([i + 1]) * chunk_size for i in range(4)]
    churn_pool = [bytes([(i % 251) + 1]) * chunk_size for i in range(32)]
    result: list[dict[str, Any]] = []
    for op_index in range(size):
        name = names[rng.randrange(len(names))]
        if family == "churn" and rng.randrange(4) == 0:
            result.append({"op": "delete", "name": name})
            continue
        chunks: list[bytes] = []
        for chunk_index in range(4):
            if family == "shared":
                chunks.append(shared_pool[rng.randrange(len(shared_pool))])
            elif family == "unique":
                # Deterministic incompressible-enough content without external randomness.
                token = f"{seed}:{op_index}:{chunk_index}:".encode()
                data = bytearray()
                counter = 0
                while len(data) < chunk_size:
                    import hashlib
                    data.extend(hashlib.sha256(token + str(counter).encode()).digest())
                    counter += 1
                chunks.append(bytes(data[:chunk_size]))
            elif family == "churn":
                chunks.append(churn_pool[rng.randrange(len(churn_pool))])
            else:
                raise ValueError(f"unknown family {family}")
        result.append({"op": "put", "name": name, "chunks_hex": [c.hex() for c in chunks]})
    return result


def public_replay_operations(files_old: dict[str, bytes], files_new: dict[str, bytes], chunk_size: int) -> list[dict[str, Any]]:
    def chunks(data: bytes) -> list[str]:
        if not data:
            return [""]
        return [data[i : i + chunk_size].hex() for i in range(0, len(data), chunk_size)]

    names = sorted(files_old)
    ops: list[dict[str, Any]] = []
    for name in names:
        ops.append({"op": "put", "name": name, "chunks_hex": chunks(files_old[name])})
    for name in names:
        ops.append({"op": "put", "name": f"clone-{name}", "chunks_hex": chunks(files_old[name])})
    for name in names:
        ops.append({"op": "put", "name": name, "chunks_hex": chunks(files_new[name])})
    for name in names:
        ops.append({"op": "delete", "name": name})
    for name in names:
        ops.append({"op": "delete", "name": f"clone-{name}"})
    for name in names:
        ops.append({"op": "put", "name": name, "chunks_hex": chunks(files_old[name])})
    return ops


def _manual_data(event_id: int, slot: int, generation: int, data: bytes, role: str, deps: list[int]) -> dict[str, Any]:
    ref = make_ref(slot, generation, data)
    return {
        "id": event_id,
        "kind": "data",
        "slot": slot,
        "generation": generation,
        "digest": ref["digest"],
        "length": ref["length"],
        "data_hex": data.hex(),
        "role": role,
        "deps": deps,
    }


def census_template(family: str, edge_mask: int) -> dict[str, Any]:
    if family == "unretired-reuse":
        data_a = b"a"
        ref_a = make_ref(0, 1, data_a)
        namespace = {"x": [ref_a]}
        manifest = canonical_manifest(namespace)
        manifest_ref = make_ref(1, 1, manifest)
        events: list[dict[str, Any]] = [
            _manual_data(0, 0, 1, data_a, "chunk", []),
            _manual_data(1, 1, 1, manifest, "manifest", []),
            {"id": 2, "kind": "root", "prefix": 1, "manifest_ref": manifest_ref, "namespace": namespace, "objects_hex": {"x": data_a.hex()}, "deps": []},
            {"id": 3, "kind": "free", "slot": 0, "generation": 1, "deps": []},
            _manual_data(4, 0, 2, b"z", "chunk", []),
        ]
        operations = [{"op": "put", "name": "x", "chunks_hex": [data_a.hex()]}]
        acknowledgements: list[dict[str, Any]] = []
    elif family == "retired-free":
        data_a = b"a"
        ref_a = make_ref(0, 1, data_a)
        ns1 = {"x": [ref_a]}
        m1 = canonical_manifest(ns1)
        m1ref = make_ref(1, 1, m1)
        ns2: dict[str, list[dict[str, Any]]] = {}
        m2 = canonical_manifest(ns2)
        m2ref = make_ref(2, 1, m2)
        events = [
            _manual_data(0, 0, 1, data_a, "chunk", []),
            _manual_data(1, 1, 1, m1, "manifest", []),
            {"id": 2, "kind": "root", "prefix": 1, "manifest_ref": m1ref, "namespace": ns1, "objects_hex": {"x": data_a.hex()}, "deps": []},
            _manual_data(3, 2, 1, m2, "manifest", []),
            {"id": 4, "kind": "root", "prefix": 2, "manifest_ref": m2ref, "namespace": ns2, "objects_hex": {}, "deps": []},
            {"id": 5, "kind": "free", "slot": 0, "generation": 1, "deps": []},
        ]
        operations = [
            {"op": "put", "name": "x", "chunks_hex": [data_a.hex()]},
            {"op": "delete", "name": "x"},
        ]
        acknowledgements = []
    else:
        raise ValueError(f"unknown census family {family}")
    bit = 0
    for i in range(len(events)):
        deps: list[int] = []
        for j in range(i):
            if edge_mask & (1 << bit):
                deps.append(j)
            bit += 1
        events[i]["deps"] = deps
    return {
        "schema": "retirement-frontier-trace-v1",
        "operations": operations,
        "events": events,
        "acknowledgements": acknowledgements,
    }
