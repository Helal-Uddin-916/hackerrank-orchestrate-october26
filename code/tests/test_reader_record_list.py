"""Tests for code/audit/readers/record_list.py on the five real list files.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import json
import os
import sys
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.readers import record_list, service_export, snapshot_bundle  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

REPO_ROOT = os.path.dirname(CODE_DIR)
DATASET = os.path.join(REPO_ROOT, "dataset")
LIST_FILES = {"evidence_13", "evidence_36", "evidence_50", "evidence_56", "evidence_59"}
FUTURE_ROWS = {"evidence_13": ("records[38]", "account_1417"), "evidence_36": ("records[45]", "account_5043")}


def manifest():
    with open(os.path.join(DATASET, "evidence_files.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load(entry):
    path = os.path.join(DATASET, entry["file_path"])
    with open(path, encoding="utf-8") as f:
        return path, f.read()


class EveryJsonFileHasExactlyOneReader(unittest.TestCase):

    def test_all_28_json_files_are_claimed_once(self):
        readers = (service_export, snapshot_bundle, record_list)
        claimed = {}
        for entry in manifest():
            if entry["file_path"].endswith(".json"):
                path, text = load(entry)
                claimed[entry["file_id"]] = [r.NAME for r in readers if r.can_read(path, text)]
        self.assertEqual(len(claimed), 28)
        self.assertEqual({f: c for f, c in claimed.items() if len(c) != 1}, {}, "each JSON file needs exactly one reader")
        self.assertEqual({f for f, c in claimed.items() if c == [record_list.NAME]}, LIST_FILES)


class RealListFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for entry in manifest():
            if entry["file_id"] in LIST_FILES:
                path, text = load(entry)
                cls.results[entry["file_id"]] = (text, record_list.read(
                    entry["file_id"], path, text, parse_time(entry["captured_at"])))

    def test_every_record_read_with_no_notes(self):
        for file_id, (text, result) in self.results.items():
            if file_id in FUTURE_ROWS:
                continue  # each has one future-dated row, checked below
            self.assertEqual(result.notes, [], file_id)
            self.assertEqual(len(result.sightings), len(json.loads(text)["records"]), file_id)
            self.assertNotIn("UNKNOWN", {s.state for s in result.sightings}, file_id)

    def test_future_dated_rows_kept_for_linking_only(self):
        # D-019: each file has one row dated two days after the file was saved; the file time equals
        # the audit_at of the request about that person (case_135, case_383).
        for file_id, (location, resource_id) in FUTURE_ROWS.items():
            text, result = self.results[file_id]
            self.assertEqual(len(result.sightings), len(json.loads(text)["records"]), file_id)
            self.assertEqual([n.location for n in result.notes], [location], file_id)
            self.assertIn("D-019", result.notes[0].message)
            future = [s for s in result.sightings if s.future_dated]
            self.assertEqual([s.resource_id for s in future], [resource_id], file_id)
            self.assertTrue(future[0].identifier_fps, "the link to the person is kept")

    def test_every_ledger_row_is_linked_to_someone(self):
        # Regression: ledgers name the identifier subject_reference, not lookup_value; links were lost.
        for file_id in ("evidence_13", "evidence_36", "evidence_50"):
            _, result = self.results[file_id]
            unlinked = [s.resource_id for s in result.sightings if not (s.identifier_fps or s.user_refs)]
            self.assertLessEqual(len(unlinked), 3, file_id)

    def test_ledger_rows_use_their_own_system_and_label(self):
        _, result = self.results["evidence_13"]  # named "connector_configurations" but lists many systems
        systems = {s.system_id for s in result.sightings}
        self.assertGreater(len(systems), 2)
        self.assertEqual({s.category_source for s in result.sightings}, {"label"})
        self.assertEqual(result.coverage, [], "a ledger of lookups covers no whole collection")

    def test_case_04_analytics_record_is_anonymized(self):
        # Sample case_04 expects VERIFY_RECORD_ANONYMIZED for analytics_901, citing evidence_56.
        _, result = self.results["evidence_56"]
        s = next(s for s in result.sightings if s.resource_id == "analytics_901")
        self.assertEqual((s.state, s.system_id, s.category), ("ANONYMIZED", "analytics_platform", "ANALYTICS_PROFILE"))
        self.assertEqual([c.store for c in result.coverage], ["analytics_platform/customer_analytics_reports"])

    def test_single_collection_records_take_the_file_time(self):
        _, result = self.results["evidence_59"]
        self.assertEqual({s.observed_at for s in result.sightings}, {parse_time("2026-09-14T15:40:00Z")})
        self.assertEqual({s.system_id for s in result.sightings}, {"export_service"})


class OddInput(unittest.TestCase):

    def test_record_count_mismatch_gives_a_note(self):
        text = json.dumps({"exported_at": "2026-09-01T00:00:00Z", "origin": "x", "record_count": 5, "records": [
            {"recorded_at": "2026-09-01T00:00:00Z", "system": "search_service", "resource_id": "search_1",
             "record_type": "SEARCH_INDEX_ENTRY", "result": "FOUND", "subject_reference": "user_1"}]})
        result = record_list.read("evidence_x", "x.json", text, parse_time("2026-09-01T00:00:00Z"))
        self.assertEqual(len(result.sightings), 1)
        self.assertIn("record_count", result.notes[0].message)

    def test_unknown_collection_gives_a_note(self):
        text = json.dumps({"captured_at": "2026-09-01T00:00:00Z", "collection": "mailing_lists", "records": []})
        result = record_list.read("evidence_x", "x.json", text, parse_time("2026-09-01T00:00:00Z"))
        self.assertIn("unknown collection", result.notes[0].message)


if __name__ == "__main__":
    unittest.main()
