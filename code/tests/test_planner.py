"""Tests for code/audit/planner.py and the whole agent (code/audit/agent.py).

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import importlib.util
import json
import os
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
import validate_output  # noqa: E402
from audit.agent import Agent  # noqa: E402
from audit.llm import LLMClient  # noqa: E402
from audit.planner import OPERATION_ORDER, plan  # noqa: E402
from audit.policy import Decision  # noqa: E402
from audit.settings import DATASET_DIR, LLM_CACHE_DIR, Settings  # noqa: E402

_spec = importlib.util.spec_from_file_location("evaluation_main", os.path.join(CODE_DIR, "evaluation", "main.py"))
grader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grader)
_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def decision(resource, finding=None, remediation=None, verification="VERIFY_RECORD_ABSENT", evidence="evidence_09",
             system="search_service", warnings=()):
    return Decision(system, resource, "SEARCH_INDEX_ENTRY", "DELETE", finding, remediation, verification, evidence,
                    warnings=list(warnings))


class StepWriter(unittest.TestCase):

    def test_operation_order_matches_the_checker(self):
        self.assertEqual(OPERATION_ORDER, [op for op, _, _ in validate_output.OPERATIONS])

    def test_complete_has_only_cited_checks(self):
        p = plan([decision("search_2"), decision("account_1", system="account_service", evidence="evidence_03")])
        self.assertEqual(p.status, "COMPLETE")
        self.assertEqual([(s["step_no"], s["resource_id"], s["evidence_id"]) for s in p.steps],
                         [(1, "account_1", "evidence_03"), (2, "search_2", "evidence_09")])

    def test_incomplete_orders_phases_operations_and_citations(self):
        p = plan([
            decision("search_1", "REPORT_REMAINING_RECORD", "DELETE_RECORD"),
            Decision("integration_service", "data_flow_1", "DATA_FLOW", "DISABLE_FLOW", "REPORT_ACTIVE_DATA_FLOW",
                     "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", "evidence_01"),
            decision("account_1", system="account_service"),
        ])
        self.assertEqual(p.status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in p.steps], [
            "REPORT_REMAINING_RECORD", "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "DELETE_RECORD",
            "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT", "VERIFY_DATA_FLOW_DISABLED"])
        self.assertEqual([s["evidence_id"] for s in p.steps], ["evidence_09", "evidence_01", "", "", "", "", ""])
        self.assertEqual([s["step_no"] for s in p.steps], list(range(1, 8)))

    def test_resource_ids_sort_as_plain_text(self):
        p = plan([decision("search_10"), decision("search_9")])
        self.assertEqual([s["resource_id"] for s in p.steps], ["search_10", "search_9"])

    def test_nothing_usable_is_incomplete_with_no_steps(self):
        p = plan([decision("search_1", verification=None, warnings=["no evidence"])])
        self.assertEqual((p.status, p.steps), ("INCOMPLETE", []))
        self.assertTrue(any("D-032" in w for w in p.warnings))

    def test_only_unproven_checks_give_incomplete_with_those_checks(self):
        d = decision("account_1", system="account_service", evidence=None)
        d.unproven = True
        p = plan([d])
        self.assertEqual(p.status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["evidence_id"]) for s in p.steps], [("VERIFY_RECORD_ABSENT", "")])

    def test_complete_leaves_out_unproven_checks(self):
        d = decision("account_1", system="account_service", evidence=None)
        d.unproven = True
        p = plan([decision("search_1"), d])
        self.assertEqual((p.status, [s["resource_id"] for s in p.steps]), ("COMPLETE", ["search_1"]))


def cache_only_client():
    def no_network(*args):
        raise AssertionError("tried to call the AI provider")
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", ""), LLM_CACHE_DIR, transport=no_network)


def write_answers(agent, requests, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for r in sorted(requests, key=lambda r: int(r["case_id"].split("_")[1])):
            result = agent.run_case(r)
            writer.writerow([r["case_id"], result.status or "UNDECIDED", json.dumps(result.steps)])


class WholeAgent(unittest.TestCase):

    def test_samples_valid_and_never_wrong(self):
        with open(os.path.join(DATASET_DIR, "sample_deletion_requests.csv"), encoding="utf-8", newline="") as f:
            requests = list(csv.DictReader(f))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "answers.csv")
            write_answers(Agent(DATASET_DIR, cache_only_client()), requests, path)
            problems = validate_output.validate(path, os.path.join(DATASET_DIR, "sample_deletion_requests.csv"))
            scores = grader.evaluate(path, os.path.join(DATASET_DIR, "sample_output.csv"))
        self.assertEqual(problems, [])
        self.assertEqual(scores["status_accuracy"], 1.0)
        self.assertEqual(scores["step_precision"], 1.0, "the agent wrote a step the sample answers do not have")
        self.assertGreaterEqual(scores["step_recall"], 0.90)

    def test_hard_cases_exactly_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            agent = Agent(tmp)
            with open(os.path.join(tmp, "graded_requests.csv"), encoding="utf-8", newline="") as f:
                graded = list(csv.DictReader(f))  # includes the NOT_APPLICABLE cases (step 5.5)
            path = os.path.join(tmp, "answers.csv")
            write_answers(agent, graded, path)
            scores = grader.evaluate(path, os.path.join(tmp, "expected_output.csv"))
        wrong = {c: r for c, r in scores["per_case"].items() if not r["exact"] and r["predicted_status"] != "<missing>"}
        self.assertEqual(sorted(wrong), [], "hard cases the agent gets wrong")


if __name__ == "__main__":
    unittest.main()
