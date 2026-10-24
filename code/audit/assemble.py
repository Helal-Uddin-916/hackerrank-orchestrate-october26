"""Find a person's records among all sightings and their state at audit_at (DESIGN.md part 5).

1. Linking: a record belongs to the person if ANY sighting of it, in any file at any time, carries
   one of the person's fingerprints or their user_id. Files saved after audit_at and future-dated
   rows may help here, but only here (D-005, D-017, D-019). Log trace links are already inside the
   sightings (D-020).
2. Eligible evidence: sightings from files saved at or before audit_at, observed at or before
   audit_at, and not future-dated (D-017, D-019).
3. Copies: replica stores (names containing "replica") are separate copies; every other store of a
   system is its one live copy, so names given by different readers do not split it.
4. Absence: a linked record also counts as ABSENT in a copy when an eligible coverage entry for that
   copy comes from a file that does not show the record (D-008), and only if that file is about this
   person (some sighting in it links to them). Snapshot files are extracts about specific people, not
   complete copies of a system (D-027).
5. Per copy the latest eligible observation wins; observations at the same moment that disagree
   resolve to the less safe state with a warning (D-009). Ties in citation go to the lowest file
   number (D-011).
6. Per record the least safe copy decides, so a row left only in a replica keeps the record present;
   among copies in that state the most recent proof is cited.
7. Only the plain single-table SQL copies (COMPLETE_TABLES) are complete, so only their coverage
   counts for everyone; multi-collection replica dumps, inventories, bundles and record lists are
   extracts about specific people (D-027, D-030).
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from audit.catalog import CONTEXT_CATEGORIES, category_for_prefix

# Least safe first: used to break same-moment conflicts (D-009) and to combine copies.
STATE_RISK = ["RESTORE_ALLOWED", "FLOW_ACTIVE", "PRESENT", "EXPOSED", "UNKNOWN",
              "REDACTED", "ANONYMIZED", "RESTORE_BLOCKED", "FLOW_DISABLED", "ABSENT"]
RISK = {state: rank for rank, state in enumerate(STATE_RISK)}
CATEGORY_SOURCE_RANK = {"label": 0, "section": 1, "prefix": 2, "": 3}
# Plain single-table SQL copies; absence from them proves absence for everyone (D-030).
COMPLETE_TABLES = {"customer_profiles_primary", "customer_profiles_failover_replica", "search_documents_primary"}


def copy_of(system_id, store):
    return store if "replica" in store else f"{system_id}/live"


def file_number(file_id):
    match = re.search(r"(\d+)$", file_id)
    return int(match.group(1)) if match else 0


@dataclass(frozen=True)
class Observation:
    """One state observation of one copy: a sighting, or an absence derived from coverage."""
    copy: str
    state: str
    observed_at: object
    file_id: str
    extras: dict
    from_coverage: bool = False


@dataclass
class RecordState:
    system_id: str
    resource_id: str
    category: str
    state: Optional[str]                 # None: linked, but no eligible evidence at audit_at
    evidence_id: Optional[str]           # file proving the state
    extras: Dict[str, object] = field(default_factory=dict)
    copies: Dict[str, str] = field(default_factory=dict)   # copy -> state
    categories: Set[str] = field(default_factory=set)      # every kind any sighting gave this record
    warnings: List[str] = field(default_factory=list)


def _eligible(sighting, audit_at):
    return (sighting.file_captured_at <= audit_at and sighting.observed_at <= audit_at
            and not sighting.future_dated)


def _pick(observations):
    """Latest observation wins; same moment: least safe state, then lowest file number."""
    latest = max(o.observed_at for o in observations)
    tied = [o for o in observations if o.observed_at == latest]
    tied.sort(key=lambda o: (RISK[o.state], file_number(o.file_id)))
    conflict = len({o.state for o in tied}) > 1
    return tied[0], conflict


class Assembler:
    """Built once per evidence set; assemble() is called once per request."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.by_resource = defaultdict(list)
        self.resources_by_fp = defaultdict(set)
        self.resources_by_ref = defaultdict(set)
        for s in evidence.sightings:
            key = (s.system_id, s.resource_id)
            self.by_resource[key].append(s)
            for fp in s.identifier_fps:
                self.resources_by_fp[fp].add(key)
            for ref in s.user_refs:
                self.resources_by_ref[ref].add(key)
        self.coverage_by_system = defaultdict(list)
        for c in evidence.coverage:
            self.coverage_by_system[c.system_id].append(c)
        self.files_showing = defaultdict(set)  # (system, resource) -> {(file_id, copy)}
        for s in evidence.sightings:
            self.files_showing[(s.system_id, s.resource_id)].add((s.file_id, copy_of(s.system_id, s.store)))

    def linked_resources(self, person):
        keys = set(self.resources_by_ref.get(person.user_id, set()))
        for fp in person.fingerprints:
            keys |= self.resources_by_fp.get(fp, set())
        return keys

    def assemble(self, person, audit_at, extra_resources=()):
        """Return {(system_id, resource_id): RecordState} for the person's records at audit_at."""
        files_about_person = self.files_about(person)
        records = {}
        for key in sorted(self.linked_resources(person) | set(extra_resources)):
            records[key] = self._record_state(key, audit_at, files_about_person)
        self._keep_latest_lookup_only_account(person, records)
        return records

    def _keep_latest_lookup_only_account(self, person, records):
        """D-028: several accounts known only from "not found" lookups of the person: keep the latest."""
        accounts = [k for k in records if k[0] == "account_service"]
        if len(accounts) < 2:
            return
        latest = {}
        for key in accounts:
            mine = [s for s in self.by_resource[key]
                    if person.user_id in s.user_refs or s.identifier_fps & person.fingerprints]
            if not mine or any(s.state != "ABSENT" for s in mine):
                return  # an account seen with real content, or linked another way: keep them all
            latest[key] = max(s.observed_at for s in mine)
        keep = max(accounts, key=lambda k: (latest[k], k[1]))
        for key in accounts:
            if key != keep:
                del records[key]
                records[keep].warnings.append(f"{key[1]} dropped: linked only by an older not-found lookup (D-028)")

    def files_about(self, person):
        """Files with at least one sighting that links to the person."""
        files = set()
        for key in self.linked_resources(person):
            for s in self.by_resource[key]:
                if person.user_id in s.user_refs or s.identifier_fps & person.fingerprints:
                    files.add(s.file_id)
        return files

    def _record_state(self, key, audit_at, files_about_person=frozenset()):
        system_id, resource_id = key
        sightings = self.by_resource.get(key, [])
        seen = {s.category for s in sightings if s.category}
        context = [c for c in CONTEXT_CATEGORIES if c in seen]
        ranked = sorted((CATEGORY_SOURCE_RANK[s.category_source], s.category) for s in sightings if s.category)
        # Context clues (D-021, D-022, attachments) beat the plain kind; then label > section > prefix.
        category = context[0] if context else (ranked[0][1] if ranked else category_for_prefix(resource_id))

        observations = defaultdict(list)
        for s in sightings:
            if _eligible(s, audit_at):
                observations[copy_of(s.system_id, s.store)].append(
                    Observation(copy_of(s.system_id, s.store), s.state, s.observed_at, s.file_id, dict(s.extras)))
        shown = self.files_showing.get(key, set())
        for c in self.coverage_by_system.get(system_id, []):
            if c.file_captured_at > audit_at or c.checked_at > audit_at:
                continue
            complete_copy = c.store.split("/", 1)[-1] in COMPLETE_TABLES
            if not complete_copy and c.file_id not in files_about_person:
                continue
            copy = copy_of(c.system_id, c.store)
            if (c.file_id, copy) not in shown:
                observations[copy].append(Observation(copy, "ABSENT", c.checked_at, c.file_id, {}, from_coverage=True))

        record = RecordState(system_id, resource_id, category, None, None, categories=seen)
        if not observations:
            record.warnings.append("linked record has no evidence at or before audit_at")
            return record
        winners = {}
        for copy, items in observations.items():
            winner, conflict = _pick(items)
            winners[copy] = winner
            record.copies[copy] = winner.state
            if conflict:
                record.warnings.append(f"sightings disagree at the same moment in {copy}; took {winner.state} (D-009)")
        # Least safe copy decides; among copies in that state the most recent proof is cited (D-011 tie-break last).
        worst = sorted(winners.values(), key=lambda o: (RISK[o.state], -o.observed_at.timestamp(), file_number(o.file_id)))[0]
        record.state, record.evidence_id = worst.state, worst.file_id
        for o in sorted(winners.values(), key=lambda o: o.observed_at):
            record.extras.update(o.extras)
        return record
