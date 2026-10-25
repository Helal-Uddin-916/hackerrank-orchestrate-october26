"""Find the person behind a request and every identifier they ever had (DESIGN.md part 4).

A request email may be current or historical. Emails are compared after trimming and lowercasing.
The person's identifiers are their current email, phone and email hash plus every old and new
value in user_audit_log.csv, all as fingerprints (audit.sighting), and their stable user_id.
All history is included, also changes after audit_at: a newer identifier cannot appear in older
evidence, and no email in the dataset ever belonged to two people.
No match or more than one match gives a warning instead of a guess.
"""
import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional

from audit.sighting import fingerprint_email, fingerprint_phone, normalize_email


@dataclass(frozen=True)
class Person:
    user_id: Optional[str]
    fingerprints: FrozenSet[str] = frozenset()
    matched_by: str = ""  # "current email", "historical email" or ""
    warnings: List[str] = field(default_factory=list)

    @property
    def found(self):
        return self.user_id is not None


def _fingerprint(field_name, value):
    value = (value or "").strip()
    if not value:
        return None
    if field_name == "email":
        return fingerprint_email(value)
    if field_name == "phone":
        return fingerprint_phone(value)
    if field_name == "email_hash":
        return value.lower()
    return None


class IdentityIndex:
    """Lookup from email to user_id and from user_id to all fingerprints, built once per dataset."""

    def __init__(self, dataset_dir):
        self.current_email = defaultdict(set)
        self.historical_email = defaultdict(set)
        self.fingerprints = defaultdict(set)
        with open(os.path.join(dataset_dir, "user_identities.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                uid = row["user_id"]
                self.current_email[normalize_email(row["email"])].add(uid)
                for name in ("email", "phone", "email_hash"):
                    fp = _fingerprint(name, row[name])
                    if fp:
                        self.fingerprints[uid].add(fp)
        with open(os.path.join(dataset_dir, "user_audit_log.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                uid, name = row["user_id"], row["field_name"]
                for value in (row["old_value"], row["new_value"]):
                    if name == "email" and value.strip():
                        self.historical_email[normalize_email(value)].add(uid)
                    fp = _fingerprint(name, value)
                    if fp:
                        self.fingerprints[uid].add(fp)

    def resolve(self, request_email):
        email = normalize_email(request_email or "")
        current = self.current_email.get(email, set())
        historical = self.historical_email.get(email, set())
        candidates = current or historical
        if not candidates:
            return Person(None, warnings=["no person matches the request email"])
        if len(candidates) > 1:
            return Person(None, warnings=[f"request email matches {len(candidates)} people; not guessing"])
        uid = next(iter(candidates))
        matched_by = "current email" if current else "historical email"
        return Person(uid, frozenset(self.fingerprints[uid]), matched_by)
