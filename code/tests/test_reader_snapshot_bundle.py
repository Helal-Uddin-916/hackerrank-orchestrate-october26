"""Tests for code/audit/readers/snapshot_bundle.py on all real bundles and our hard case.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import collections
import csv
import importlib.util
import json
import os
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.readers import snapshot_bundle  # noqa: E402
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
    return text, snapshot_bundle.read(file_id, path, text, parse_time(entry["captured_at"]))


class AllRealBundles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for entry in read_csv(os.path.join(DATASET, "evidence_files.csv")):
            path = os.path.join(DATASET, entry["file_path"])
            if path.endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if snapshot_bundle.can_read(path, text):
                    cls.results[entry["file_id"]] = (text, snapshot_bundle.read(
                        entry["file_id"], path, text, parse_time(entry["captured_at"])))

    def test_all_twelve_bundles_recognised(self):
        self.assertEqual(len(self.results), 12)

    def test_every_record_read_with_no_notes(self):
        for file_id, (text, result) in self.results.items():
            snapshots = json.loads(text)["snapshots"]
            self.assertEqual(result.notes, [], file_id)
            self.assertEqual(len(result.sightings), sum(len(s["records"]) for s in snapshots), file_id)

    def test_every_snapshot_except_lookup_history_is_coverage(self):
        for file_id, (text, result) in self.results.items():
            snapshots = json.loads(text)["snapshots"]
            expected = sum(1 for s in snapshots if s["collection"] != "operational_record_history")
            self.assertEqual(len(result.coverage), expected, file_id)

    def test_no_unknown_states_in_real_data(self):
        states = collections.Counter(s.state for _, r in self.results.values() for s in r.sightings)
        self.assertNotIn("UNKNOWN", states)

    def test_lookup_history_rows_use_their_own_system_and_label(self):
        rows = [s for _, r in self.results.values() for s in r.sightings
                if s.category_source == "label" and s.reader == snapshot_bundle.NAME]
        self.assertGreater(len(rows), 100)
        self.assertNotIn("backup_restore_service", {s.system_id for s in rows})


class RealEvidence51(unittest.TestCase):
    """evidence_51 is behind samples case_31 (COMPLETE) and case_32 (INCOMPLETE tombstone)."""

    @classmethod
    def setUpClass(cls):
        _, cls.result = read_file(DATASET, "evidence_51")
        cls.by_id = {s.resource_id: s for s in cls.result.sightings}

    def test_clean_tombstone_is_redacted(self):
        s = self.by_id["tombstone_440"]
        self.assertEqual((s.state, s.category, s.system_id), ("REDACTED", "ACCOUNT_TOMBSTONE", "identity_service"))
        self.assertEqual(s.user_refs, frozenset({"user_541"}))

    def test_leaky_tombstone_is_exposed(self):
        s = self.by_id["tombstone_9178"]
        self.assertEqual(s.state, "EXPOSED")
        self.assertEqual(s.pii_fields_present, frozenset({"name", "email", "phone", "email_hash"}))

    def test_empty_account_snapshot_is_coverage(self):
        accounts = [c for c in self.result.coverage if c.system_id == "account_service"]
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].store, "account_service/customer_profiles")
        self.assertEqual(accounts[0].checked_at, parse_time("2026-09-15T12:00:00Z"))
        self.assertFalse([s for s in self.result.sightings if s.system_id == "account_service"])

    def test_security_records_present_and_linked(self):
        self.assertEqual((self.by_id["audit_record_6909"].state, self.by_id["audit_record_6909"].user_refs),
                         ("PRESENT", frozenset({"user_541"})))


class RealEvidence02(unittest.TestCase):
    """evidence_02 is the late bundle (saved 2026-09-17) from sample case_27."""

    @classmethod
    def setUpClass(cls):
        _, cls.result = read_file(DATASET, "evidence_02")
        cls.by_id = {s.resource_id: s for s in cls.result.sightings}

    def test_inner_snapshot_times_are_kept(self):
        s = self.by_id["account_3866"]
        self.assertEqual(s.observed_at, parse_time("2026-09-17T12:00:00Z"))
        self.assertEqual(s.file_captured_at, parse_time("2026-09-17T12:00:00Z"))
        self.assertEqual((s.state, s.user_refs), ("PRESENT", frozenset({"user_27"})))
        older = self.by_id["support_4707"]
        self.assertEqual(older.observed_at, parse_time("2026-09-15T12:00:00Z"))
        self.assertLess(older.observed_at, older.file_captured_at)

    def test_redacted_message_and_anonymized_analytics(self):
        self.assertEqual(self.by_id["support_4707"].state, "REDACTED")
        a = self.by_id["analytics_4574"]
        self.assertEqual((a.state, a.identifier_fps, a.user_refs), ("ANONYMIZED", frozenset(), frozenset()))


class HardCaseTombstone(unittest.TestCase):

    def test_hash_only_tombstone_is_exposed_and_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            _, result = read_file(tmp, "evidence_914")
        tomb = next(s for s in result.sightings if s.resource_id == "tombstone_914")
        self.assertEqual((tomb.state, tomb.pii_fields_present), ("EXPOSED", frozenset({"email_hash"})))
        self.assertIn(fingerprint_email("willow@example.com"), tomb.identifier_fps)
        self.assertIn("account_service/customer_profiles", {c.store for c in result.coverage})
        decoy = next(s for s in result.sightings if s.resource_id == "tombstone_919")
        self.assertEqual(decoy.state, "REDACTED")


class OddInput(unittest.TestCase):

    def read_text(self, snapshots):
        text = json.dumps({"exported_at": "2026-09-01T00:00:00Z", "snapshots": snapshots})
        return snapshot_bundle.read("evidence_x", "x.json", text, parse_time("2026-09-01T00:00:00Z"))

    def test_support_message_with_email_is_exposed_and_linked(self):
        result = self.read_text([{"captured_at": "2026-09-01T00:00:00Z", "data_store": "support_platform",
                                  "collection": "support_conversations", "records": [
                                      {"resource_id": "support_1", "subject_reference": "user_1",
                                       "message_text": "Please call me, my email is a@example.com"}]}])
        s = result.sightings[0]
        self.assertEqual((s.state, s.pii_fields_present), ("EXPOSED", frozenset({"email"})))
        self.assertIn(fingerprint_email("a@example.com"), s.identifier_fps)

    def test_attachment_owner_kept_as_fingerprint(self):
        result = self.read_text([{"captured_at": "2026-09-01T00:00:00Z", "data_store": "support_platform",
                                  "collection": "ticket_attachments", "records": [
                                      {"resource_id": "support_2", "uploaded_by": "b@example.com"}]}])
        s = result.sightings[0]
        self.assertEqual(s.category, "SUPPORT_ATTACHMENT")
        self.assertEqual(s.extras["attachment_owners"], (fingerprint_email("b@example.com"),))

    def test_unknown_collection_gives_a_note(self):
        result = self.read_text([{"captured_at": "2026-09-01T00:00:00Z", "data_store": "marketing",
                                  "collection": "mailing_lists", "records": [{"resource_id": "m_1"}]}])
        self.assertEqual((result.sightings, result.coverage), ([], []))
        self.assertIn("unknown collection", result.notes[0].message)

    def test_snapshot_newer_than_file_gives_no_coverage_but_keeps_records_flagged(self):
        result = self.read_text([{"captured_at": "2026-09-02T00:00:00Z", "data_store": "account_service",
                                  "collection": "customer_profiles", "records": [
                                      {"resource_id": "account_1", "customer_reference": "user_1"}]}])
        self.assertEqual(result.coverage, [])
        self.assertEqual(len(result.notes), 1)
        self.assertIn("no coverage", result.notes[0].message)
        self.assertTrue(result.sightings[0].future_dated)

    def test_not_a_bundle(self):
        self.assertFalse(snapshot_bundle.can_read("x.json", '{"service_exports": {}}'))


if __name__ == "__main__":
    unittest.main()
