"""Link records that carry no identifier through cleanup-job timing (D-028, making D-005 concrete).

Some records never show a name, email or user reference (often accounts and anonymized analytics).
The only trace of their owner is a deletion_worker activity row timed around the owner's request
report. A record is linked to a request only when exactly one request fits:
- the row falls between WINDOW_BEFORE before and WINDOW_AFTER after the request's reported_at;
- the record is not already linked to anyone through evidence;
- requests whose person already has a directly linked record of the same kind are ruled out.
- if several requests still fit, a file showing the record that is about exactly one of their
  people decides (D-035): a real piece of evidence, never an ordering guess.
When several requests still fit, nothing is linked and each fitting request gets a warning.
"""
import csv
import os
from collections import defaultdict
from datetime import timedelta

from audit.catalog import PREFIXES, prefix_of as prefix
from audit.sighting import parse_time

WINDOW_BEFORE = timedelta(minutes=90)
WINDOW_AFTER = timedelta(minutes=10)
def load_requests(dataset_dir):
    """All requests in the dataset folder: the ones to answer and, if present, the samples."""
    requests = []
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        path = os.path.join(dataset_dir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                requests.extend(csv.DictReader(f))
    return requests


class ActivityLinker:

    def __init__(self, assembler, identity_index, requests, dataset_dir):
        self.requests = {r["case_id"]: r for r in requests}
        self.reported_at = {c: parse_time(r["reported_at"]) for c, r in self.requests.items()}
        directly, self.files_about = {}, {}
        for case_id, request in self.requests.items():
            person = identity_index.resolve(request["email"])
            directly[case_id] = assembler.linked_resources(person) if person.found else set()
            self.files_about[case_id] = assembler.files_about(person) if person.found else set()
        self.files_showing = {key: {s.file_id for s in sightings} for key, sightings in assembler.by_resource.items()}
        self.linked_anywhere = set().union(*directly.values()) if directly else set()
        self.kinds_linked = {c: {prefix(rid) for _, rid in keys} for c, keys in directly.items()}

        self.rows_by_resource = defaultdict(list)
        with open(os.path.join(dataset_dir, "system_activity.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["source_system"] != "deletion_worker" or prefix(row["resource_id"]) not in PREFIXES:
                    continue
                key = (row["target_system"], row["resource_id"])
                if key not in self.linked_anywhere:
                    self.rows_by_resource[key].append(parse_time(row["event_time"]))

    def candidates(self, key):
        """Requests whose report window holds a cleanup row on this record and that lack this kind of record."""
        kind = prefix(key[1])
        fitting = set()
        for at in self.rows_by_resource.get(key, []):
            for case_id, reported in self.reported_at.items():
                if reported - WINDOW_BEFORE <= at <= reported + WINDOW_AFTER and kind not in self.kinds_linked[case_id]:
                    fitting.add(case_id)
        if len(fitting) > 1:  # D-035: a file showing the record that is about exactly one candidate
            shown_in = self.files_showing.get(key, set())
            about = {c for c in fitting if shown_in & self.files_about[c]}
            if len(about) == 1:
                return about
        return fitting

    def link(self, case_id):
        """Return (records linked to this request, warnings about records that also fit other requests)."""
        linked, warnings = set(), []
        reported = self.reported_at.get(case_id)
        if reported is None:
            return linked, warnings
        for key, times in sorted(self.rows_by_resource.items()):
            if not any(reported - WINDOW_BEFORE <= at <= reported + WINDOW_AFTER for at in times):
                continue
            fitting = self.candidates(key)
            if fitting == {case_id}:
                linked.add(key)
            elif case_id in fitting:
                others = sorted(fitting - {case_id})
                warnings.append(f"{key[1]} has no identifier and may belong to this request, but also fits "
                                f"{', '.join(others)}; not linked (D-028)")
        return linked, warnings
