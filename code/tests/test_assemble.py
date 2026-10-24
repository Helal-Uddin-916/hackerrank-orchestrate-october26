"""Tests for code/audit/assemble.py: sample answers and hard cases.

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
from audit.activity_link import ActivityLinker, load_requests  # noqa: E402
from audit.assemble import Assembler  # noqa: E402
from audit.evidence import load_evidence  # noqa: E402
from audit.identity import IdentityIndex  # noqa: E402
from audit.llm import LLMClient  # noqa: E402
from audit.settings import DATASET_DIR, LLM_CACHE_DIR, Settings  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

FINDING_STATE = {"REPORT_REMAINING_RECORD": {"PRESENT", "EXPOSED"}, "REPORT_EXPOSED_PII": {"EXPOSED"},
                 "REPORT_ACTIVE_DATA_FLOW": {"FLOW_ACTIVE"}, "REPORT_RESTORE_RISK": {"RESTORE_ALLOWED"}}
VERIFY_STATE = {"VERIFY_RECORD_ABSENT": {"ABSENT"}, "VERIFY_RECORD_PRESENT": {"PRESENT"},
                "VERIFY_RECORD_REDACTED": {"REDACTED"}, "VERIFY_RECORD_ANONYMIZED": {"ANONYMIZED"},
                "VERIFY_DATA_FLOW_DISABLED": {"FLOW_DISABLED"}, "VERIFY_RESTORE_BLOCKED": {"RESTORE_BLOCKED"}}


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def cache_only_client():
    def no_network(*args):
        raise AssertionError("tried to call the AI provider")
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", ""), LLM_CACHE_DIR, transport=no_network)


ASSEMBLER = Assembler(load_evidence(DATASET_DIR, cache_only_client()))
INDEX = IdentityIndex(DATASET_DIR)
LINKER = ActivityLinker(ASSEMBLER, INDEX, load_requests(DATASET_DIR), DATASET_DIR)
REQUESTS = {r["case_id"]: r for r in rows(os.path.join(DATASET_DIR, "sample_deletion_requests.csv"))}


def expected_from_answer(status, steps):
    """{(system, resource): (allowed states or None, citation or None)} implied by a sample answer."""
    want = {}
    for s in steps:
        key = (s["system_id"], s["resource_id"])
        if s["phase"] == "FINDING":
            want[key] = (FINDING_STATE[s["operation"]], s["evidence_id"])
        elif s["phase"] == "VERIFICATION" and key not in want:
            # INCOMPLETE verification is the required end state, not an observation.
            want[key] = (VERIFY_STATE[s["operation"]] if status == "COMPLETE" else None, s["evidence_id"] or None)
    return want


class SampleAnswers(unittest.TestCase):

    def test_every_linked_record_has_the_expected_state_and_citation(self):
        linked = 0
        for row in rows(os.path.join(DATASET_DIR, "sample_output.csv")):
            req = REQUESTS[row["case_id"]]
            extra, _ = LINKER.link(row["case_id"])
            records = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]), extra)
            extras = set(records) - set(expected_from_answer(row["deletion_status"], json.loads(row["steps_json"])))
            self.assertEqual(extras, set(), f"{row['case_id']} links records the sample answer does not have")
            for key, (states, cite) in expected_from_answer(row["deletion_status"], json.loads(row["steps_json"])).items():
                got = records.get(key)
                if got is None:
                    continue  # ambiguous (D-028) or found in no evidence at all
                linked += 1
                if states:
                    self.assertIn(got.state, states, f"{row['case_id']} {key}")
                if cite:
                    self.assertEqual(got.evidence_id, cite, f"{row['case_id']} {key}")
        self.assertGreaterEqual(linked, 93, "fewer sample records linked than before")

    def test_case_27_security_record_not_hidden_by_another_persons_snapshot(self):
        req = REQUESTS["case_27"]
        records = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]))
        record = records[("security_service", "audit_record_3830")]
        self.assertEqual((record.state, record.evidence_id), ("PRESENT", "evidence_92"))

    def test_case_16_replica_copy_keeps_the_account_present(self):
        req = REQUESTS["case_16"]
        record = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]))[("account_service", "account_994")]
        self.assertEqual(record.state, "PRESENT")
        self.assertIn("account_service/customer_data_read_replica", record.copies)


class ActivityLinking(unittest.TestCase):

    def test_unique_fit_is_linked(self):
        extra, warnings = LINKER.link("case_01")
        self.assertIn(("account_service", "account_001"), extra)

    def test_symmetric_pair_is_not_guessed(self):
        for case_id in ("case_18", "case_20"):
            extra, warnings = LINKER.link(case_id)
            self.assertNotIn(("account_service", "account_5521"), extra)
            self.assertNotIn(("account_service", "account_5037"), extra)
            self.assertTrue(any("D-028" in w for w in warnings), case_id)

    def test_case_01_absence_proven_by_complete_sql_copy(self):
        req = REQUESTS["case_01"]
        extra, _ = LINKER.link("case_01")
        record = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]), extra)[("account_service", "account_001")]
        self.assertEqual((record.state, record.evidence_id), ("ABSENT", "evidence_54"))

    def test_file_about_exactly_one_candidate_decides(self):
        # D-035: account_3136 fits case_25, case_549 and case_550 by timing; evidence_92 shows it and is about case_25.
        extra, _ = LINKER.link("case_25")
        self.assertIn(("account_service", "account_3136"), extra)
        for other in ("case_549", "case_550"):
            self.assertNotIn(("account_service", "account_3136"), LINKER.link(other)[0])
        req = REQUESTS["case_25"]
        record = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]), extra)[("account_service", "account_3136")]
        self.assertEqual((record.state, record.evidence_id), ("ABSENT", "evidence_92"))

    def test_older_lookup_only_account_is_dropped(self):
        req = REQUESTS["case_17"]
        records = ASSEMBLER.assemble(INDEX.resolve(req["email"]), parse_time(req["audit_at"]))
        accounts = [k[1] for k in records if k[0] == "account_service"]
        self.assertEqual(accounts, ["account_4092"])
        self.assertTrue(any("account_6523" in w for w in records[("account_service", "account_4092")].warnings))


class HardCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cc.build(cls.tmp.name)
        cls.assembler = Assembler(load_evidence(cls.tmp.name))
        cls.index = IdentityIndex(cls.tmp.name)
        cls.linker = ActivityLinker(cls.assembler, cls.index, load_requests(cls.tmp.name), cls.tmp.name)
        cls.reqs = {r["case_id"]: r for r in rows(os.path.join(cls.tmp.name, "deletion_requests.csv"))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def records(self, case_id):
        req = self.reqs[case_id]
        extra, _ = self.linker.link(case_id)
        person = self.index.resolve(req["email"])
        return {k[1]: v for k, v in self.assembler.assemble(person, parse_time(req["audit_at"]), extra).items()}

    def test_expected_records_states_and_citations(self):
        for case in cc.CASES:
            if case["expected_status"] not in ("COMPLETE", "INCOMPLETE"):
                continue
            got = self.records(case["id"])
            for key, (states, cite) in expected_from_answer(case["expected_status"], case["expected_steps"]).items():
                self.assertIn(key[1], got, f"{case['id']} {key} not linked")
                if states:
                    self.assertIn(got[key[1]].state, states, f"{case['id']} {key}")
                if cite:
                    self.assertEqual(got[key[1]].evidence_id, cite, f"{case['id']} {key}")

    def test_late_file_does_not_count(self):
        self.assertEqual(self.records("case_905")["account_905"].state, "ABSENT")

    def test_out_of_scope_inventory_does_not_hide_the_search_entry(self):
        self.assertEqual(self.records("case_909")["search_909"].state, "PRESENT")

    def test_replica_only_row(self):
        self.assertEqual(self.records("case_908")["account_908"].state, "PRESENT")

    def test_same_moment_conflict_takes_worse_and_warns(self):
        record = self.records("case_913")["search_925"]
        self.assertEqual((record.state, record.evidence_id), ("PRESENT", "evidence_927"))
        self.assertTrue(any("D-009" in w for w in record.warnings))

    def test_two_requests_judged_on_their_own_dates(self):
        self.assertEqual(self.records("case_904")["search_904"].state, "PRESENT")
        self.assertEqual(self.records("case_910")["search_904"].state, "ABSENT")

    def test_account_without_identifier_linked_through_cleanup_timing(self):
        self.assertEqual(self.records("case_914")["account_914"].state, "ABSENT")

    def test_decoys_are_not_linked(self):
        for case_id in ("case_901", "case_903", "case_911"):
            self.assertFalse({"search_902", "search_9022", "data_flow_9022", "backup_919"} & set(self.records(case_id)))


if __name__ == "__main__":
    unittest.main()
