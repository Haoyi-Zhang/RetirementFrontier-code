"""Same-input, 512-operation serialized-write ratios used by the paper figure."""
from decimal import Decimal
import json
from pathlib import Path
from statistics import median


def retained_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [r for r in rows if r.get("size") == 512]
    families = ("shared", "unique", "churn")
    policies = ("no-dedup", "sync-metadata", "unchecked-dedup", "certified-dedup")
    if len(selected) != 60 or len({(r.get("family"), r.get("seed"), r.get("policy")) for r in selected}) != 60:
        raise ValueError("Expected 60 unique family/seed/policy observations")
    answer = []
    for family in families:
        by_policy = {policy: [r for r in selected if r.get("family") == family and r.get("policy") == policy] for policy in policies}
        if any(len(group) != 5 for group in by_policy.values()) or len({frozenset(r["seed"] for r in group) for group in by_policy.values()}) != 1:
            raise ValueError("Each policy must use the same five seeds")
        values = {}
        for policy, group in by_policy.items():
            ratios = []
            for row in group:
                numerator, denominator = row["encoded_write_bytes"], row["logical_input_bytes"]
                if type(numerator) is not int or numerator < 0 or type(denominator) is not int or denominator <= 0:
                    raise ValueError("Invalid serialized byte numerator or input denominator")
                value = numerator / denominator
                if type(row.get("encoded_write_ratio")) not in (int, float) or row["encoded_write_ratio"] != value:
                    raise ValueError("Stored ratio disagrees with serialized bytes / logical input bytes")
                ratios.append(Decimal(numerator) / Decimal(denominator))
            values[policy] = f"{median(ratios):.4f}"
        unchecked = {r["seed"]: r for r in by_policy["unchecked-dedup"]}
        for row in by_policy["certified-dedup"]:
            other = unchecked[row["seed"]]
            if any(row[k] != other[k] for k in ("events", "roots", "slots", "logical_input_bytes", "chunk_payload_bytes", "manifest_payload_bytes", "encoded_write_bytes")):
                raise ValueError("Certified/unchecked storage traces differ")
        answer.append(dict(family=family.title(), no_dedup=values["no-dedup"], sync_metadata=values["sync-metadata"], batch_four_dedup=values["unchecked-dedup"]))
    return answer
