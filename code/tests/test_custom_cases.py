"""Tests for code/evaluation/custom_cases.py.

These check that each made-up case is well formed and still tests what it claims to test,
so an edit cannot quietly turn a hard case into an easy one. Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
import validate_output  # noqa: E402

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

CASES = {c["id"]: c for c in cc.CASES}


ALLOWED_SQL = re.compile(r"^\s*(CREATE TABLE|CREATE INDEX|INSERT INTO|BEGIN|COMMIT|PRAGMA foreign_keys\s*=\s*ON)\b", re.I)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_sql(text):
    """Load SQL text into an in-memory database, refusing any statement outside the allowlist."""
    db = sqlite3.connect(":memory:")
    buf = []
    for line in text.splitlines(keepends=True):
        if not buf and line.strip().startswith("--"):
            continue
        buf.append(line)
        statement = "".join(buf)
        if sqlite3.complete_statement(statement):
            if not ALLOWED_SQL.match(statement):
                raise ValueError(f"statement not allowed: {statement[:40]!r}")
            db.execute(statement)
            buf = []
    return db


class BuiltDataset(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = cls.tmp.name
        cls.expected_path = cc.build(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_expected_answers_pass_the_format_checker(self):
        problems = validate_output.validate(
            self.expected_path, os.path.join(self.out, "graded_requests.csv"), self.out)
        self.assertEqual(problems, [])

    def test_same_layout_as_real_dataset(self):
        for name in ("deletion_requests.csv", "user_identities.csv", "user_audit_log.csv",
                     "system_activity.csv", "deletion_rules.csv", "evidence_files.csv"):
            with open(os.path.join(self.out, name), encoding="utf-8") as built, \
                    open(os.path.join(cc.REAL_DATASET, name), encoding="utf-8") as real:
                self.assertEqual(built.readline(), real.readline(), f"{name} header differs from dataset/")

    def test_every_manifest_file_exists(self):
        for row in read_csv(os.path.join(self.out, "evidence_files.csv")):
            self.assertTrue(os.path.exists(os.path.join(self.out, row["file_path"])), row["file_id"])


class CasesTestWhatTheyClaim(unittest.TestCase):

    def test_old_email_case_needs_identity_history(self):
        case = CASES["case_901"]
        old = case["request"]["email"]
        self.assertNotIn(old, {u["email"] for u in cc.IDENTITIES}, "request email must not be current")
        self.assertIn(old, {a["old_value"] for a in case["audit_log"] if a["field_name"] == "email"})
        for ev in case["evidence"]:
            self.assertNotIn(old, ev["content"], "evidence must only show the new email")

    def test_late_file_has_a_note_exactly_at_audit_time(self):
        case = CASES["case_905"]
        audit_at = case["request"]["audit_at"]
        late = [ev for ev in case["evidence"] if ev["captured_at"] > audit_at]
        self.assertTrue(late, "case needs a file saved after audit_at")
        self.assertTrue(any(f"{audit_at} | " in ev["content"] and "FOUND" in ev["content"] for ev in late))
        cited = {s["evidence_id"] for s in case["expected_steps"]}
        self.assertFalse(cited & {ev["file_id"] for ev in late}, "a late file must never be cited")

    def test_spanish_case_is_not_deletion_but_has_found_records(self):
        case = CASES["case_917"]
        text = case["request"]["request_text"].lower()
        for word in ("elimin", "borr", "supr", "delet", "eras"):
            self.assertNotIn(word, text)
        self.assertIn("FOUND", case["evidence"][0]["content"], "records should tempt a wrong deletion")
        self.assertEqual((case["expected_status"], case["expected_steps"]), ("NOT_APPLICABLE", []))

    def test_backup_case_has_restorable_backup_and_blocks_it(self):
        case = CASES["case_911"]
        self.assertIn("| backup_911 | BACKUP_ARCHIVE | ACTIVE |", case["evidence"][0]["content"])
        ops = [(s["operation"], s["resource_id"]) for s in case["expected_steps"]]
        self.assertIn(("REPORT_RESTORE_RISK", "backup_911"), ops)
        self.assertIn(("BLOCK_RESTORE", "backup_911"), ops)
        self.assertNotIn(("DELETE_RECORD", "backup_911"), ops, "backups are kept, not deleted (rule_14)")

    def test_sync_case_turns_flow_off_before_deleting(self):
        ops = [s["operation"] for s in CASES["case_912"]["expected_steps"]]
        self.assertLess(ops.index("DISABLE_DATA_FLOW"), ops.index("DELETE_RECORD"))

    def test_tombstone_case_leaks_only_the_hash(self):
        case = CASES["case_914"]
        snapshots = json.loads(case["evidence"][0]["content"])["snapshots"]
        stores = {s["data_store"]: s for s in snapshots}
        tomb = next(r for r in stores["identity_service"]["records"] if r["resource_id"] == "tombstone_914")
        self.assertEqual((tomb["name"], tomb["email"], tomb["phone"]), (None, None, None))
        self.assertEqual(tomb["email_hash"], cc.email_hash(case["request"]["email"]))
        self.assertNotIn(case["request"]["email"], case["evidence"][0]["content"], "no readable email anywhere")
        self.assertEqual(stores["account_service"]["records"], [], "account absence comes from an empty snapshot")
        self.assertNotIn("account_914", case["evidence"][0]["content"], "account id only in activity")
        self.assertIn("account_914", {a["resource_id"] for a in case["activity"]})

    def test_replica_case_row_only_in_replica(self):
        db = load_sql(CASES["case_908"]["evidence"][0]["content"])
        primary = {r[0] for r in db.execute("SELECT resource_id FROM customer_data_primary")}
        replica = {r[0] for r in db.execute("SELECT resource_id FROM customer_data_read_replica")}
        self.assertNotIn("account_908", primary)
        self.assertIn("account_908", replica)

    def test_coverage_case_inventory_skips_search_and_security(self):
        case = CASES["case_909"]
        early, inventory = case["evidence"]
        self.assertLess(early["captured_at"], inventory["captured_at"], "inventory must be the newest file")
        db = load_sql(inventory["content"])
        scope = {r[0] for r in db.execute("SELECT origin_system FROM snapshot_scope")}
        self.assertEqual(scope, {"account_service"})
        self.assertEqual(db.execute("SELECT COUNT(*) FROM customer_search_documents").fetchone()[0], 0)
        self.assertIn("| search_909 | SEARCH_INDEX_ENTRY | FOUND |", early["content"])
        cited = {s["evidence_id"] for s in case["expected_steps"]}
        self.assertNotIn(inventory["file_id"], cited, "an out-of-scope inventory proves nothing here")

    def test_two_requests_same_person_judged_on_own_dates(self):
        first, second = CASES["case_904"], CASES["case_910"]
        self.assertEqual(first["request"]["email"], second["request"]["email"])
        self.assertLess(first["request"]["audit_at"], second["request"]["audit_at"])
        self.assertEqual((first["expected_status"], second["expected_status"]), ("INCOMPLETE", "COMPLETE"))
        late_file = next(ev for ev in first["evidence"] if ev["file_id"] == "evidence_915")
        self.assertGreater(late_file["captured_at"], first["request"]["audit_at"])
        self.assertLessEqual(late_file["captured_at"], second["request"]["audit_at"])
        self.assertEqual({s["evidence_id"] for s in second["expected_steps"]}, {"evidence_915"})

    def test_sql_files_use_only_allowed_statements(self):
        for case in cc.CASES:
            for ev in case["evidence"]:
                if ev["file_path"].endswith(".sql"):
                    load_sql(ev["content"])  # raises if a statement is not allowed

    def test_messy_email_matches_only_after_cleanup(self):
        raw = CASES["case_902"]["request"]["email"]
        self.assertNotEqual(raw, raw.strip().lower())
        self.assertIn(raw.strip().lower(), {u["email"] for u in cc.IDENTITIES})
        self.assertNotIn(raw, {u["email"] for u in cc.IDENTITIES})

    def test_neighbour_leftovers_do_not_spoil_complete(self):
        case = CASES["case_903"]
        content = case["evidence"][0]["content"]
        self.assertIn("| search_9022 | SEARCH_INDEX_ENTRY | FOUND | decoy@example.com", content)
        self.assertIn("| data_flow_9022 | DATA_FLOW | ACTIVE | decoy@example.com", content)
        self.assertEqual(case["expected_status"], "COMPLETE")

    def test_exact_audit_time_file_decides_search(self):
        case = CASES["case_906"]
        exact = next(ev for ev in case["evidence"] if ev["file_id"] == "evidence_924")
        self.assertEqual(exact["captured_at"], case["request"]["audit_at"])
        cites = {s["resource_id"]: s["evidence_id"] for s in case["expected_steps"]}
        self.assertEqual(cites["search_923"], "evidence_924")
        self.assertEqual(cites["account_923"], "evidence_923", "each record cites its own latest sighting")

    def test_activity_claims_contradict_evidence(self):
        case = CASES["case_907"]
        claims = {a["resource_id"]: a["reported_result"] for a in case["activity"]}
        self.assertEqual(claims, {"account_924": "FAILED", "search_924": "SUCCESS"})
        content = case["evidence"][0]["content"]
        self.assertIn("| account_924 | USER_PROFILE | NOT_FOUND |", content)
        self.assertIn("| search_924 | SEARCH_INDEX_ENTRY | FOUND |", content)

    def test_conflict_same_moment_takes_worse_and_warns(self):
        case = CASES["case_913"]
        rows = [line for ev in case["evidence"] for line in ev["content"].splitlines() if "| search_925 |" in line]
        self.assertEqual(len({line.split(" | ")[0] for line in rows}), 1, "both sightings at the same time")
        self.assertEqual({line.split(" | ")[4] for line in rows}, {"FOUND", "NOT_FOUND"})
        self.assertEqual(case["expected_steps"][0]["evidence_id"], "evidence_927")
        self.assertIn("CONFLICTING_SIGHTINGS", case["expected_warnings"])

    def test_retention_ended_is_ungraded_and_warns(self):
        case = CASES["case_915"]
        export = json.loads(case["evidence"][1]["content"])
        record = next(r for r in export["service_exports"]["security_service"]["audit_records"]
                      if r["subject_reference"] == "user_926")
        self.assertLess(record["retention_until"], case["request"]["audit_at"])
        self.assertIsNone(case["expected_status"])
        self.assertEqual(case["expected_warnings"], ["RETENTION_ENDED"])

    def test_order_record_gets_no_step_but_is_reported(self):
        case = CASES["case_916"]
        self.assertIn("| order_927 | ORDER_RECORD | FOUND |", case["evidence"][0]["content"])
        self.assertNotIn("order_927", {s["resource_id"] for s in case["expected_steps"]})
        self.assertIn("NO_RULE_FOR_RECORD", case["expected_warnings"])

    def test_no_longer_is_still_deletion(self):
        case = CASES["case_918"]
        text = case["request"]["request_text"].lower()
        self.assertIn("no longer", text)
        self.assertIn("deleted", text)
        self.assertNotEqual(case["expected_status"], "NOT_APPLICABLE")

    def test_do_not_close_is_not_deletion(self):
        case = CASES["case_919"]
        self.assertIn("do not close", case["request"]["request_text"].lower())
        self.assertIn("| FOUND |", case["evidence"][0]["content"], "records should tempt a wrong deletion")
        self.assertEqual((case["expected_status"], case["expected_steps"]), ("NOT_APPLICABLE", []))

    def test_evidence_injection_is_present_and_ignored(self):
        case = CASES["case_920"]
        self.assertIn(cc.EVIDENCE_INJECTION, case["evidence"][0]["content"])
        self.assertIn("| search_930 | SEARCH_INDEX_ENTRY | FOUND |", case["evidence"][0]["content"])
        self.assertEqual(case["expected_status"], "INCOMPLETE", "the planted note must not win")

    def test_request_injection_is_present_and_ignored(self):
        case = CASES["case_930"]
        self.assertEqual(case["request"]["request_text"], cc.REQUEST_INJECTION)
        self.assertIn("delet", case["request"]["request_text"].lower(), "it does mention deletion")
        self.assertEqual((case["expected_status"], case["expected_steps"]), ("NOT_APPLICABLE", []))

    def test_every_case_has_title_and_reason(self):
        for case in cc.CASES:
            self.assertTrue(case["title"] and len(case["why"]) > 40, case["id"])
        self.assertEqual(len(cc.CASES), 21)
        self.assertEqual(len({c["id"] for c in cc.CASES}), 21, "case ids must be unique")

    def test_decoy_never_appears_in_answers(self):
        decoy_resources = {"search_902", "backup_919", "tombstone_919", "search_9081",
                           "search_9022", "data_flow_9022", "audit_record_9260"}
        for case in cc.CASES:
            self.assertFalse({s["resource_id"] for s in case["expected_steps"]} & decoy_resources, case["id"])


if __name__ == "__main__":
    unittest.main()
