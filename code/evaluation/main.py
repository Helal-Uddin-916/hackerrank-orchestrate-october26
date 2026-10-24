"""Score predicted answers against known-correct answers (the samples, or our own test cases).

Usage (from the repo root):
    python3 code/evaluation/main.py                        # output.csv vs dataset/sample_output.csv
    python3 code/evaluation/main.py PREDICTED.csv EXPECTED.csv
    python3 code/evaluation/main.py PREDICTED.csv EXPECTED.csv --details   # show what differs per case

Only cases present in EXPECTED are scored. Scores (all between 0 and 1):
- status accuracy:   cases whose deletion_status is right.
- step precision:    predicted steps that are in the expected answer (order ignored).
- step recall:       expected steps that we predicted (order ignored).
- step F1:           balance of precision and recall.
- field accuracy:    per field, how often step i has the right value, comparing position by
                     position (a missing or extra step counts as wrong for every field).
- sequence accuracy: cases whose whole step list is exactly right, in the right order.
- exact case match:  cases with the right status and exactly the right steps.
Precision/recall compare steps without step_no, so one missing step counts once instead of
shifting every later step. step_no still counts in field accuracy and sequence accuracy.
"""
import argparse
import collections
import csv
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
CONTENT_FIELDS = FIELDS[1:]  # everything except step_no


def read_answers(path):
    """Return {case_id: (status, steps)}; unparseable steps_json becomes None."""
    answers = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                steps = json.loads(row["steps_json"])
            except ValueError:
                steps = None
            answers[row["case_id"]] = (row["deletion_status"], steps)
    return answers


def step_key(step, fields=CONTENT_FIELDS):
    """A hashable view of one step; by default without step_no, for order-free comparison."""
    return tuple(step.get(field) for field in fields) if isinstance(step, dict) else ("<not a step>",)


def score_case(expected_steps, predicted_steps):
    """Return the per-case counts that the totals are built from."""
    predicted_steps = predicted_steps if isinstance(predicted_steps, list) else []
    expected_keys = collections.Counter(step_key(s) for s in expected_steps)
    predicted_keys = collections.Counter(step_key(s) for s in predicted_steps)
    matched = sum((expected_keys & predicted_keys).values())

    positions = max(len(expected_steps), len(predicted_steps))
    field_hits = collections.Counter()
    for i in range(min(len(expected_steps), len(predicted_steps))):
        e, p = expected_steps[i], predicted_steps[i]
        if isinstance(p, dict):
            for field in FIELDS:
                field_hits[field] += e.get(field) == p.get(field)

    return {
        "matched": matched,
        "expected": len(expected_steps),
        "predicted": len(predicted_steps),
        "positions": positions,
        "field_hits": field_hits,
        "same_sequence": [step_key(s, FIELDS) for s in expected_steps] == [step_key(s, FIELDS) for s in predicted_steps],
        "missing": sorted((expected_keys - predicted_keys).elements(), key=str),
        "extra": sorted((predicted_keys - expected_keys).elements(), key=str),
    }


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 1.0


def evaluate(predicted_path, expected_path):
    """Return a dict with overall scores and per-case results."""
    expected = read_answers(expected_path)
    predicted = read_answers(predicted_path)

    totals = collections.Counter()
    field_hits = collections.Counter()
    status_confusion = collections.Counter()
    cases = {}
    for case_id, (expected_status, expected_steps) in expected.items():
        predicted_status, predicted_steps = predicted.get(case_id, ("<missing>", []))
        result = score_case(expected_steps, predicted_steps)
        result["expected_status"] = expected_status
        result["predicted_status"] = predicted_status
        result["status_ok"] = expected_status == predicted_status
        result["exact"] = result["status_ok"] and result["same_sequence"]
        cases[case_id] = result

        status_confusion[(expected_status, predicted_status)] += 1
        totals["cases"] += 1
        totals["status_ok"] += result["status_ok"]
        totals["same_sequence"] += result["same_sequence"]
        totals["exact"] += result["exact"]
        for name in ("matched", "expected", "predicted", "positions"):
            totals[name] += result[name]
        field_hits.update(result["field_hits"])

    precision = ratio(totals["matched"], totals["predicted"])
    recall = ratio(totals["matched"], totals["expected"])
    return {
        "cases": totals["cases"],
        "status_accuracy": ratio(totals["status_ok"], totals["cases"]),
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": ratio(2 * precision * recall, precision + recall) if precision + recall else 0.0,
        "field_accuracy": {f: ratio(field_hits[f], totals["positions"]) for f in FIELDS},
        "sequence_accuracy": ratio(totals["same_sequence"], totals["cases"]),
        "exact_case_match": ratio(totals["exact"], totals["cases"]),
        "status_confusion": dict(status_confusion),
        "per_case": cases,
    }


def print_report(scores, details):
    print(f"cases scored:       {scores['cases']}")
    print(f"status accuracy:    {scores['status_accuracy']:.1%}")
    print(f"step precision:     {scores['step_precision']:.1%}")
    print(f"step recall:        {scores['step_recall']:.1%}")
    print(f"step F1:            {scores['step_f1']:.1%}")
    print(f"sequence accuracy:  {scores['sequence_accuracy']:.1%}")
    print(f"exact case match:   {scores['exact_case_match']:.1%}")
    print("field accuracy:")
    for field, value in scores["field_accuracy"].items():
        print(f"  {field:12} {value:.1%}")
    print("status (expected -> predicted):")
    for (exp, pred), n in sorted(scores["status_confusion"].items()):
        print(f"  {exp:15} -> {pred:15} {n}")

    wrong = {c: r for c, r in scores["per_case"].items() if not r["exact"]}
    print(f"cases not exactly right: {len(wrong)}")
    for case_id, r in wrong.items():
        print(f"  {case_id}: status {r['expected_status']} -> {r['predicted_status']}, "
              f"missing {len(r['missing'])}, extra {len(r['extra'])}, order ok: {r['same_sequence']}")
        if details:
            for step in r["missing"]:
                print(f"      missing: {dict(zip(CONTENT_FIELDS, step))}")
            for step in r["extra"]:
                print(f"      extra:   {dict(zip(CONTENT_FIELDS, step))}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("predicted", nargs="?", default=os.path.join(REPO_ROOT, "output.csv"))
    parser.add_argument("expected", nargs="?", default=os.path.join(REPO_ROOT, "dataset", "sample_output.csv"))
    parser.add_argument("--details", action="store_true", help="list missing and extra steps per case")
    args = parser.parse_args()
    print_report(evaluate(args.predicted, args.expected), args.details)
    return 0


if __name__ == "__main__":
    sys.exit(main())
