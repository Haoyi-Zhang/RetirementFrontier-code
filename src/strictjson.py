"""Strict JSON decoding for trace, image, and certificate trust boundaries.

Rejects duplicate object members and JSON's non-standard NaN/Infinity tokens.
Canonical type/range checks remain the responsibility of the schema parser.
"""
from __future__ import annotations
import json
from typing import Any, IO

class DuplicateKeyError(ValueError):
    pass

def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key,value in pairs:
        if key in out:
            raise DuplicateKeyError(f"duplicate JSON object member: {key!r}")
        out[key]=value
    return out

def _constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON number is not permitted: {token}")

def loads(data: str | bytes | bytearray, **kwargs: Any) -> Any:
    if 'object_pairs_hook' in kwargs or 'parse_constant' in kwargs:
        raise TypeError('strict JSON decoder owns object_pairs_hook and parse_constant')
    return json.loads(data, object_pairs_hook=_object, parse_constant=_constant, **kwargs)

def load(fp: IO[str] | IO[bytes], **kwargs: Any) -> Any:
    return loads(fp.read(), **kwargs)
