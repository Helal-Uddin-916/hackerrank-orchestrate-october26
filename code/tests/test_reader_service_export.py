"""Tests for code/audit/readers/service_export.py on all real export files and our hard case.

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
from audit.readers import service_export  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

REPO_ROOT = os.path.dirname(CODE_DIR)
DATASET = os.path.join(REPO_ROOT, "dataset")

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_entry(dataset_dir, entry):
    path = os.path.join(dataset_dir, entry["file_path"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, service_export.read(entry["file_id"], path, text, parse_time(entry["captured_at"]))


def manifest_entry(dataset_dir, file_id):
    return next(r for r in read_csv(os.path.join(dataset_dir, "evidence_files.csv")) if r["file_id"] == file_id)


class AllRealExports(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for entry in read_csv(os.path.join(DATASET, "evidence_files.csv")):
            path = os.path.join(DATASET, entry["file_path"])
            if not path.endswith(".json"):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if service_export.can_read(path, text):
                cls.results[entry["file_id"]] = (text, service_export.read(
                    entry["file_id"], path, text, parse_time(entry["captured_at"])))

    def test_all_eleven_exports_recognised(self):
        self.assertEqual(len(self.results), 11)

    def test_every_entry_read_with_no_notes(self):
        for file_id, (text, result) in self.results.items():
            data = json.loads(text)["service_exports"]
            entries = sum(len(v) for sections in data.values() for v in sections.values())
            self.assertEqual(result.notes, [], file_id)
            self.assertEqual(len(result.sightings), entries, file_id)  # every query has exactly one hit

    def test_no_sighting_is_newer_than_its_file(self):
        for file_id, (_, result) in self.results.items():
            for s in result.sightings:
                self.assertLessEqual(s.observed_at, s.file_captured_at, file_id)


class RealEvidence92(unittest.TestCase):
    """evidence_92 is the file behind sample case_27 (traced by hand in Phase 1)."""

    @classmethod
    def setUpClass(cls):
        _, cls.result = read_entry(DATASET, manifest_entry(DATASET, "evidence_92"))
        cls.by_id = {s.resource_id: s for s in cls.result.sightings}

    def test_case_27_person_linked_records(self):
        mine = {s.resource_id: s.state for s in self.result.sightings if "user_27" in s.user_refs}
        self.assertEqual(mine, {
            "backup_5970": "RESTORE_BLOCKED",
            "data_flow_2295": "FLOW_DISABLED",
            "audit_record_3830": "PRESENT",
            "support_4707": "REDACTED",
        })

    def test_records_with_no_identifier_are_kept_unlinked(self):
        # Linking these to user_27 is the later job of D-005; the reader must not invent a link.
        for rid, state in (("account_3866", "ABSENT"), ("analytics_4574", "ANONYMIZED")):
            s = self.by_id[rid]
            self.assertEqual(s.state, state)
            self.assertEqual((s.identifier_fps, s.user_refs, s.pii_fields_present), (frozenset(),) * 3)

    def test_categories_come_from_section_names(self):
        self.assertEqual((self.by_id["support_4707"].category, self.by_id["support_4707"].category_source),
                         ("SUPPORT_MESSAGE", "section"))
        self.assertEqual(self.by_id["data_flow_2295"].category, "DATA_FLOW")

    def test_extras_carry_policy_facts(self):
        self.assertEqual(self.by_id["audit_record_3830"].extras, {"retention_until": "2034-09-30T23:59:59Z"})
        self.assertEqual(self.by_id["backup_5970"].extras, {"restore_allowed": False})
        self.assertEqual(self.by_id["data_flow_2295"].extras, {"enabled": False})

    def test_exposed_records_show_which_identifiers(self):
        self.assertEqual(self.by_id["analytics_3697"].state, "EXPOSED")
        self.assertEqual(self.by_id["analytics_3697"].pii_fields_present, frozenset({"email"}))
        # display_name is empty in all 58 real search hits; only the email is left.
        self.assertEqual(self.by_id["search_1993"].pii_fields_present, frozenset({"email"}))
        self.assertEqual(self.by_id["data_flow_3827"].state, "FLOW_ACTIVE")


class HardCaseAndOddInput(unittest.TestCase):

    def test_case_915_retention_date_is_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            _, result = read_entry(tmp, manifest_entry(tmp, "evidence_929"))
        record = next(s for s in result.sightings if s.resource_id == "audit_record_926")
        self.assertEqual(record.extras["retention_until"], "2026-06-30T23:59:59Z")
        self.assertEqual(record.user_refs, frozenset({"user_926"}))

    def read_text(self, exports):
        text = json.dumps({"exported_at": "2026-09-01T00:00:00Z", "service_exports": exports})
        return service_export.read("evidence_x", "x.json", text, parse_time("2026-09-01T00:00:00Z"))

    def test_unknown_section_gives_a_note(self):
        result = self.read_text({"marketing_service": {"lists": [{"resource_id": "m_1"}]}})
        self.assertEqual(result.sightings, [])
        self.assertIn("unknown service section", result.notes[0].message)

    def test_search_query_with_no_hits_is_absent_and_linked(self):
        result = self.read_text({"search_service": {"index_queries": [
            {"resource_id": "search_1", "observed_at": "2026-09-01T00:00:00Z", "query": "a@example.com",
             "hits": {"total": 0, "documents": []}}]}})
        s = result.sightings[0]
        self.assertEqual((s.state, len(s.identifier_fps), s.pii_fields_present), ("ABSENT", 1, frozenset()))

    def test_broken_entry_gives_a_note_not_a_crash(self):
        result = self.read_text({"backup_service": {"restore_entries": [{"resource_id": "backup_1"}]}})
        self.assertEqual(result.sightings, [])
        self.assertIn("entry rejected", result.notes[0].message)

    def test_not_an_export(self):
        self.assertFalse(service_export.can_read("x.json", '{"snapshots": []}'))
        self.assertFalse(service_export.can_read("x.txt", "CUSTOMER PLATFORM - SHIFT HANDOVER"))


if __name__ == "__main__":
    unittest.main()
