from __future__ import annotations
import math, pathlib, sys, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import strictjson

class StrictJsonTests(unittest.TestCase):
    def test_duplicate_member_rejected_at_any_depth(self):
        with self.assertRaises(strictjson.DuplicateKeyError): strictjson.loads('{"a":1,"a":2}')
        with self.assertRaises(strictjson.DuplicateKeyError): strictjson.loads('{"x":{"a":1,"a":2}}')
    def test_nonfinite_extensions_rejected(self):
        for token in ('NaN','Infinity','-Infinity'):
            with self.subTest(token=token), self.assertRaises(ValueError): strictjson.loads('{"x":'+token+'}')
    def test_valid_json_preserves_types(self):
        x=strictjson.loads('{"i":1,"b":true,"s":"1","n":null}')
        self.assertIs(type(x['i']),int); self.assertIs(type(x['b']),bool); self.assertEqual(x['s'],'1')
    def test_sensitive_modules_do_not_call_raw_json_load(self):
        for name in ('model.py','verifier.py','medium.py','cli.py'):
            p=ROOT/'src'/name
            if p.exists():
                text=p.read_text()
                self.assertNotIn('json.load(',text,name)
                self.assertNotIn('json.loads(',text,name)
