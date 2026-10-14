"""The optional current-paper plot retains its observations and denominator."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("write_ratio_data", ROOT / "plots/write_ratio_data.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PlotSources(unittest.TestCase):
    def test_retained_medians(self):
        self.assertEqual(module.retained_rows(ROOT / "results/scaling/policy-metrics.jsonl"), [
            dict(family="Shared", no_dedup="2.7352", sync_metadata="3.9734", batch_four_dedup="0.9977"),
            dict(family="Unique", no_dedup="2.7324", sync_metadata="5.7398", batch_four_dedup="2.7324"),
            dict(family="Churn", no_dedup="2.7679", sync_metadata="4.9086", batch_four_dedup="1.7176")])

    def malformed(self, change):
        rows = [json.loads(x) for x in (ROOT / "results/scaling/policy-metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        rows = copy.deepcopy([r for r in rows if r["size"] == 512])
        change(rows)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.retained_rows(path)

    def test_duplicate_rejected(self):
        self.malformed(lambda rows: rows.__setitem__(1, rows[0]))

    def test_bad_denominator_rejected(self):
        self.malformed(lambda rows: rows[0].__setitem__("logical_input_bytes", 0))

    def test_stale_ratio_rejected(self):
        self.malformed(lambda rows: rows[0].__setitem__("encoded_write_ratio", 0))


if __name__ == "__main__":
    unittest.main()
