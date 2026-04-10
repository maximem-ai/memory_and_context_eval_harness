"""
Prepare an interleaved LongMemEval dataset.

The original longmemeval_oracle.json is sorted by question type, so taking
the first N records only gives one category. This script round-robins across
all 6 question types to create a balanced order.

Usage:
    python scripts/prepare_longmemeval_interleaved.py

Output:
    datasets/longmemeval/longmemeval_interleaved.json
"""

import json
import os
from collections import defaultdict

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATASET_DIR = os.path.join(os.path.dirname(_DIR), "datasets", "longmemeval")

INPUT_FILE = os.path.join(_DATASET_DIR, "longmemeval_oracle.json")
OUTPUT_FILE = os.path.join(_DATASET_DIR, "longmemeval_interleaved.json")


def interleave(records: list) -> list:
    """Round-robin across question types to produce balanced ordering."""
    by_type: dict[str, list] = defaultdict(list)
    for record in records:
        by_type[record.get("question_type", "unknown")].append(record)

    # Sort type names for deterministic ordering
    type_names = sorted(by_type.keys())
    queues = {t: list(by_type[t]) for t in type_names}

    result = []
    while any(queues.values()):
        for t in type_names:
            if queues[t]:
                result.append(queues[t].pop(0))

    return result


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"ERROR: Input file not found: {INPUT_FILE}")
        print("Run: python scripts/download_datasets.py --dataset longmemeval")
        return

    with open(INPUT_FILE, "r") as f:
        raw = json.load(f)

    print(f"Loaded {len(raw)} records from {INPUT_FILE}")

    # Show original distribution
    from collections import Counter
    orig_dist = Counter(r.get("question_type", "") for r in raw)
    print(f"Original distribution: {dict(orig_dist)}")

    interleaved = interleave(raw)

    # Verify interleaving
    first_30_dist = Counter(r.get("question_type", "") for r in interleaved[:30])
    print(f"First 30 records distribution: {dict(first_30_dist)}")

    # Count unique sessions in first 30 records
    first_30_sessions = set()
    first_30_turns = 0
    for r in interleaved[:30]:
        for sid in r.get("haystack_session_ids", []):
            first_30_sessions.add(sid)
        for sess in r.get("haystack_sessions", []):
            first_30_turns += len(sess)
    print(f"First 30 records: {len(first_30_sessions)} unique sessions, {first_30_turns} total turns")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(interleaved, f, indent=2)

    print(f"Wrote {len(interleaved)} records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
