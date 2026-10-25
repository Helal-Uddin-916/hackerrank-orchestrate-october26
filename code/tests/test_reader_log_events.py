"""Tests for code/audit/readers/log_events.py on all 26 real logs and the 8 log-cited samples.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import csv
import os
import sys
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.readers import log_events  # noqa: E402
from audit.sighting import fingerprint_email, fingerprint_phone, parse_time  # noqa: E402

REPO_ROOT = os.path.dirname(CODE_DIR)
DATASET = os.path.join(REPO_ROOT, "dataset")


def rows(name):
    with open(os.path.join(DATASET, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


MANIFEST = {r["file_id"]: r for r in rows("evidence_files.csv")}
IDENTITIES = rows("user_identities.csv")
AUDIT = rows("user_audit_log.csv")
REQUESTS = {r["case_id"]: r for r in rows("sample_deletion_requests.csv")}


def subject(case_id):
    """(user_id, fingerprints) for a sample case's person, current and historical."""
    email = REQUESTS[case_id]["email"].strip().lower()
    uid = next((u["user_id"] for u in IDENTITIES if u["email"].strip().lower() == email), None) or next(
        a["user_id"] for a in AUDIT if a["field_name"] == "email" and email in (a["old_value"].lower(), a["new_value"].lower()))
    me = next(u for u in IDENTITIES if u["user_id"] == uid)
    fps = {fingerprint_email(me["email"]), fingerprint_phone(me["phone"]), me["email_hash"]}
    for a in AUDIT:
        if a["user_id"] == uid:
            for v in (a["old_value"], a["new_value"]):
                fps.add(fingerprint_email(v) if a["field_name"] == "email" else
                        fingerprint_phone(v) if a["field_name"] == "phone" else v)
    return uid, fps


def read(file_id):
    entry = MANIFEST[file_id]
    path = os.path.join(DATASET, entry["file_path"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return log_events.read(file_id, path, text, parse_time(entry["captured_at"]))


RESULTS = {}


def result(file_id):
    if file_id not in RESULTS:
        RESULTS[file_id] = read(file_id)
    return RESULTS[file_id]


class AllRealLogs(unittest.TestCase):

    def test_every_log_reads_without_rejections(self):
        logs = [fid for fid, e in MANIFEST.items() if e["file_path"].endswith(".log")]
        self.assertEqual(len(logs), 26)
        for file_id in logs:
            notes = result(file_id).notes
            bad = [n for n in notes if "not understood" in n.message or "rejected" in n.message or "UNKNOWN" in n.message]
            self.assertEqual(bad, [], file_id)
            self.assertTrue(result(file_id).sightings, f"{file_id} produced no sightings")

    def test_noise_is_counted_in_one_note_per_file(self):
        notes = [n for n in result("evidence_01").notes if n.location == "file"]
        self.assertEqual(len(notes), 1)
        self.assertIn("connection_acquired", notes[0].message)

    def test_no_raw_identifier_in_any_note(self):
        for file_id in RESULTS:
            for n in RESULTS[file_id].notes:
                self.assertNotIn("@", n.message, file_id)


class LogCitedSamples(unittest.TestCase):
    """Each sample's records, as its official answer needs them (docs/PHASE4_LOG_TRACE.md)."""

    def check(self, case_id, file_id, expected, unlinked=()):
        uid, fps = subject(case_id)
        sightings = result(file_id).sightings
        for resource_id, (category, state) in expected.items():
            mine = [s for s in sightings if s.resource_id == resource_id and (s.identifier_fps & fps or uid in s.user_refs)]
            self.assertTrue(mine, f"{case_id}: no sighting of {resource_id} linked to {uid}")
            self.assertIn((category, state), {(s.category, s.state) for s in mine}, f"{case_id} {resource_id}")
        for resource_id, (category, state) in dict(unlinked).items():
            found = [s for s in sightings if s.resource_id == resource_id]
            self.assertTrue(found, resource_id)
            self.assertIn((category, state), {(s.category, s.state) for s in found})
            self.assertFalse(any(s.identifier_fps or s.user_refs for s in found), f"{resource_id} must stay unlinked")

    def test_case_03(self):
        self.check("case_03", "evidence_94", {
            "account_7566": ("USER_PROFILE", "ABSENT"),
            "audit_record_2016": ("SECURITY_AUDIT_LOG", "PRESENT"),
        }, unlinked={"analytics_9406": ("ANALYTICS_PROFILE", "ANONYMIZED")})

    def test_case_10_missing_deletion_marker(self):
        self.check("case_10", "evidence_61", {"support_002": ("SECONDARY_IDENTIFIER", "PRESENT")})

    def test_case_12_opted_in_contact(self):
        self.check("case_12", "evidence_01", {
            "account_9797": ("USER_PROFILE", "ABSENT"),
            "analytics_9914": ("MARKETING_PROFILE", "PRESENT"),
            "audit_record_2945": ("SECURITY_AUDIT_LOG", "PRESENT"),
        })

    def test_case_15_active_connector_linked_by_trace(self):
        self.check("case_15", "evidence_01", {
            "account_4745": ("USER_PROFILE", "ABSENT"),
            "data_flow_9846": ("DATA_FLOW", "FLOW_ACTIVE"),
            "audit_record_1596": ("SECURITY_AUDIT_LOG", "PRESENT"),
        })

    def test_case_17_restorable_backup_linked_by_trace(self):
        self.check("case_17", "evidence_01", {
            "account_4092": ("USER_PROFILE", "ABSENT"),
            "audit_record_1797": ("SECURITY_AUDIT_LOG", "PRESENT"),
            "backup_3245": ("BACKUP_ARCHIVE", "RESTORE_ALLOWED"),
        })

    def test_case_18(self):
        self.check("case_18", "evidence_94", {
            "search_4013": ("SEARCH_INDEX_ENTRY", "PRESENT"),
            "analytics_037": ("ANALYTICS_PROFILE", "EXPOSED"),
            "support_5569": ("SUPPORT_MESSAGE", "EXPOSED"),
            "data_flow_4890": ("DATA_FLOW", "FLOW_ACTIVE"),
        })

    def test_case_19(self):
        self.check("case_19", "evidence_01", {
            "support_9553": ("SUPPORT_MESSAGE", "EXPOSED"),
            "search_6083": ("SEARCH_INDEX_ENTRY", "PRESENT"),
            "audit_record_6793": ("SECURITY_AUDIT_LOG", "PRESENT"),
            "account_9102": ("USER_PROFILE", "ABSENT"),
        })

    def test_case_20(self):
        self.check("case_20", "evidence_94", {
            "search_4262": ("SEARCH_INDEX_ENTRY", "PRESENT"),
            "data_flow_1770": ("DATA_FLOW", "FLOW_ACTIVE"),
        })

    def test_decoy_replica_copies_are_not_secondary_identifiers(self):
        by_id = {s.resource_id: s for s in result("evidence_61").sightings}
        self.assertEqual(by_id["support_4104"].category, "", "no missing marker, so no D-022 category")


class OddLines(unittest.TestCase):

    def read_text(self, *lines):
        return log_events.read("evidence_x", "x.log", "\n".join(lines), parse_time("2026-09-02T00:00:00Z"))

    def test_disagreeing_connector_flags_count_as_active_with_a_note(self):
        r = self.read_text('2026-09-01T00:00:00Z [WARN] [integration_service] [tr-1] connector_heartbeat: '
                           'resource_id="data_flow_1" enabled="false" scheduler_registered="true"')
        self.assertEqual(r.sightings[0].state, "FLOW_ACTIVE")
        self.assertTrue(any("D-023" in n.message for n in r.notes))

    def test_dropped_export_events_are_noted(self):
        r = self.read_text('2026-09-01T00:00:00Z [DEBUG] [log_router] [c-1] export_started: instance="c-1" expected_source_events="10"',
                           '2026-09-01T00:00:01Z [INFO] [log_router] [c-1] export_completed: instance="c-1" source_events="8"')
        self.assertTrue(any("dropped" in n.message for n in r.notes))

    def test_unparseable_line_is_noted(self):
        r = self.read_text("this is not a log line")
        self.assertIn("not understood", r.notes[0].message)

    def test_future_dated_line_kept_for_linking_only(self):
        r = self.read_text('2026-09-03T00:00:00Z level=INFO service=account_service event=profile_lookup_completed '
                           'resource_id=account_1 http_status=200 rows=1 correlation_key=user_1')
        self.assertTrue(r.sightings[0].future_dated)
        self.assertTrue(any("D-019" in n.message for n in r.notes))


if __name__ == "__main__":
    unittest.main()
