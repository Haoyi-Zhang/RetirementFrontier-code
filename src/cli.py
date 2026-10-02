#!/usr/bin/env python3
"""Local JSON command-line interface for the artifact."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
from strictjson import loads as strict_json_loads

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from checker import analyze_model
from fallback import fixed_candidate_model
from medium import materialize, recover
from model import AdmissionError, admit_trace, load_trace, save_json
from verifier import TraceFormatError, VerificationError, verify_certificate

MAX_BYTES = 64 * 1024 * 1024


def _load_json(path: str) -> Any:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("input exceeds 64 MiB")
    return strict_json_loads(raw)


def _print(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("certify")
    p.add_argument("trace")
    p.add_argument("--certificate")
    p.add_argument("--minimum", action="store_true")

    p = sub.add_parser("verify")
    p.add_argument("trace")
    p.add_argument("certificate")

    p = sub.add_parser("materialize")
    p.add_argument("trace")
    p.add_argument("cut")
    p.add_argument("image")

    p = sub.add_parser("recover")
    p.add_argument("image")
    p.add_argument("--fallback", action="store_true")

    p = sub.add_parser("fallback")
    p.add_argument("trace")
    p.add_argument("--max-choices", type=int, default=1_000_000)

    args = parser.parse_args(argv)
    try:
        if args.command == "certify":
            trace = load_trace(args.trace)
            result = analyze_model(admit_trace(trace), minimum=args.minimum)
            if result["safe"] and args.certificate:
                save_json(args.certificate, result["certificate"])
            _print({**result, "status": "safe" if result["safe"] else "unsafe"})
            return 0 if result["safe"] else 1
        if args.command == "verify":
            result = verify_certificate(load_trace(args.trace), _load_json(args.certificate))
            _print({**result, "status": "accepted"})
            return 0
        if args.command == "materialize":
            model = admit_trace(load_trace(args.trace))
            cut = _load_json(args.cut)
            if not isinstance(cut, list):
                raise ValueError("cut must be a JSON list")
            image = materialize(model, cut)
            save_json(args.image, image)
            _print({"written": args.image, "events": len(cut)})
            return 0
        if args.command == "recover":
            image = _load_json(args.image)
            result = recover(image, fallback=args.fallback)
            status = result.get("status", "recovered" if result.get("ok") else "unsafe")
            _print({**result, "status": status})
            return 0 if result.get("ok") else 2 if status == "malformed" else 1
        if args.command == "fallback":
            result = fixed_candidate_model(admit_trace(load_trace(args.trace)), max_choices=args.max_choices)
            _print(result)
            return 0 if result.get("safe") is True else 1 if result.get("safe") is False else 2
        raise AssertionError("unreachable")
    except TraceFormatError as exc:
        _print({"ok": False, "status": "malformed", "error": str(exc)})
        return 2
    except VerificationError as exc:
        _print({"ok": False, "status": "rejected", "error": str(exc)})
        return 2
    except (AdmissionError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "status": "malformed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
