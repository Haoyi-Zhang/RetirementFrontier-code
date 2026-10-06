"""Tiny owned parser cases and mocked whole-run deadline regressions."""
from __future__ import annotations
import importlib.util
import json
import math
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import strictjson
from medium import recover
from model import MAX_ID


class BoundaryContractTests(unittest.TestCase):
    def test_exponent_overflow_is_rejected_at_any_depth(self):
        for token in ('1e400', '-1e400', '9.99e9999'):
            for text in (token, '{"x":['+token+']}'):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    strictjson.loads(text)

    def test_finite_floats_keep_json_types(self):
        values = strictjson.loads('[1e308,1e-400,0.5,1,true]')
        self.assertTrue(all(math.isfinite(x) for x in values))
        self.assertEqual(values[1], 0.0)
        self.assertIs(type(values[0]), float)
        self.assertIs(type(values[3]), int)
        self.assertIs(type(values[4]), bool)

    def test_finite_float_hook_cannot_be_bypassed(self):
        with self.assertRaises(TypeError):
            strictjson.loads('1e400', parse_float=lambda token: float(token))

    def image(self, key, slot=0):
        return {'schema':'retirement-frontier-image-v1','roots':[],
                'slots':{key:{'kind':'free','event':0,'slot':slot,'generation':0}}}

    def test_noncanonical_slot_keys_return_malformed(self):
        for key in ('\u00b2', '\u0660', '00', '9'*4301, str(MAX_ID+1)):
            for fallback in (False, True):
                with self.subTest(key=key[:25], fallback=fallback):
                    result = recover(self.image(key), fallback=fallback)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['status'], 'malformed')

    def test_canonical_slot_bounds_still_recover(self):
        for slot in (0, MAX_ID):
            result = recover(self.image(str(slot), slot))
            self.assertTrue(result['ok'])
            self.assertEqual(result['prefix'], 0)

    def runner(self):
        path=Path(__file__).resolve().parents[1]/'scripts/run_all.py'
        spec=importlib.util.spec_from_file_location('bounded_campaign_test',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_campaign_stages_share_one_wall_deadline(self):
        runner=self.runner()
        paths={stage:Mock() for stage in ('tests','pilots')}
        for path in paths.values():
            path.is_file.return_value=True
            path.read_text.return_value=json.dumps({'cpu_seconds':0,'max_rss_kib':0})
        with patch.object(runner,'STAGES',('tests','pilots')), \
             patch.object(runner,'RESULT_PATHS',paths), \
             patch.object(runner,'_save_json'), \
             patch.object(runner.time,'perf_counter',side_effect=[0,10,20,30]), \
             patch.object(runner.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertEqual(runner.main(),0)
            self.assertEqual([c.kwargs['timeout'] for c in run.call_args_list],
                             [runner.CAMPAIGN_TIMEOUT_SECONDS-10,
                              runner.CAMPAIGN_TIMEOUT_SECONDS-20])

    def test_expired_campaign_does_not_launch_another_stage(self):
        runner=self.runner()
        with patch.object(runner.time,'perf_counter',side_effect=[0,2701]), \
             patch.object(runner.subprocess,'run') as run:
            with self.assertRaises(TimeoutError):
                runner.main()
            run.assert_not_called()


if __name__=='__main__':
    unittest.main()
