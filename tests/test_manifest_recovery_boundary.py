"""Expected manifest decoding failures preserve recovery's public outcomes."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cli import main
from medium import recover
from model import make_ref


def image_with_manifest(payload: bytes) -> dict:
    slots = {}
    roots = []
    for slot, event, prefix, data in ((0, 0, 1, b"{}"), (1, 2, 2, payload)):
        ref = make_ref(slot, 1, data)
        slots[str(slot)] = {"kind": "data", "event": event, **ref,
                            "data_hex": data.hex(), "role": "manifest"}
        roots.append({"event": event + 1, "prefix": prefix, "manifest_ref": ref})
    return {"schema": "retirement-frontier-image-v1", "slots": slots, "roots": roots}


class ManifestRecoveryBoundaryTests(unittest.TestCase):
    payloads = (b'{"x":[],"x":[]}', b'{"x":NaN}', b'{"x":1e9999}', b'\xff')

    def test_strict_recovery_reports_unrecoverable_root(self):
        for payload in self.payloads:
            with self.subTest(payload=payload):
                result = recover(image_with_manifest(payload))
                self.assertFalse(result["ok"])
                self.assertEqual(result["root_event"], 3)
                self.assertIn("canonical JSON", result["reason"])

    def test_fallback_recovers_the_older_valid_root(self):
        for payload in self.payloads:
            with self.subTest(payload=payload):
                result = recover(image_with_manifest(payload), fallback=True)
                self.assertTrue(result["ok"])
                self.assertEqual(result["root_event"], 1)
                self.assertEqual(result["prefix"], 1)
                self.assertEqual(result["objects_hex"], {})

    def test_cli_preserves_strict_and_fallback_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "image.json"
            for payload in self.payloads:
                path.write_text(json.dumps(image_with_manifest(payload)), encoding="utf-8")
                for fallback, code, status in ((False, 1, "unsafe"), (True, 0, "recovered")):
                    with self.subTest(payload=payload, fallback=fallback):
                        output = io.StringIO()
                        args = ["recover", str(path)] + (["--fallback"] if fallback else [])
                        with contextlib.redirect_stdout(output):
                            actual = main(args)
                        self.assertEqual(actual, code)
                        self.assertEqual(json.loads(output.getvalue())["status"], status)
