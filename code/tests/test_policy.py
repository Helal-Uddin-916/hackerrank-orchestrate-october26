"""Tests for code/audit/policy.py: sample answers, hard cases and edge cases.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.activity_link import ActivityLinker, load_requests  # noqa: E402
from audit.assemble import Assembler, RecordState  # noqa: E402
from audit.evidence import load_evidence  # noqa: E402
from audit.identity import IdentityIndex, Person  # noqa: E402
from audit.llm import LLMClient  # noqa: E402
from audit.policy import Policy, load_rules  # noqa: E402
from audit.settings import DATASET_DIR, LLM_CACHE_DIR, Settings  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

PHASE_INDEX = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def expected_ops(steps):
    want = {}
    for s in steps:
        want.setdefault((s["system_id"], s["resource_id"]), [None, None, None])[PHASE_INDEX[s["phase"]]] = s["operation"]
    return want


class World:
    """Everything needed to decide records for one dataset folder."""

    def __init__(self, dataset_dir, client=None):
        self.assembler = Assembler(load_evidence(dataset_dir, client))
        self.index = IdentityIndex(dataset_dir)
        self.linker = ActivityLinker(self.assembler, self.index, load_requests(dataset_dir), dataset_dir)
        self.policy = Policy(dataset_dir)

    def decisions(self, request):
        person = self.index.resolve(request["email"])
        audit_at = parse_time(request["audit_at"])
        records = self.assembler.assemble(person, audit_at, self.linker.link(request["case_id"])[0])
        return {k: self.policy.decide(r, person, audit_at) for k, r in records.items()}


def cache_only_client():
    def no_network(*args):
        raise AssertionError("tried to call the AI provider")
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", ""), LLM_CACHE_DIR, transport=no_network)


REAL = World(DATASET_DIR, cache_only_client())


class SampleAnswers(unittest.TestCase):

    def test_every_linked_record_gets_the_expected_finding_fix_and_check(self):
        requests = {r["case_id"]: r for r in rows(os.path.join(DATASET_DIR, "sample_deletion_requests.csv"))}
        checked = 0
        for row in rows(os.path.join(DATASET_DIR, "sample_output.csv")):
            decisions = REAL.decisions(requests[row["case_id"]])
            for key, ops in expected_ops(json.loads(row["steps_json"])).items():
                if key in decisions:
                    d = decisions[key]
                    self.assertEqual([d.finding, d.remediation, d.verification], ops, f"{row['case_id']} {key}")
                    checked += 1
        self.assertGreaterEqual(checked, 92)


class HardCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cc.build(cls.tmp.name)
        cls.world = World(cls.tmp.name)
        cls.requests = {r["case_id"]: r for r in rows(os.path.join(cls.tmp.name, "deletion_requests.csv"))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decisions(self, case_id):
        return {k[1]: d for k, d in self.world.decisions(self.requests[case_id]).items()}

    def test_graded_cases_get_exactly_their_expected_operations(self):
        for case in cc.CASES:
            if case["expected_status"] not in ("COMPLETE", "INCOMPLETE"):
                continue
            got = self.decisions(case["id"])
            for key, ops in expected_ops(case["expected_steps"]).items():
                d = got[key[1]]
                self.assertEqual([d.finding, d.remediation, d.verification], ops, f"{case['id']} {key}")

    def test_retention_ended_gives_a_warning_and_no_step(self):
        d = self.decisions("case_915")["audit_record_926"]
        self.assertEqual((d.finding, d.remediation, d.verification), (None, None, None))
        self.assertTrue(any("RETENTION_ENDED" in w for w in d.warnings))

    def test_order_record_goes_to_the_side_report(self):
        d = self.decisions("case_916")["order_927"]
        self.assertTrue(d.side_report)
        self.assertIsNone(d.verification)

    def test_same_moment_conflict_warning_is_carried(self):
        self.assertTrue(any("D-009" in w for w in self.decisions("case_913")["search_925"].warnings))


class EdgeCases(unittest.TestCase):

    def setUp(self):
        self.policy = REAL.policy
        self.person = Person("user_1", frozenset({"a" * 64}), "current email")
        self.audit_at = parse_time("2026-09-15T12:00:00Z")

    def record(self, category, state, **extras):
        return RecordState("support_platform", "support_1", category, state, "evidence_01", extras=extras)

    def test_shared_attachment_is_flagged_not_deleted(self):
        d = self.policy.decide(self.record("SUPPORT_ATTACHMENT", "PRESENT", attachment_owners=("a" * 64, "b" * 64)),
                               self.person, self.audit_at)
        self.assertIsNone(d.remediation)
        self.assertTrue(any("D-014" in w for w in d.warnings))

    def test_own_attachment_is_deleted(self):
        d = self.policy.decide(self.record("SUPPORT_ATTACHMENT", "PRESENT", attachment_owners=("a" * 64,)),
                               self.person, self.audit_at)
        self.assertEqual(d.remediation, "DELETE_RECORD")

    def test_unknown_backup_state_is_treated_as_a_risk_with_a_warning(self):
        d = self.policy.decide(RecordState("backup_service", "backup_1", "BACKUP_ARCHIVE", "UNKNOWN", "e"), self.person, self.audit_at)
        self.assertEqual(d.finding, "REPORT_RESTORE_RISK")
        self.assertTrue(any("UNKNOWN" in w for w in d.warnings))

    def test_record_without_evidence_gets_only_its_unproven_end_state_check(self):
        # D-031 (changed from "no step"): sample case_23 expects VERIFY_RECORD_PRESENT for a kept record
        # that has no evidence before audit_at.
        d = self.policy.decide(RecordState("account_service", "account_1", "USER_PROFILE", None, None), self.person, self.audit_at)
        self.assertEqual((d.finding, d.remediation, d.verification, d.unproven), (None, None, "VERIFY_RECORD_ABSENT", True))

    def test_unknown_rule_action_stops_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(os.path.join(DATASET_DIR, "deletion_rules.csv"), os.path.join(tmp, "deletion_rules.csv"))
            with open(os.path.join(tmp, "deletion_rules.csv"), "a", encoding="utf-8") as f:
                f.write("rule_99,NEW_KIND,Always,ARCHIVE\n")
            with self.assertRaises(ValueError):
                load_rules(tmp)


if __name__ == "__main__":
    unittest.main()
