"""Check an output CSV against the submission format rules.

Usage (from the repo root):
    python3 code/validate_output.py                      # checks output.csv for all 518 requests
    python3 code/validate_output.py dataset/sample_output.csv --requests dataset/sample_deletion_requests.csv
    python3 code/validate_output.py ANSWERS.csv --dataset path/to/other_dataset   # e.g. a custom test case

Prints every problem found and exits with status 1 if there are any, 0 if the file is valid.
Only format and contract rules are checked here, not whether the answers are correct.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(REPO_ROOT, "dataset")

HEADER = ["case_id", "deletion_status", "steps_json"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]

# Allowed operations in their required order, with the state and phase each one must have
# (AGENTS.md §6.4, problem_statement.md "Allowed Operations").
OPERATIONS = [
    ("REPORT_REMAINING_RECORD", "PRESENT", "FINDING"),
    ("REPORT_EXPOSED_PII", "PRESENT", "FINDING"),
    ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "FINDING"),
    ("REPORT_RESTORE_RISK", "ACTIVE", "FINDING"),
    ("DISABLE_DATA_FLOW", "DISABLED", "REMEDIATION"),
    ("BLOCK_RESTORE", "BLOCKED", "REMEDIATION"),
    ("DELETE_RECORD", "ABSENT", "REMEDIATION"),
    ("REDACT_RECORD", "REDACTED", "REMEDIATION"),
    ("ANONYMIZE_RECORD", "ANONYMIZED", "REMEDIATION"),
    ("VERIFY_RECORD_ABSENT", "ABSENT", "VERIFICATION"),
    ("VERIFY_RECORD_PRESENT", "PRESENT", "VERIFICATION"),
    ("VERIFY_RECORD_REDACTED", "REDACTED", "VERIFICATION"),
    ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "VERIFICATION"),
    ("VERIFY_DATA_FLOW_DISABLED", "DISABLED", "VERIFICATION"),
    ("VERIFY_RESTORE_BLOCKED", "BLOCKED", "VERIFICATION"),
]
OP_ORDER = {op: i for i, (op, _, _) in enumerate(OPERATIONS)}
OP_STATE = {op: state for op, state, _ in OPERATIONS}
OP_PHASE = {op: phase for op, _, phase in OPERATIONS}


def parse_time(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def case_number(case_id):
    return int(case_id.split("_", 1)[1])


def load_evidence_times(dataset_dir=DATASET):
    """Map every citable id (file_id, activity_id, audit_id) to the time it came into existence."""
    times = {}
    for row in read_csv(os.path.join(dataset_dir, "evidence_files.csv")):
        times[row["file_id"]] = parse_time(row["captured_at"])
    for row in read_csv(os.path.join(dataset_dir, "system_activity.csv")):
        times[row["activity_id"]] = parse_time(row["event_time"])
    for row in read_csv(os.path.join(dataset_dir, "user_audit_log.csv")):
        times[row["audit_id"]] = parse_time(row["event_time"])
    return times


def check_steps(case_id, status, steps, audit_at, evidence_times):
    """Return a list of problems for one row's steps."""
    problems = []
    if not isinstance(steps, list):
        return [f"{case_id}: steps_json is not a JSON array"]

    if status == "NOT_APPLICABLE":
        if steps:
            problems.append(f"{case_id}: NOT_APPLICABLE must have steps_json = []")
        return problems
    if not steps:
        if status == "COMPLETE":  # INCOMPLETE [] is allowed: nothing could be proven (D-032)
            problems.append(f"{case_id}: COMPLETE must have at least one step")
        return problems

    previous_key = None
    for index, step in enumerate(steps, start=1):
        where = f"{case_id} step {index}"
        if not isinstance(step, dict):
            problems.append(f"{where}: step is not a JSON object")
            continue
        if set(step) != STEP_FIELDS:
            missing = sorted(STEP_FIELDS - set(step))
            extra = sorted(set(step) - STEP_FIELDS)
            problems.append(f"{where}: wrong fields (missing {missing}, extra {extra})")
            continue

        if not isinstance(step["step_no"], int) or isinstance(step["step_no"], bool) or step["step_no"] != index:
            problems.append(f"{where}: step_no is {step['step_no']!r}, expected {index}")
        for field in ("system_id", "resource_id", "evidence_id"):
            if not isinstance(step[field], str):
                problems.append(f"{where}: {field} must be a string")
        for field in ("system_id", "resource_id"):
            if isinstance(step[field], str) and not step[field].strip():
                problems.append(f"{where}: {field} is empty")

        operation, phase, state = step["operation"], step["phase"], step["state"]
        if operation not in OP_ORDER:
            problems.append(f"{where}: operation {operation!r} is not allowed")
            continue
        if phase != OP_PHASE[operation]:
            problems.append(f"{where}: {operation} belongs to phase {OP_PHASE[operation]}, not {phase!r}")
        if state != OP_STATE[operation]:
            problems.append(f"{where}: {operation} must have state {OP_STATE[operation]}, not {state!r}")

        # Order: phase, then operation table order, then system_id, then resource_id.
        key = (OP_ORDER[operation], step["system_id"], step["resource_id"])
        if previous_key is not None and key < previous_key:
            problems.append(f"{where}: out of order (phase/operation/system_id/resource_id)")
        if previous_key is not None and key == previous_key:
            problems.append(f"{where}: duplicate of the previous step")
        previous_key = key

        evidence_id = step["evidence_id"]
        if not isinstance(evidence_id, str):
            continue
        must_cite = phase == "FINDING" or status == "COMPLETE"
        if must_cite:
            if not evidence_id:
                problems.append(f"{where}: {phase} step in a {status} case must cite evidence")
            elif evidence_id not in evidence_times:
                problems.append(f"{where}: evidence_id {evidence_id!r} does not exist")
            elif evidence_times[evidence_id] > audit_at:
                problems.append(f"{where}: evidence_id {evidence_id} was created after audit_at")
        elif evidence_id:
            problems.append(f"{where}: {phase} step in an INCOMPLETE case must have evidence_id \"\"")

    phases = {step.get("phase") for step in steps if isinstance(step, dict)}
    if status == "COMPLETE" and phases - {"VERIFICATION"}:
        problems.append(f"{case_id}: COMPLETE may contain only VERIFICATION steps")
    return problems


def validate(output_path, requests_path, dataset_dir=DATASET):
    """Return a list of problems in output_path, checked against the requests in requests_path."""
    requests = {row["case_id"]: row for row in read_csv(requests_path)}
    evidence_times = load_evidence_times(dataset_dir)

    with open(output_path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)
    if header != HEADER:
        return [f"header is {header}, expected {HEADER}"]

    problems = []
    seen = []
    for line_no, row in enumerate(rows, start=2):
        if len(row) != 3:
            problems.append(f"line {line_no}: expected 3 columns, found {len(row)}")
            continue
        case_id, status, steps_json = row
        seen.append(case_id)
        if case_id not in requests:
            problems.append(f"line {line_no}: unknown case_id {case_id!r}")
            continue
        if status not in STATUSES:
            problems.append(f"{case_id}: deletion_status {status!r} is not allowed")
            continue
        try:
            steps = json.loads(steps_json)
        except ValueError:
            problems.append(f"{case_id}: steps_json is not valid JSON")
            continue
        audit_at = parse_time(requests[case_id]["audit_at"])
        problems.extend(check_steps(case_id, status, steps, audit_at, evidence_times))

    duplicates = sorted({c for c in seen if seen.count(c) > 1})
    if duplicates:
        problems.append(f"duplicate case_ids: {duplicates}")
    missing = sorted(set(requests) - set(seen), key=case_number)
    if missing:
        problems.append(f"{len(missing)} case_ids missing, first few: {missing[:5]}")
    if len(rows) != len(requests):
        problems.append(f"{len(rows)} data rows, expected {len(requests)}")
    known = [c for c in seen if c in requests]
    if known != sorted(known, key=case_number):
        problems.append("rows are not sorted by case_id number (D-006)")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", nargs="?", default=os.path.join(REPO_ROOT, "output.csv"))
    parser.add_argument("--dataset", default=DATASET, help="dataset folder (default: dataset/)")
    parser.add_argument("--requests", help="requests CSV (default: <dataset>/deletion_requests.csv)")
    args = parser.parse_args()

    requests = args.requests or os.path.join(args.dataset, "deletion_requests.csv")
    problems = validate(args.output, requests, args.dataset)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s) found in {os.path.relpath(args.output, REPO_ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
