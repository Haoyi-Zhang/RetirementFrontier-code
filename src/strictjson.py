"""Strict JSON decoding for trace, image, and certificate trust boundaries.

Rejects duplicate object members, JSON's non-standard NaN/Infinity tokens,
and exponent notation that overflows the decoder's finite float range.
Canonical type/range checks remain the responsibility of the schema parser.
"""
from __future__ import annotations
import json
import math
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

def _float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"JSON number overflows the finite float range: {token}")
    return value

def loads(data: str | bytes | bytearray, **kwargs: Any) -> Any:
    if any(key in kwargs for key in ('object_pairs_hook', 'parse_constant', 'parse_float')):
        raise TypeError('strict JSON decoder owns object_pairs_hook, parse_constant and parse_float')
    return json.loads(data, object_pairs_hook=_object, parse_constant=_constant,
                      parse_float=_float, **kwargs)

def load(fp: IO[str] | IO[bytes], **kwargs: Any) -> Any:
    return loads(fp.read(), **kwargs)
