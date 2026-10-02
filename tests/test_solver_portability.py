from __future__ import annotations
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import solver


class SolverPortabilityTests(unittest.TestCase):
    def test_system_library_does_not_require_python_package(self):
        with patch("solver.ctypes.util.find_library", return_value="libz3.so.4"), patch("solver.importlib.util.find_spec", return_value=None):
            self.assertEqual(solver._library_candidates(), ["libz3.so.4"])

    def test_explicit_skip_prevents_library_loading(self):
        with patch.dict(os.environ, {"RF_SKIP_SMT": "1"}), patch("solver.ctypes.CDLL") as loader:
            self.assertFalse(solver.solver_available())
            loader.assert_not_called()

    def test_wheel_bundled_library_is_discovered(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib/libz3.so"
            lib.parent.mkdir(); lib.touch()
            spec = types.SimpleNamespace(submodule_search_locations=[td])
            with patch("solver.ctypes.util.find_library", return_value=None), patch("solver.importlib.util.find_spec", return_value=spec):
                self.assertEqual(solver._library_candidates(), [str(lib)])

    def test_unavailable_probe_is_false_not_success(self):
        with patch("solver._library_candidates", return_value=[]):
            self.assertFalse(solver.solver_available())

    def test_missing_library_query_raises_unavailable_not_unknown(self):
        from model import admit_trace
        from targeted_cases import no_root_multi_ack
        with patch('solver._library_candidates', return_value=[]):
            with self.assertRaises(solver.SolverUnavailable):
                solver.solve_model(admit_trace(no_root_multi_ack()))

    def test_full_preflight_failure_does_not_start_campaign(self):
        import importlib.util
        import sys
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('portable_test_full', root/'scripts/reproduce_portable.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with patch.object(sys, 'argv', ['reproduce_portable.py', '--require-smt']), \
             patch.object(mod, 'solver_available', return_value=False), \
             patch.object(mod.subprocess, 'run') as runner:
            self.assertEqual(mod.main(), 2)
            runner.assert_not_called()

    def test_core_mode_records_omission_not_an_unknown_query(self):
        import importlib.util
        import json
        import sys
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('portable_test_core', root/'scripts/reproduce_portable.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td, \
             patch.object(sys, 'argv', ['reproduce_portable.py', '--without-smt']), \
             patch.object(mod, 'ROOT', Path(td)), \
             patch.object(mod, 'solver_available') as probe, \
             patch.object(mod.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as runner:
            self.assertEqual(mod.main(), 0)
            probe.assert_not_called()
            self.assertEqual(runner.call_args.kwargs['env']['RF_SKIP_SMT'], '1')
            record = json.loads((Path(td)/'results/summary/execution-mode.json').read_text())
            self.assertFalse(record['smt_executed'])
            self.assertEqual(record['mode'], 'core-without-smt')
            self.assertTrue(record['omitted_checks'])
