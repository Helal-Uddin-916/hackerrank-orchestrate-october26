"""Tests for code/evaluation/main.py (the grader).

Each test changes a copy of the correct sample answers in one way and checks that only the
matching score moves. Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import copy
import csv
import importlib.util
import json
import os
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("evaluation_main", os.path.join(CODE_DIR, "evaluation", "main.py"))
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

SAMPLE_OUTPUT = os.path.join(ev.REPO_ROOT, "dataset", "sample_output.csv")


def renumber(steps):
    for i, step in enumerate(steps, start=1):
        step["step_no"] = i
    return steps


class Grader(unittest.TestCase):

    def setUp(self):
        with open(SAMPLE_OUTPUT, encoding="utf-8", newline="") as f:
            self.rows = list(csv.DictReader(f))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def grade(self, change=None, drop_case=None):
        """Write a changed copy of the samples, grade it against the originals."""
        path = os.path.join(self.tmp.name, "predicted.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            for row in self.rows:
                if row["case_id"] == drop_case:
                    continue
                status, steps = row["deletion_status"], json.loads(row["steps_json"])
                if change:
                    status, steps = change(row["case_id"], status, copy.deepcopy(steps))
                writer.writerow([row["case_id"], status, json.dumps(steps)])
        return ev.evaluate(path, SAMPLE_OUTPUT)

    def test_correct_answers_score_100(self):
        scores = self.grade()
        for name in ("status_accuracy", "step_precision", "step_recall", "step_f1",
                     "sequence_accuracy", "exact_case_match"):
            self.assertEqual(scores[name], 1.0, name)
        self.assertTrue(all(v == 1.0 for v in scores["field_accuracy"].values()))

    def test_wrong_status_only_lowers_status(self):
        def change(case_id, status, steps):
            return ("INCOMPLETE" if case_id == "case_01" else status), steps
        scores = self.grade(change)
        self.assertLess(scores["status_accuracy"], 1.0)
        self.assertEqual(scores["step_f1"], 1.0)
        self.assertEqual(scores["sequence_accuracy"], 1.0)
        self.assertFalse(scores["per_case"]["case_01"]["exact"])

    def test_one_removed_step_counts_once(self):
        # Regression: step_no used to be part of the comparison, so removing one step
        # renumbered the rest and reported "missing 9, extra 8".
        def change(case_id, status, steps):
            if case_id == "case_11":
                steps = renumber(steps[:5] + steps[6:])
            return status, steps
        case = self.grade(change)["per_case"]["case_11"]
        self.assertEqual(len(case["missing"]), 1)
        self.assertEqual(len(case["extra"]), 0)
        self.assertFalse(case["same_sequence"])

    def test_swapped_steps_only_lower_order(self):
        def change(case_id, status, steps):
            if case_id == "case_27":
                steps[0], steps[1] = steps[1], steps[0]
                renumber(steps)
            return status, steps
        scores = self.grade(change)
        case = scores["per_case"]["case_27"]
        self.assertEqual((len(case["missing"]), len(case["extra"])), (0, 0))
        self.assertFalse(case["same_sequence"])
        self.assertEqual(scores["step_f1"], 1.0)
        self.assertLess(scores["sequence_accuracy"], 1.0)

    def test_extra_step_lowers_precision_not_recall(self):
        def change(case_id, status, steps):
            if case_id == "case_27":
                extra = dict(steps[-1], resource_id="backup_0000")
                steps = renumber(steps + [extra])
            return status, steps
        scores = self.grade(change)
        self.assertLess(scores["step_precision"], 1.0)
        self.assertEqual(scores["step_recall"], 1.0)
        self.assertEqual(len(scores["per_case"]["case_27"]["extra"]), 1)

    def test_wrong_evidence_lowers_that_field(self):
        def change(case_id, status, steps):
            if case_id == "case_27":
                steps[0]["evidence_id"] = "evidence_01"
            return status, steps
        fields = self.grade(change)["field_accuracy"]
        self.assertLess(fields["evidence_id"], 1.0)
        self.assertEqual(fields["resource_id"], 1.0)

    def test_missing_case_counts_as_wrong(self):
        scores = self.grade(drop_case="case_05")
        case = scores["per_case"]["case_05"]
        self.assertEqual(case["predicted_status"], "<missing>")
        self.assertFalse(case["exact"])
        self.assertLess(scores["step_recall"], 1.0)

    def test_broken_json_counts_as_no_steps(self):
        path = os.path.join(self.tmp.name, "predicted.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            for row in self.rows:
                steps_json = "[{broken" if row["case_id"] == "case_11" else row["steps_json"]
                writer.writerow([row["case_id"], row["deletion_status"], steps_json])
        case = ev.evaluate(path, SAMPLE_OUTPUT)["per_case"]["case_11"]
        self.assertEqual(case["predicted"], 0)
        self.assertEqual(len(case["missing"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
