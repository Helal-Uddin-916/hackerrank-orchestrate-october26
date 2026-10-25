"""Tests for code/audit/evidence.py: all 94 files read once each, nothing skipped silently.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import collections
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.evidence import load_evidence  # noqa: E402
from audit.llm import LLMClient  # noqa: E402
from audit.settings import DATASET_DIR, LLM_CACHE_DIR, Settings  # noqa: E402

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def cache_only_client():
    def no_network(*args):
        raise AssertionError("tried to call the AI provider")
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", ""), LLM_CACHE_DIR, transport=no_network)


EVIDENCE = load_evidence(DATASET_DIR, cache_only_client())


class FullDataset(unittest.TestCase):

    def test_all_94_files_have_a_reader(self):
        self.assertEqual(len(EVIDENCE.reader_by_file), 94)
        self.assertNotIn("unread", set(EVIDENCE.reader_by_file.values()))

    def test_files_per_reader(self):
        files = collections.Counter(EVIDENCE.reader_by_file.values())
        self.assertEqual(dict(files), {
            "sql_snapshot": 24, "log_events": 26, "handover_text": 1, "service_export_json": 11,
            "snapshot_bundle_json": 12, "record_list_json": 5, "ai_document": 15,
        })

    def test_no_file_level_failures(self):
        # Match the loader's own failure messages exactly: a log's noise summary can mention an event
        # called thumbnail_failed, which is not a file failure.
        failures = ("file listed in the manifest is missing", "no reader recognises", "more than one reader",
                    "reader failed:", "image/PDF not read")
        bad = [n for n in EVIDENCE.notes if n.location == "file" and n.message.startswith(failures)]
        self.assertEqual(bad, [])

    def test_every_sighting_and_coverage_points_to_a_known_file(self):
        for s in EVIDENCE.sightings:
            self.assertIn(s.file_id, EVIDENCE.reader_by_file)
            self.assertEqual(s.file_captured_at, EVIDENCE.captured_at[s.file_id])
        for c in EVIDENCE.coverage:
            self.assertIn(c.file_id, EVIDENCE.reader_by_file)

    def test_no_raw_identifier_in_any_note(self):
        for n in EVIDENCE.notes:
            self.assertNotIn("@", n.message + n.location)

    def test_only_the_two_known_future_rows_are_future_dated(self):
        future = sorted((s.file_id, s.resource_id) for s in EVIDENCE.sightings if s.future_dated)
        self.assertEqual(future, [("evidence_13", "account_1417"), ("evidence_36", "account_5043")])


class Robustness(unittest.TestCase):

    def test_hard_case_dataset_loads_without_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            evidence = load_evidence(tmp)
        self.assertEqual(set(evidence.reader_by_file.values()) - {"unread"},
                         {"handover_text", "snapshot_bundle_json", "sql_snapshot", "service_export_json"})
        self.assertNotIn("unread", evidence.reader_by_file.values())

    def test_missing_file_and_unknown_format_are_noted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            with open(os.path.join(tmp, "evidence_files.csv"), "a", encoding="utf-8") as f:
                f.write("evidence_998,evidence/records/missing.json,x,2026-09-01T00:00:00Z\n")
                f.write("evidence_999,evidence/records/odd.json,x,2026-09-01T00:00:00Z\n")
            with open(os.path.join(tmp, "evidence", "records", "odd.json"), "w") as f:
                f.write('{"something": "else"}')
            evidence = load_evidence(tmp)
        self.assertEqual(evidence.reader_by_file["evidence_998"], "unread")
        self.assertEqual(evidence.reader_by_file["evidence_999"], "unread")
        messages = {n.file_id: n.message for n in evidence.notes if n.location == "file"}
        self.assertIn("missing", messages["evidence_998"])
        self.assertIn("no reader", messages["evidence_999"])

    def test_images_without_an_ai_client_are_noted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc.build(tmp)
            os.makedirs(os.path.join(tmp, "evidence", "images"), exist_ok=True)
            shutil.copy(os.path.join(DATASET_DIR, "evidence", "images", "backup_restore_console_20260429.png"),
                        os.path.join(tmp, "evidence", "images", "x.png"))
            with open(os.path.join(tmp, "evidence_files.csv"), "a", encoding="utf-8") as f:
                f.write("evidence_997,evidence/images/x.png,x,2026-09-01T00:00:00Z\n")
            evidence = load_evidence(tmp, client=None)
        self.assertEqual(evidence.reader_by_file["evidence_997"], "unread")


if __name__ == "__main__":
    unittest.main()
