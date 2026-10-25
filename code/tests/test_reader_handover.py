"""Tests for code/audit/readers/handover.py on real evidence and on our hard cases.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import importlib.util
import os
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.readers import handover  # noqa: E402
from audit.sighting import fingerprint_email, parse_time  # noqa: E402

REPO_ROOT = os.path.dirname(CODE_DIR)
DATASET = os.path.join(REPO_ROOT, "dataset")

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_file(dataset_dir, file_id):
    entry = next(r for r in read_csv(os.path.join(dataset_dir, "evidence_files.csv")) if r["file_id"] == file_id)
    path = os.path.join(dataset_dir, entry["file_path"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, handover.read(file_id, path, text, parse_time(entry["captured_at"]))


class RealEvidence03(unittest.TestCase):
    """evidence_03 is the handover file behind sample case_11 (traced by hand in Phase 1)."""

    @classmethod
    def setUpClass(cls):
        cls.text, cls.result = read_file(DATASET, "evidence_03")

    def test_recognised(self):
        self.assertTrue(handover.can_read("x.txt", self.text))

    def test_every_row_becomes_a_sighting_and_nothing_else_is_noted(self):
        rows = [line for line in self.text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(self.result.sightings), len(rows))
        self.assertEqual(self.result.notes, [])

    def test_case_11_person_gets_exactly_the_six_hand_traced_sightings(self):
        identity = next(u for u in read_csv(os.path.join(DATASET, "user_identities.csv")) if u["user_id"] == "user_32")
        fp = fingerprint_email(identity["email"])
        mine = {s.resource_id: s.state for s in self.result.sightings
                if fp in s.identifier_fps or "user_32" in s.user_refs}
        self.assertEqual(mine, {
            "account_4248": "ABSENT",
            "search_052": "PRESENT",
            "analytics_2034": "EXPOSED",
            "support_864": "EXPOSED",
            "data_flow_9522": "FLOW_ACTIVE",
            "audit_record_9394": "PRESENT",
        })

    def test_backup_and_flow_words_depend_on_category(self):
        states = {s.resource_id: s.state for s in self.result.sightings}
        self.assertEqual(states["backup_3870"], "RESTORE_ALLOWED")
        self.assertEqual(states["backup_706"], "RESTORE_BLOCKED")
        self.assertEqual(states["data_flow_2901"], "FLOW_DISABLED")

    def test_absent_rows_keep_the_link_but_hold_no_personal_data(self):
        absent = [s for s in self.result.sightings if s.state == "ABSENT"]
        self.assertTrue(absent)
        for s in absent:
            self.assertTrue(s.identifier_fps, s.resource_id)
            self.assertEqual(s.pii_fields_present, frozenset(), s.resource_id)

    def test_times_and_proof_are_attached(self):
        s = next(s for s in self.result.sightings if s.resource_id == "search_052")
        self.assertEqual((s.file_id, s.time_source, s.category_source), ("evidence_03", "observation", "label"))
        self.assertTrue(s.location.startswith("line "))
        self.assertEqual(s.observed_at, parse_time("2026-10-01T12:00:00Z"))


class HardCaseFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cc.build(cls.tmp.name)
        cls.manifest = read_csv(os.path.join(cls.tmp.name, "evidence_files.csv"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_handover_file_reads_cleanly_except_the_planted_note(self):
        for entry in self.manifest:
            if not entry["file_path"].endswith(".txt"):
                continue
            text, result = read_file(self.tmp.name, entry["file_id"])
            rows = [line for line in text.splitlines() if line.startswith("- ")]
            self.assertEqual(len(result.sightings), len(rows), entry["file_id"])
            expected_notes = 1 if entry["file_id"] == "evidence_933" else 0
            self.assertEqual(len(result.notes), expected_notes, entry["file_id"])

    def test_planted_instruction_is_ignored_and_not_copied(self):
        _, result = read_file(self.tmp.name, "evidence_933")
        note = result.notes[0]
        self.assertEqual(note.message, "not a record row; ignored")
        self.assertNotIn("@", note.message + note.location)
        states = {s.resource_id: s.state for s in result.sightings}
        self.assertEqual(states["search_930"], "PRESENT", "the planted note must not change any state")

    def test_order_record_passes_through_for_the_side_report(self):
        _, result = read_file(self.tmp.name, "evidence_930")
        order = next(s for s in result.sightings if s.resource_id == "order_927")
        self.assertEqual((order.category, order.state), ("ORDER_RECORD", "PRESENT"))


class OddRows(unittest.TestCase):

    def read_text(self, *rows):
        text = "\n".join([handover.TITLE, "Snapshot: 2026-09-01T00:00:00Z", ""] + list(rows))
        return handover.read("evidence_x", "x.txt", text, parse_time("2026-09-01T00:00:00Z"))

    def test_unknown_state_word_becomes_unknown_with_a_note(self):
        result = self.read_text("- 2026-09-01T00:00:00Z | search_service | search_1 | SEARCH_INDEX_ENTRY | MAYBE | user_1")
        self.assertEqual(result.sightings[0].state, "UNKNOWN")
        self.assertIn("unknown state word", result.notes[0].message)

    def test_unreadable_identifier_gives_a_note_not_a_link(self):
        result = self.read_text("- 2026-09-01T00:00:00Z | search_service | search_1 | SEARCH_INDEX_ENTRY | FOUND | J. Smith")
        self.assertEqual((result.sightings[0].identifier_fps, result.sightings[0].user_refs), (frozenset(), frozenset()))
        self.assertIn("identifier column", result.notes[0].message)

    def test_email_hash_identifier_is_used_directly(self):
        h = fingerprint_email("someone@example.com")
        result = self.read_text(f"- 2026-09-01T00:00:00Z | identity_service | tombstone_1 | ACCOUNT_TOMBSTONE | FOUND | {h}")
        s = result.sightings[0]
        self.assertEqual((s.identifier_fps, s.pii_fields_present), (frozenset({h}), frozenset({"email_hash"})))

    def test_row_newer_than_its_file_is_kept_for_linking_only(self):
        # D-019: kept as a flagged sighting with a note, never dropped silently.
        result = self.read_text("- 2026-09-02T00:00:00Z | search_service | search_1 | SEARCH_INDEX_ENTRY | FOUND | user_1")
        self.assertTrue(result.sightings[0].future_dated)
        self.assertEqual(result.sightings[0].user_refs, frozenset({"user_1"}))
        self.assertIn("D-019", result.notes[0].message)

    def test_not_a_handover_file(self):
        self.assertFalse(handover.can_read("x.json", '{"snapshots": []}'))


if __name__ == "__main__":
    unittest.main()
