"""Tests for code/audit/sighting.py: valid shapes pass, every kind of bad shape is rejected.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import dataclasses
import os
import sys
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.sighting import (Coverage, Sighting, SightingError, fingerprint_email,  # noqa: E402
                            fingerprint_phone, parse_time)

REPO_ROOT = os.path.dirname(CODE_DIR)
EMAIL_FP = fingerprint_email("river.new@example.com")


def good_sighting(**changes):
    """The search_052 sighting traced by hand in Phase 1 (evidence_03 line 26), with a fake identity."""
    values = dict(
        file_id="evidence_03", location="line 26", reader="handover_text",
        observed_at=parse_time("2026-10-01T12:00:00Z"), time_source="observation",
        file_captured_at=parse_time("2026-10-01T12:00:00Z"),
        system_id="search_service", store="search_service", resource_id="search_052",
        state="PRESENT", raw_state="FOUND",
        category="SEARCH_INDEX_ENTRY", category_source="label",
        identifier_fps=frozenset({EMAIL_FP}), pii_fields_present=frozenset({"email"}),
    )
    values.update(changes)
    return Sighting(**values)


class SightingShape(unittest.TestCase):

    def test_good_sighting_passes(self):
        s = good_sighting()
        self.assertEqual((s.state, s.category), ("PRESENT", "SEARCH_INDEX_ENTRY"))

    def test_sighting_is_read_only(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            good_sighting().state = "ABSENT"

    def assertRejected(self, text, **changes):
        with self.assertRaises(SightingError) as ctx:
            good_sighting(**changes)
        self.assertIn(text, str(ctx.exception))

    def test_unknown_state(self):
        self.assertRejected("state", state="GONE")

    def test_missing_resource(self):
        self.assertRejected("resource_id", resource_id="")

    def test_time_without_timezone(self):
        from datetime import datetime
        self.assertRejected("timezone-aware", observed_at=datetime(2026, 10, 1, 12, 0))

    def test_observed_after_file_saved_needs_the_future_flag(self):
        self.assertRejected("future_dated", observed_at=parse_time("2026-10-02T00:00:00Z"))

    def test_future_flag_accepted_when_true(self):
        s = good_sighting(observed_at=parse_time("2026-10-02T00:00:00Z"), future_dated=True)
        self.assertTrue(s.future_dated)

    def test_future_flag_rejected_when_false(self):
        self.assertRejected("future_dated", future_dated=True)

    def test_bad_time_source(self):
        self.assertRejected("time_source", time_source="guess")

    def test_raw_email_instead_of_fingerprint(self):
        self.assertRejected("fingerprints", identifier_fps=frozenset({"river.new@example.com"}))

    def test_raw_email_in_raw_state(self):
        self.assertRejected("raw email", raw_state="FOUND river.new@example.com")

    def test_raw_email_in_extras(self):
        self.assertRejected("raw email", extras={"attachment_owners": "a@example.com"})

    def test_unknown_extra(self):
        self.assertRejected("unknown extras", extras={"favourite_colour": "blue"})

    def test_bad_user_ref(self):
        self.assertRejected("user_<number>", user_refs=frozenset({"customer 32"}))

    def test_unknown_pii_field(self):
        self.assertRejected("pii_fields_present", pii_fields_present=frozenset({"shoe_size"}))

    def test_category_without_source(self):
        self.assertRejected("both set", category_source="")

    def test_lowercase_category(self):
        self.assertRejected("UPPER_SNAKE_CASE", category="search_index_entry")

    def test_category_outside_the_rules_is_allowed(self):
        # D-015: records that fit no rule still become sightings, so they can reach the side report.
        self.assertEqual(good_sighting(category="ORDER_RECORD").category, "ORDER_RECORD")


class CoverageShape(unittest.TestCase):

    def test_good_coverage_passes(self):
        c = Coverage("evidence_54", "account_service", "failover_replica",
                     parse_time("2026-09-02T23:00:00Z"), parse_time("2026-09-02T23:00:00Z"))
        self.assertEqual(c.store, "failover_replica")

    def test_checked_after_file_saved(self):
        with self.assertRaises(SightingError):
            Coverage("evidence_54", "account_service", "failover_replica",
                     parse_time("2026-09-03T00:00:00Z"), parse_time("2026-09-02T23:00:00Z"))


class Fingerprints(unittest.TestCase):

    def test_email_fingerprint_matches_every_dataset_email_hash(self):
        with open(os.path.join(REPO_ROOT, "dataset", "user_identities.csv"), encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        mismatches = sum(fingerprint_email(r["email"]) != r["email_hash"] for r in rows)
        self.assertEqual((len(rows), mismatches), (850, 0))

    def test_email_fingerprint_ignores_case_and_spaces(self):
        self.assertEqual(fingerprint_email("  River.New@Example.COM "), EMAIL_FP)

    def test_phone_fingerprint_ignores_formatting(self):
        self.assertEqual(fingerprint_phone("+1-555-0101"), fingerprint_phone("+1 (555) 0101"))
        self.assertNotEqual(fingerprint_phone("+1-555-0101"), fingerprint_phone("+1-555-0102"))

    def test_parse_time_accepts_both_utc_styles(self):
        self.assertEqual(parse_time("2026-09-15T12:00:00Z"), parse_time("2026-09-15T12:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
