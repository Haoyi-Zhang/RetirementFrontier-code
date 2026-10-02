#!/usr/bin/env python3
"""Run the artifact unit tests from any working directory.

This entry point deliberately owns sys.path setup, so the documented command
works in a clean unpack without shell-specific PYTHONPATH syntax.
"""
from __future__ import annotations
import argparse
import os
import pathlib
import sys
import unittest

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="verify clean-root import setup without running discovery")
    args = parser.parse_args()
    sys.path.insert(0, str(SRC))
    if args.smoke:
        __import__("model")
        print("clean-root import smoke test passed")
        return 0
    suite = unittest.defaultTestLoader.discover(str(TESTS), pattern="test*.py")
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
