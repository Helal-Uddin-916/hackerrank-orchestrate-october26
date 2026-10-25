"""Tests for code/audit/readers/sql_snapshot.py on all 24 real SQL files and our hard cases.

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
from audit.readers import sql_snapshot  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

REPO_ROOT = os.path.dirname(CODE_DIR)
DATASET = os.path.join(REPO_ROOT, "dataset")

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def manifest(dataset_dir):
    with open(os.path.join(dataset_dir, "evidence_files.csv"), encoding="utf-8", newline="") as f:
        return {r["file_id"]: r for r in csv.DictReader(f)}


def read(dataset_dir, file_id):
    entry = manifest(dataset_dir)[file_id]
    path = os.path.join(dataset_dir, entry["file_path"])
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, sql_snapshot.read(file_id, path, text, parse_time(entry["captured_at"]))


def stores_of(result, resource_id):
    return {s.store for s in result.sightings if s.resource_id == resource_id}


class AllRealSqlFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = {fid: read(DATASET, fid) for fid, e in manifest(DATASET).items() if e["file_path"].endswith(".sql")}

    def test_all_24_files_load_with_no_notes(self):
        self.assertEqual(len(self.results), 24)
        for file_id, (_, result) in self.results.items():
            self.assertEqual(result.notes, [], file_id)

    def test_one_sighting_per_data_row(self):
        for file_id, (text, result) in self.results.items():
            db = sql_snapshot.load(text)
            tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if t != "snapshot_scope"]
            rows = sum(db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
            self.assertEqual(len(result.sightings), rows, file_id)

    def test_sighting_time_is_the_snapshot_time_and_never_future(self):
        for file_id, (_, result) in self.results.items():
            for s in result.sightings:
                self.assertFalse(s.future_dated, file_id)
                self.assertLessEqual(s.observed_at, s.file_captured_at, file_id)


class Inventories(unittest.TestCase):

    def test_out_of_scope_systems_get_no_coverage(self):
        # customer_data_inventory_20260905.sql covers only account_service; its other tables are empty.
        _, result = read(DATASET, "evidence_90")
        self.assertEqual({c.system_id for c in result.coverage}, {"account_service"})
        self.assertEqual(result.sightings, [])

    def test_case_09_restorable_backup(self):
        # Sample case_09 reports REPORT_RESTORE_RISK for backup_4978 citing evidence_93.
        _, result = read(DATASET, "evidence_93")
        by_id = {s.resource_id: s for s in result.sightings}
        self.assertEqual(by_id["backup_4978"].state, "RESTORE_ALLOWED")
        self.assertEqual(by_id["backup_4978"].extras, {"restore_allowed": True})
        self.assertEqual(by_id["backup_3933"].state, "RESTORE_BLOCKED")
        self.assertEqual(len(result.coverage), 8)
        security = [s for s in result.sightings if s.category == "SECURITY_AUDIT_LOG"]
        self.assertTrue(all("retention_until" in s.extras for s in security))


class Replicas(unittest.TestCase):

    def test_case_16_account_only_in_read_replica(self):
        # Sample case_16 reports account_994 as remaining, citing the read replica file evidence_05.
        _, result = read(DATASET, "evidence_05")
        self.assertEqual(stores_of(result, "account_994"), {"account_service/customer_data_read_replica"})
        self.assertEqual(stores_of(result, "search_1007"),
                         {"search_service/customer_data_primary", "search_service/customer_data_read_replica"})
        covered = {c.store for c in result.coverage if c.system_id == "account_service"}
        self.assertEqual(covered, {"account_service/customer_data_primary", "account_service/customer_data_read_replica"})

    def test_case_05_account_only_in_failover_copy_and_case_01_absent_from_both(self):
        _, result = read(DATASET, "evidence_54")
        self.assertEqual(stores_of(result, "account_005"), {"account_service/customer_profiles_failover_replica"})
        self.assertEqual(stores_of(result, "account_001"), set(), "case_01's account is in neither table")
        self.assertEqual({c.store for c in result.coverage},
                         {"account_service/customer_profiles_primary", "account_service/customer_profiles_failover_replica"})

    def test_case_06_search_primary(self):
        _, result = read(DATASET, "evidence_57")
        s = next(s for s in result.sightings if s.resource_id == "search_001")
        self.assertEqual((s.state, s.system_id, s.store), ("PRESENT", "search_service", "search_service/search_documents_primary"))
        self.assertIn("email", s.pii_fields_present)


class HardCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cc.build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_case_908_replica_only_row(self):
        _, result = read(self.tmp.name, "evidence_908")
        self.assertEqual(result.notes, [])
        self.assertEqual(stores_of(result, "account_908"), {"account_service/customer_data_read_replica"})

    def test_case_909_inventory_covers_only_accounts(self):
        _, result = read(self.tmp.name, "evidence_910")
        self.assertEqual({c.system_id for c in result.coverage}, {"account_service"})


class UnsafeSql(unittest.TestCase):

    def refused(self, text):
        result = sql_snapshot.read("evidence_x", "x.sql", text, parse_time("2026-09-01T00:00:00Z"))
        self.assertEqual((result.sightings, result.coverage), ([], []))
        self.assertIn("file refused", result.notes[0].message)

    def test_drop_table_is_refused(self):
        self.refused("CREATE TABLE t (a TEXT);\nDROP TABLE t;\n")

    def test_attach_database_is_refused(self):
        self.refused("ATTACH DATABASE '/tmp/x.db' AS x;\n")

    def test_other_pragma_is_refused(self):
        self.refused("PRAGMA writable_schema=ON;\n")

    def test_unfinished_statement_is_refused(self):
        self.refused("CREATE TABLE t (a TEXT);\nINSERT INTO t VALUES ('x'\n")


if __name__ == "__main__":
    unittest.main()
