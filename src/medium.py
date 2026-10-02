"""Materialized crash images and strict/fallback recovery."""
from __future__ import annotations
from strictjson import loads as strict_json_loads

import json
from typing import Any, Iterable

from model import MAX_ID, Model, canonical_json_bytes, digest_hex, ref_key


class RecoveryError(ValueError):
    """Raised when a materialized image cannot be interpreted safely."""


def _require_int(value: Any, where: str, *, minimum: int = 0, maximum: int = MAX_ID) -> int:
    """Parse a JSON integer without accepting booleans, floats, or numeric strings."""
    if type(value) is not int:
        raise RecoveryError(f"{where}: must be an integer")
    if value < minimum or value > maximum:
        raise RecoveryError(f"{where}: integer out of range")
    return value


def _decode_hex(value: Any, where: str) -> bytes:
    if not isinstance(value, str) or len(value) % 2 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise RecoveryError(f"{where}: invalid hexadecimal string")
    return bytes.fromhex(value)


def materialize(model: Model, cut: Iterable[int], require_legal: bool = True) -> dict[str, Any]:
    ids = sorted(set(_require_int(x, "cut event", maximum=max(0, model.n - 1)) for x in cut))
    if any(i < 0 or i >= model.n for i in ids):
        raise RecoveryError("cut contains an out-of-range event")
    if require_legal and not model.is_legal_cut(ids):
        raise RecoveryError("cut is not dependency closed")
    slots: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for i in ids:
        event = model.events[i]
        if event["kind"] == "data":
            slots[str(event["slot"])] = {
                "kind": "data",
                "event": i,
                "slot": event["slot"],
                "generation": event["generation"],
                "digest": event["digest"],
                "length": event["length"],
                "data_hex": event["data_hex"],
                "role": event["role"],
            }
        elif event["kind"] == "free":
            slots[str(event["slot"])] = {
                "kind": "free",
                "event": i,
                "slot": event["slot"],
                "generation": event.get("generation", 0),
            }
        elif event["kind"] == "root":
            roots.append({
                "event": i,
                "prefix": event["prefix"],
                "manifest_ref": event["manifest_ref"],
            })
    return {"schema": "retirement-frontier-image-v1", "slots": slots, "roots": roots}


def _normalize_ref(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecoveryError(f"{where}: reference is not an object")
    required = {"slot", "generation", "digest", "length"}
    if set(raw) != required:
        raise RecoveryError(f"{where}: reference fields are invalid")
    slot = _require_int(raw["slot"], f"{where} slot")
    generation = _require_int(raw["generation"], f"{where} generation", minimum=1)
    length = _require_int(raw["length"], f"{where} length")
    digest = raw["digest"]
    if not isinstance(digest, str):
        raise RecoveryError(f"{where}: digest must be a string")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise RecoveryError(f"{where}: digest is not lower-case SHA-256 hex")
    return {"slot": slot, "generation": generation, "digest": digest, "length": length}


def _normalize_roots(image: dict[str, Any]) -> list[dict[str, Any]]:
    roots_raw = image.get("roots")
    if not isinstance(roots_raw, list):
        raise RecoveryError("image roots must be a list")
    roots: list[dict[str, Any]] = []
    seen_events: set[int] = set()
    for index, raw in enumerate(roots_raw):
        if not isinstance(raw, dict) or set(raw) != {"event", "prefix", "manifest_ref"}:
            raise RecoveryError(f"root marker {index} is malformed")
        event = _require_int(raw["event"], f"root marker {index} event")
        prefix = _require_int(raw["prefix"], f"root marker {index} prefix")
        if event in seen_events:
            raise RecoveryError(f"root marker {index} has an invalid identity")
        seen_events.add(event)
        roots.append({
            "event": event,
            "prefix": prefix,
            "manifest_ref": _normalize_ref(raw["manifest_ref"], f"root marker {index}"),
        })
    ascending = sorted(roots, key=lambda root: root["event"])
    for previous, current in zip(ascending, ascending[1:]):
        if current["prefix"] <= previous["prefix"]:
            raise RecoveryError("root prefixes do not increase with root event order")
    return list(reversed(ascending))


def _slots(image: dict[str, Any]) -> dict[str, Any]:
    slots = image.get("slots")
    if not isinstance(slots, dict):
        raise RecoveryError("image slots must be an object")
    return slots


def _slot_summary(image: dict[str, Any]) -> tuple[list[int], set[int]]:
    occupied: list[int] = []
    events: set[int] = set()
    for key, record in _slots(image).items():
        if not isinstance(key, str) or not key.isdigit():
            raise RecoveryError("image contains a non-integer slot key")
        slot = int(key)
        if not (0 <= slot <= MAX_ID) or str(slot) != key:
            raise RecoveryError("image contains a noncanonical slot key")
        if (not isinstance(record, dict)
                or not isinstance(record.get("kind"), str)
                or record["kind"] not in {"data", "free"}):
            raise RecoveryError(f"slot {slot} has a malformed record")
        if record["kind"] == "data":
            required = {"kind", "event", "slot", "generation", "digest", "length", "data_hex", "role"}
        else:
            required = {"kind", "event", "slot", "generation"}
        if set(record) != required:
            raise RecoveryError(f"slot {slot} has malformed {record['kind']}-record fields")
        event = _require_int(record["event"], f"slot {slot} event")
        if event in events:
            raise RecoveryError("image reuses one event identifier for multiple slot records")
        events.add(event)
        record_slot = _require_int(record["slot"], f"slot {slot} record slot")
        if record_slot != slot:
            raise RecoveryError(f"slot {slot} record is stored under the wrong key")
        if record["kind"] == "data":
            _require_int(record["generation"], f"slot {slot} generation", minimum=1)
            length = _require_int(record["length"], f"slot {slot} length")
            digest = record["digest"]
            if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise RecoveryError(f"slot {slot} has an invalid digest")
            if (not isinstance(record["role"], str)
                    or record["role"] not in {"chunk", "manifest"}):
                raise RecoveryError(f"slot {slot} has an invalid data role")
            data = _decode_hex(record["data_hex"], f"slot {slot} data")
            if len(data) != length or digest_hex(data) != digest:
                raise RecoveryError(f"slot {slot} fails length/digest validation")
            occupied.append(slot)
        else:
            _require_int(record["generation"], f"slot {slot} generation")
    return sorted(occupied), events


def _occupied_slots(image: dict[str, Any]) -> list[int]:
    return _slot_summary(image)[0]


def _read_ref(image: dict[str, Any], raw_ref: dict[str, Any], expected_role: str) -> bytes:
    ref = _normalize_ref(raw_ref, "manifest entry")
    record = _slots(image).get(str(ref["slot"]))
    if not isinstance(record, dict) or record.get("kind") != "data":
        raise RecoveryError(f"slot {ref['slot']} is absent or free")
    required = {"kind", "event", "slot", "generation", "digest", "length", "data_hex", "role"}
    if set(record) != required:
        raise RecoveryError(f"slot {ref['slot']} has malformed data-record fields")
    record_slot = _require_int(record["slot"], f"slot {ref['slot']} record slot")
    generation = _require_int(record["generation"], f"slot {ref['slot']} generation", minimum=1)
    length = _require_int(record["length"], f"slot {ref['slot']} length")
    event = _require_int(record["event"], f"slot {ref['slot']} event")
    if record_slot != ref["slot"]:
        raise RecoveryError(f"slot {ref['slot']} has inconsistent record identity")
    if generation != ref["generation"]:
        raise RecoveryError(f"slot {ref['slot']} has the wrong generation")
    if record.get("role") != expected_role:
        raise RecoveryError(f"slot {ref['slot']} has the wrong data role")
    if record.get("digest") != ref["digest"] or length != ref["length"]:
        raise RecoveryError(f"slot {ref['slot']} has inconsistent record metadata")
    data = _decode_hex(record["data_hex"], "stored data encoding")
    if len(data) != ref["length"] or digest_hex(data) != ref["digest"]:
        raise RecoveryError(f"slot {ref['slot']} fails length/digest validation")
    return data


def _recover_root(image: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    manifest_bytes = _read_ref(image, root["manifest_ref"], "manifest")
    try:
        namespace = strict_json_loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("manifest bytes are not canonical JSON") from exc
    if canonical_json_bytes(namespace) != manifest_bytes:
        raise RecoveryError("manifest JSON is not canonical")
    if not isinstance(namespace, dict):
        raise RecoveryError("manifest is not a namespace object")
    if any(not isinstance(name, str) for name in namespace):
        raise RecoveryError("manifest contains a non-string object name")

    objects_hex: dict[str, str] = {}
    reference_counts: dict[str, int] = {}
    reachable_slots = {int(root["manifest_ref"]["slot"])}
    for name in sorted(namespace):
        refs = namespace[name]
        if not isinstance(refs, list):
            raise RecoveryError("malformed manifest entry")
        chunks: list[bytes] = []
        for index, raw_ref in enumerate(refs):
            ref = _normalize_ref(raw_ref, f"object {name!r} reference {index}")
            chunks.append(_read_ref(image, ref, "chunk"))
            key = ":".join(map(str, ref_key(ref)))
            reference_counts[key] = reference_counts.get(key, 0) + 1
            reachable_slots.add(int(ref["slot"]))
        objects_hex[name] = b"".join(chunks).hex()
    occupied = set(_occupied_slots(image))
    return {
        "ok": True,
        "prefix": int(root["prefix"]),
        "root_event": int(root["event"]),
        "objects_hex": objects_hex,
        "reference_counts": reference_counts,
        "reachable_slots": sorted(reachable_slots),
        "unreachable_slots": sorted(occupied - reachable_slots),
        "root_history": len(image.get("roots", [])),
    }


def recover(image: dict[str, Any], fallback: bool = False) -> dict[str, Any]:
    if (
        not isinstance(image, dict)
        or set(image) != {"schema", "slots", "roots"}
        or image.get("schema") != "retirement-frontier-image-v1"
    ):
        return {"ok": False, "status": "malformed", "reason": "unsupported image schema"}
    try:
        roots = _normalize_roots(image)
        occupied, slot_events = _slot_summary(image)
        root_events = {int(root["event"]) for root in roots}
        if root_events & slot_events:
            raise RecoveryError("image reuses one event identifier across roots and slots")
    except RecoveryError as exc:
        return {"ok": False, "status": "malformed", "reason": str(exc)}

    if not roots:
        return {
            "ok": True,
            "prefix": 0,
            "root_event": None,
            "objects_hex": {},
            "reference_counts": {},
            "reachable_slots": [],
            "unreachable_slots": occupied,
            "root_history": 0,
        }
    if not fallback:
        try:
            return _recover_root(image, roots[0])
        except RecoveryError as exc:
            return {
                "ok": False,
                "reason": str(exc),
                "root_event": int(roots[0]["event"]),
                "root_history": len(roots),
            }

    failures: list[dict[str, Any]] = []
    for root in roots:
        try:
            return _recover_root(image, root)
        except RecoveryError as exc:
            failures.append({"root_event": int(root["event"]), "reason": str(exc)})
    return {
        "ok": True,
        "prefix": 0,
        "root_event": None,
        "objects_hex": {},
        "reference_counts": {},
        "reachable_slots": [],
        "unreachable_slots": occupied,
        "root_history": len(roots),
        "skipped": failures,
    }


def erase_slots(image: dict[str, Any], slots: Iterable[int]) -> dict[str, Any]:
    copied = strict_json_loads(json.dumps(image))
    for slot in slots:
        copied.get("slots", {}).pop(str(int(slot)), None)
    return copied
