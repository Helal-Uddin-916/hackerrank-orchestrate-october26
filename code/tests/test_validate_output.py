"""Tests for code/validate_output.py.

Each test starts from a correct sample answer, breaks it in one way, and checks that the
checker reports that exact problem. Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import copy
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate_output as vo  # noqa: E402

SAMPLE_OUTPUT = os.path.join(vo.DATASET, "sample_output.csv")
SAMPLE_REQUESTS = os.path.join(vo.DATASET, "sample_deletion_requests.csv")


def load_samples():
    rows = vo.read_csv(SAMPLE_OUTPUT)
    requests = {r["case_id"]: r for r in vo.read_csv(SAMPLE_REQUESTS)}
    return rows, requests


class StepChecks(unittest.TestCase):
    """Break one row's steps in one way at a time."""

    @classmethod
    def setUpClass(cls):
        rows, requests = load_samples()
        cls.steps = {r["case_id"]: json.loads(r["steps_json"]) for r in rows}
        cls.audit_at = {c: vo.parse_time(r["audit_at"]) for c, r in requests.items()}
        cls.evidence_times = vo.load_evidence_times()

    def problems(self, case_id, status, steps):
        return vo.check_steps(case_id, status, steps, self.audit_at[case_id], self.evidence_times)

    def complete(self):
        return copy.deepcopy(self.steps["case_27"])  # COMPLETE, 6 verification steps

    def incomplete(self):
        return copy.deepcopy(self.steps["case_11"])  # INCOMPLETE, 4 findings / 4 fixes / 6 checks

    def assertCaught(self, problems, text):
        self.assertTrue(any(text in p for p in problems), f"expected a problem containing {text!r}, got {problems}")

    @staticmethod
    def renumber(steps):
        for i, step in enumerate(steps, start=1):
            step["step_no"] = i
        return steps

    def test_correct_rows_pass(self):
        self.assertEqual(self.problems("case_27", "COMPLETE", self.complete()), [])
        self.assertEqual(self.problems("case_11", "INCOMPLETE", self.incomplete()), [])

    def test_swapped_steps_are_out_of_order(self):
        steps = self.complete()
        steps[0], steps[1] = steps[1], steps[0]
        self.assertCaught(self.problems("case_27", "COMPLETE", self.renumber(steps)), "out of order")

    def test_made_up_operation(self):
        steps = self.complete()
        steps[0]["operation"] = "PURGE_EVERYTHING"
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "is not allowed")

    def test_wrong_state_for_operation(self):
        steps = self.complete()
        steps[0]["state"] = "PRESENT"  # VERIFY_RECORD_ABSENT must be ABSENT
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "must have state ABSENT")

    def test_wrong_phase_for_operation(self):
        steps = self.complete()
        steps[0]["phase"] = "REMEDIATION"
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "belongs to phase VERIFICATION")

    def test_complete_check_without_evidence(self):
        steps = self.complete()
        steps[2]["evidence_id"] = ""
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "must cite evidence")

    def test_evidence_captured_after_audit_at(self):
        steps = self.complete()
        steps[0]["evidence_id"] = "evidence_02"  # captured 2026-09-17, case_27 audit_at 2026-09-15
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "created after audit_at")

    def test_evidence_that_does_not_exist(self):
        steps = self.complete()
        steps[0]["evidence_id"] = "evidence_999"
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "does not exist")

    def test_incomplete_remediation_with_evidence(self):
        steps = self.incomplete()
        steps[4]["evidence_id"] = "evidence_03"  # step 5 is DISABLE_DATA_FLOW
        self.assertCaught(self.problems("case_11", "INCOMPLETE", steps), 'must have evidence_id ""')

    def test_incomplete_finding_without_evidence(self):
        steps = self.incomplete()
        steps[0]["evidence_id"] = ""
        self.assertCaught(self.problems("case_11", "INCOMPLETE", steps), "must cite evidence")

    def test_skipped_step_number(self):
        steps = self.complete()
        steps[3]["step_no"] = 5
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "expected 4")

    def test_step_number_as_text(self):
        steps = self.complete()
        steps[0]["step_no"] = "1"
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "expected 1")

    def test_missing_and_extra_fields(self):
        steps = self.complete()
        del steps[0]["evidence_id"]
        steps[0]["note"] = "extra"
        self.assertCaught(self.problems("case_27", "COMPLETE", steps), "wrong fields")

    def test_not_applicable_with_steps(self):
        self.assertCaught(self.problems("case_27", "NOT_APPLICABLE", self.complete()), "must have steps_json = []")

    def test_not_applicable_empty_passes(self):
        self.assertEqual(self.problems("case_27", "NOT_APPLICABLE", []), [])

    def test_complete_with_a_finding(self):
        steps = self.incomplete()
        self.assertCaught(self.problems("case_11", "COMPLETE", steps), "only VERIFICATION steps")

    def test_incomplete_without_findings_is_allowed(self):
        # D-032: a deletion that nothing can prove is INCOMPLETE with only its required checks.
        steps = self.renumber([s for s in self.incomplete() if s["phase"] == "VERIFICATION"])
        self.assertEqual(self.problems("case_11", "INCOMPLETE", steps), [])

    def test_incomplete_with_no_steps_is_allowed(self):
        self.assertEqual(self.problems("case_11", "INCOMPLETE", []), [])

    def test_duplicate_step(self):
        steps = self.complete()
        steps.insert(1, copy.deepcopy(steps[0]))
        self.assertCaught(self.problems("case_27", "COMPLETE", self.renumber(steps)), "duplicate")

    def test_complete_with_no_steps(self):
        self.assertCaught(self.problems("case_27", "COMPLETE", []), "at least one step")

    def test_steps_not_a_list(self):
        self.assertCaught(self.problems("case_27", "COMPLETE", {"step_no": 1}), "not a JSON array")


class FileChecks(unittest.TestCase):
    """Break the whole file in one way at a time."""

    def setUp(self):
        self.rows, _ = load_samples()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, rows, header=vo.HEADER):
        path = os.path.join(self.tmp.name, "output.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for r in rows:
                writer.writerow([r["case_id"], r["deletion_status"], r["steps_json"]])
        return vo.validate(path, SAMPLE_REQUESTS)

    def assertCaught(self, problems, text):
        self.assertTrue(any(text in p for p in problems), f"expected a problem containing {text!r}, got {problems}")

    def test_sample_file_passes(self):
        self.assertEqual(self.write(self.rows), [])

    def test_wrong_header(self):
        self.assertCaught(self.write(self.rows, header=["case", "status", "steps"]), "header is")

    def test_missing_row(self):
        problems = self.write(self.rows[:-1])
        self.assertCaught(problems, "case_ids missing")
        self.assertCaught(problems, "31 data rows, expected 32")

    def test_rows_out_of_order(self):
        rows = list(self.rows)
        rows[0], rows[1] = rows[1], rows[0]
        self.assertCaught(self.write(rows), "not sorted")

    def test_duplicate_case(self):
        self.assertCaught(self.write(self.rows + [self.rows[0]]), "duplicate case_ids")

    def test_unknown_case(self):
        extra = dict(self.rows[0], case_id="case_999")
        self.assertCaught(self.write(self.rows + [extra]), "unknown case_id")

    def test_bad_status(self):
        rows = [dict(self.rows[0], deletion_status="DONE")] + self.rows[1:]
        self.assertCaught(self.write(rows), "is not allowed")

    def test_invalid_json(self):
        rows = [dict(self.rows[0], steps_json="[{broken")] + self.rows[1:]
        self.assertCaught(self.write(rows), "not valid JSON")


class NumberOrder(unittest.TestCase):
    """D-006: case_ids sort by number, so case_9 comes before case_10."""

    def test_case_number(self):
        ids = ["case_10", "case_9", "case_100", "case_33"]
        self.assertEqual(sorted(ids, key=vo.case_number), ["case_9", "case_10", "case_33", "case_100"])


if __name__ == "__main__":
    unittest.main()
