"""Tests for code/audit/identity.py on the real dataset and the hard cases.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import collections
import csv
import importlib.util
import os
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.identity import IdentityIndex  # noqa: E402
from audit.settings import DATASET_DIR  # noqa: E402
from audit.sighting import fingerprint_email  # noqa: E402

_spec = importlib.util.spec_from_file_location("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

INDEX = IdentityIndex(DATASET_DIR)


def requests(name="deletion_requests.csv", dataset_dir=DATASET_DIR):
    with open(os.path.join(dataset_dir, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class RealRequests(unittest.TestCase):

    def test_every_request_finds_exactly_one_person(self):
        people = [INDEX.resolve(r["email"]) for r in requests()]
        self.assertEqual(len(people), 518)
        self.assertTrue(all(p.found and not p.warnings for p in people))

    def test_100_requests_use_a_historical_email(self):
        how = collections.Counter(INDEX.resolve(r["email"]).matched_by for r in requests())
        self.assertEqual(how["historical email"], 100)

    def test_samples_find_their_people(self):
        for r in requests("sample_deletion_requests.csv"):
            self.assertTrue(INDEX.resolve(r["email"]).found, r["case_id"])

    def test_case_11_person_and_fingerprints(self):
        r = next(r for r in requests("sample_deletion_requests.csv") if r["case_id"] == "case_11")
        person = INDEX.resolve(r["email"])
        self.assertEqual(person.user_id, "user_32")
        self.assertIn(fingerprint_email(r["email"]), person.fingerprints)
        self.assertGreaterEqual(len(person.fingerprints), 2)  # at least email and phone


class HardCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cc.build(cls.tmp.name)
        cls.index = IdentityIndex(cls.tmp.name)
        cls.reqs = {r["case_id"]: r for r in requests(dataset_dir=cls.tmp.name)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_old_email_finds_the_person_and_their_new_email(self):
        person = self.index.resolve(self.reqs["case_901"]["email"])
        self.assertEqual((person.user_id, person.matched_by), ("user_901", "historical email"))
        self.assertIn(fingerprint_email("river.new@example.com"), person.fingerprints)
        self.assertIn(fingerprint_email("river.old@example.com"), person.fingerprints)

    def test_messy_email_is_cleaned(self):
        self.assertEqual(self.index.resolve(self.reqs["case_902"]["email"]).user_id, "user_921")

    def test_two_requests_same_person(self):
        self.assertEqual(self.index.resolve(self.reqs["case_904"]["email"]).user_id,
                         self.index.resolve(self.reqs["case_910"]["email"]).user_id)

    def test_decoy_is_a_different_person(self):
        self.assertNotEqual(self.index.resolve("decoy@example.com").user_id,
                            self.index.resolve(self.reqs["case_901"]["email"]).user_id)


class Warnings(unittest.TestCase):

    def test_unknown_email_warns(self):
        person = INDEX.resolve("nobody@example.invalid")
        self.assertFalse(person.found)
        self.assertIn("no person", person.warnings[0])

    def test_email_shared_by_two_people_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "user_identities.csv"), "w", encoding="utf-8") as f:
                f.write("user_id,name,email,phone,email_hash,updated_at\n"
                        "user_1,A,a@example.com,+1-555-0001,,2026-01-01T00:00:00Z\n"
                        "user_2,B,b@example.com,+1-555-0002,,2026-01-01T00:00:00Z\n")
            with open(os.path.join(tmp, "user_audit_log.csv"), "w", encoding="utf-8") as f:
                f.write("audit_id,user_id,event_time,field_name,old_value,new_value\n"
                        "audit_1,user_2,2026-01-01T00:00:00Z,email,a@example.com,b@example.com\n")
            index = IdentityIndex(tmp)
        # a@example.com is user_1's current email; current matches win over history.
        self.assertEqual(index.resolve("a@example.com").user_id, "user_1")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "user_identities.csv"), "w", encoding="utf-8") as f:
                f.write("user_id,name,email,phone,email_hash,updated_at\n"
                        "user_1,A,a@example.com,,,2026-01-01T00:00:00Z\n"
                        "user_2,B,a@example.com,,,2026-01-01T00:00:00Z\n")
            with open(os.path.join(tmp, "user_audit_log.csv"), "w", encoding="utf-8") as f:
                f.write("audit_id,user_id,event_time,field_name,old_value,new_value\n")
            person = IdentityIndex(tmp).resolve("a@example.com")
        self.assertFalse(person.found)
        self.assertIn("2 people", person.warnings[0])


if __name__ == "__main__":
    unittest.main()
