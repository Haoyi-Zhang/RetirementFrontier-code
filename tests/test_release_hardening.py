from __future__ import annotations
import importlib.util
import os
import pathlib
import subprocess
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class ReleaseHardeningTests(unittest.TestCase):
    def test_clean_root_runner_works_without_pythonpath(self):
        env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
        cp=subprocess.run([sys.executable, str(ROOT/'scripts'/'run_tests.py'), '--smoke'],
                          cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env, timeout=30)
        self.assertEqual(cp.returncode,0,cp.stdout[-2000:])

    def test_release_normalizer_ignores_only_host_fields(self):
        spec=importlib.util.spec_from_file_location('release_validator', ROOT/'scripts'/'validate_release.py')
        mod=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod)
        a={'decision':True,'count':7,'wall_seconds':1.0,'nested':{'recovery_median_ns':10}}
        b={'decision':True,'count':7,'wall_seconds':999.0,'nested':{'recovery_median_ns':20}}
        self.assertEqual(mod.canonical(a),mod.canonical(b))
        b['count']=8
        self.assertNotEqual(mod.canonical(a),mod.canonical(b))

    def test_last_user_scalar_equals_all_threatened_root_inequalities(self):
        # Exhaust all finite last-user sets and forced-root values in this
        # bounded ordinal universe, including the empty and repeated cases.
        from itertools import product
        for count in range(5):
            for last_users in product(range(-1, 5), repeat=count):
                threatened = [r for r in last_users if r >= 0]
                frontier = max(threatened, default=-1)
                for guard in range(-1, 6):
                    pair = all(guard > r for r in threatened)
                    scalar = frontier < 0 or guard > frontier
                    self.assertEqual(pair, scalar)

if __name__=='__main__': unittest.main()
