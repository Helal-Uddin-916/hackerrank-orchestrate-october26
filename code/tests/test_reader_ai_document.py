"""Tests for code/audit/readers/ai_document.py using the 15 saved AI answers (no AI calls).

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
from audit.llm import LLMClient  # noqa: E402
from audit.readers import ai_document  # noqa: E402
from audit.settings import LLM_CACHE_DIR, Settings  # noqa: E402
from audit.sighting import fingerprint_email, fingerprint_phone, parse_time  # noqa: E402

DATASET = os.path.join(os.path.dirname(CODE_DIR), "dataset")


def rows(name):
    with open(os.path.join(DATASET, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


MANIFEST = {r["file_id"]: r for r in rows("evidence_files.csv")}
DOC_IDS = sorted(f for f, r in MANIFEST.items() if r["file_path"].endswith((".png", ".pdf")))


def cache_only_client():
    # No key: the client must answer from the saved cache, never call the provider.
    def no_network(*args):
        raise AssertionError("a test tried to call the AI provider")
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", ""), LLM_CACHE_DIR, transport=no_network)


RESULTS = {}


def result(file_id):
    if file_id not in RESULTS:
        entry = MANIFEST[file_id]
        path = os.path.join(DATASET, entry["file_path"])
        answer = ai_document.extract(cache_only_client(), path)
        RESULTS[file_id] = ai_document.read_answer(file_id, path, answer, parse_time(entry["captured_at"]))
    return RESULTS[file_id]


class AllSavedAnswers(unittest.TestCase):

    def test_all_15_documents_come_from_the_cache(self):
        self.assertEqual(len(DOC_IDS), 15)
        for file_id in DOC_IDS:
            result(file_id)

    def test_no_raw_identifier_in_notes_or_raw_state(self):
        for file_id in DOC_IDS:
            r = result(file_id)
            for n in r.notes:
                self.assertNotIn("@", n.message, file_id)
            for s in r.sightings:
                self.assertNotIn("@", s.raw_state, file_id)

    def test_sightings_use_file_time(self):
        for file_id in DOC_IDS:
            for s in result(file_id).sightings:
                self.assertEqual((s.time_source, s.observed_at), ("file", s.file_captured_at))


class Case07Attachment(unittest.TestCase):
    """Sample case_07 expects support_001 deleted, citing evidence_58 (delivery_correction_attachment.pdf)."""

    def test_support_001_is_an_attachment_linked_to_the_person(self):
        req = next(r for r in rows("sample_deletion_requests.csv") if r["case_id"] == "case_07")
        me = next(u for u in rows("user_identities.csv") if u["email"].strip().lower() == req["email"].strip().lower())
        fps = {fingerprint_email(me["email"]), fingerprint_phone(me["phone"])}
        s = next(s for s in result("evidence_58").sightings if s.resource_id == "support_001")
        self.assertEqual((s.category, s.state, s.system_id), ("SUPPORT_ATTACHMENT", "PRESENT", "support_platform"))
        self.assertTrue(s.identifier_fps & fps, "must link to the case_07 person")
        self.assertLessEqual(s.file_captured_at, parse_time(req["audit_at"]))


class OtherDocuments(unittest.TestCase):

    def by_id(self, file_id):
        return {s.resource_id: s for s in result(file_id).sightings}

    def test_backups_restored_or_unknown(self):
        s = self.by_id("evidence_32")
        self.assertEqual(s["backup_9286"].state, "RESTORE_ALLOWED")
        self.assertEqual(s["backup_9281"].state, "RESTORE_ALLOWED", "named only in a restore event")
        self.assertEqual(s["backup_9287"].state, "UNKNOWN")
        self.assertEqual(s["backup_9287"].extras.get("retention_until"), "2026-07-26T00:00:00Z")

    def test_split_pdf_time_is_repaired(self):
        self.assertEqual(ai_document.parse_loose_date("18 Jun 2026 10 24 UTC", 2026), "2026-06-18T10:24:00Z")
        self.assertEqual(ai_document.parse_loose_date("26 Apr 02:00", 2026), "2026-04-26T02:00:00Z")
        self.assertEqual(ai_document.parse_loose_date("2026-08-25T04:03:00Z", 2026), "2026-08-25T04:03:00Z")
        self.assertIsNone(ai_document.parse_loose_date("moments ago", 2026))

    def test_security_record_keep_until_from_pdf(self):
        s = self.by_id("evidence_55")["audit_record_003"]
        self.assertEqual((s.category, s.state), ("SECURITY_AUDIT_LOG", "PRESENT"))
        self.assertEqual(s.extras["retention_until"], "2027-08-25T04:03:00Z")
        self.assertEqual(s.user_refs, frozenset({"user_03"}))

    def test_data_flows_active_and_marketing_profile(self):
        flows = self.by_id("evidence_14")
        self.assertEqual(flows["data_flow_9846"].state, "FLOW_ACTIVE")
        self.assertTrue(flows["data_flow_9846"].identifier_fps)
        marketing = [s for s in result("evidence_60").sightings if s.resource_id == "marketing_001"]
        self.assertEqual([(s.category, s.state) for s in marketing], [("MARKETING_PROFILE", "PRESENT")])

    def test_entries_without_record_ids_are_counted_not_turned_into_sightings(self):
        r = result("evidence_27")  # disputed orders register: ORD- numbers only
        self.assertEqual(r.sightings, [])
        self.assertTrue(any("side report" in n.message for n in r.notes))
        self.assertTrue(any("addressed to the reader" in n.message for n in r.notes))


if __name__ == "__main__":
    unittest.main()


class CachePrivacy(unittest.TestCase):
    """D-034: saved transcriptions hold fingerprints, never raw identifiers."""

    RAW = {"document_title": "Console", "page_time_text": None, "contains_instructions_to_reader": False, "unreadable": [],
           "records": [
               {"resource_id": "CS-1 / support_7", "system_text": None, "status_text": "Open",
                "identifiers": [{"kind": "email", "value": "Someone@Example.com"}, {"kind": "phone", "value": "+1-555-0100"},
                                {"kind": "name", "value": "Pat Example"}, {"kind": "user_reference", "value": "user_7"}],
                "dates": [{"label": "Retention end", "value": "26 Jul 2026"}],
                "facts": [{"label": "Uploader", "value": "customer"}, {"label": "Default address", "value": "1 Main St"}],
                "where": "table_row"},
               {"resource_id": "Pat Example", "system_text": None, "status_text": None, "identifiers": [],
                "dates": [], "facts": [], "where": "detail_panel"}],
           "events": [{"time_text": "25 Apr", "resource_id": "backup_1", "text": "backup_1 restored · Completed"},
                      {"time_text": "09:12", "resource_id": None, "text": "Pat Example: where is my order? someone@example.com"}]}

    def test_scrub_keeps_what_the_reader_needs_and_nothing_personal(self):
        out = ai_document.scrub_answer(self.RAW)
        text = json.dumps(out)
        for raw in ("@", "555", "Pat Example", "1 Main St"):
            self.assertNotIn(raw, text)
        first = out["records"][0]
        self.assertEqual(first["resource_id"], "support_7")
        self.assertIn({"kind": "email", "value": "fp:email:" + fingerprint_email("someone@example.com")}, first["identifiers"])
        self.assertIn({"kind": "user_reference", "value": "user_7"}, first["identifiers"])
        self.assertEqual(first["facts"][0], {"label": "Uploader", "value": "customer"})
        self.assertEqual(first["dates"], self.RAW["records"][0]["dates"])
        self.assertEqual(out["records"][1]["resource_id"], "[entry without record id]")
        self.assertEqual(out["events"][0]["text"], "backup_1 restored · Completed")
        self.assertEqual(out["events"][1]["text"], "[removed]")

    def test_scrubbed_answer_reads_the_same_as_the_raw_one(self):
        at = parse_time("2026-09-01T00:00:00Z")
        raw = ai_document.read_answer("evidence_x", "x_attachment.pdf", self.RAW, at)
        scrubbed = ai_document.read_answer("evidence_x", "x_attachment.pdf", ai_document.scrub_answer(self.RAW), at)
        key = lambda r: [(s.resource_id, s.state, s.category, s.identifier_fps, s.user_refs, s.pii_fields_present, s.extras)
                         for s in r.sightings]
        self.assertEqual(key(raw), key(scrubbed))

    def test_scrubbing_twice_changes_nothing(self):
        # Regression: a second pass used to fingerprint the fingerprints.
        once = ai_document.scrub_answer(self.RAW)
        self.assertEqual(ai_document.scrub_answer(once), once)

    def test_tokens_inside_kept_text_are_left_alone(self):
        raw = dict(self.RAW, events=[{"time_text": None, "resource_id": "backup_1",
                                      "text": "backup_1 restored for someone@example.com, call 12345678901"}])
        once = ai_document.scrub_answer(raw)
        text = once["events"][0]["text"]
        self.assertEqual(text.count("fp:"), 2)
        self.assertEqual(ai_document.scrub_answer(once)["events"][0]["text"], text)

    def test_saved_cache_holds_no_raw_identifiers(self):
        import glob
        import re
        for path in glob.glob(os.path.join(LLM_CACHE_DIR, "*.json")):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), path)
            self.assertIsNone(re.search(r"\+\d[\d\s().-]{7,}\d", text), path)
            for name in re.findall(r'"kind": "name",\s*"value": "([^"]*)"', text):
                self.assertEqual(name, "fp:name", path)
