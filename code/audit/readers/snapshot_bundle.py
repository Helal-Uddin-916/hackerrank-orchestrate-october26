"""Reader for snapshot bundles (evidence/records/*snapshot_bundle*.json and similar).

Shape: {"exported_at", "source", "snapshots": [{"captured_at", "data_store", "collection",
"records": [...]}, ...]}. Each snapshot has its own captured_at, which can be older than the
bundle's export (the file's saved time decides eligibility, D-017).

Every snapshot also produces Coverage: it checked that collection of that system at that time.
An empty snapshot therefore proves the collection held nothing then (D-008; sample case_32).

Collections and their rules (checked against all 12 real bundle files):
- customer_profiles: PRESENT. deleted_customer_references (tombstones): EXPOSED if any
  name/email/phone/hash is left, else REDACTED.
- customer_analytics_reports: EXPOSED if email/phone/hash left, else ANONYMIZED.
- support_conversations: EXPOSED if message_text still contains an email or phone, REDACTED if it
  is "[REDACTED]". ticket_attachments: PRESENT, owner kept as a fingerprint (D-014).
- customer_search_documents, customer_export_jobs, security_audit_events: PRESENT.
- retained_order_records: legal-hold record, PRESENT, hold end date kept as an extra.
- backup_catalog: restore_allowed. connector_configurations: enabled.
- operational_record_history: one row per lookup, with its own system, record_type, result
  and recorded_at, read like a handover row.
A user_<n> reference alone is allowed and only links the record (D-018).
"""
import json
import re

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated, state_from_word
from audit.sighting import Coverage, Sighting, SightingError, fingerprint_email, fingerprint_phone, parse_time

NAME = "snapshot_bundle_json"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
HASH = re.compile(r"^[0-9a-f]{64}$")
USER_REF = re.compile(r"^user_\d+$")

COLLECTIONS = {
    "customer_profiles": "USER_PROFILE",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "customer_export_jobs": "USER_EXPORT",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD",
    "backup_catalog": "BACKUP_ARCHIVE",
    "connector_configurations": "DATA_FLOW",
    "operational_record_history": None,  # each row carries its own record_type
}


def can_read(path, text):
    if not text.lstrip().startswith("{"):
        return False
    try:
        return isinstance(json.loads(text).get("snapshots"), list)
    except ValueError:
        return False


class Who:
    """Collects fingerprints, user references and personal-data kinds for one record."""

    def __init__(self):
        self.fps, self.refs, self.pii = set(), set(), set()

    def identifier(self, value):
        """A field holding one identifier: email, phone, hash or user reference."""
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip()
        if value.startswith("fp:"):  # an identifier already turned into a fingerprint (D-034)
            kind, _, fp = value[3:].partition(":")
            if kind in ("email", "phone", "email_hash") and HASH.match(fp):
                self.fps.add(fp)
                self.pii.add(kind)
            elif kind == "name":
                self.pii.add("name")
            return
        if USER_REF.match(value):
            self.refs.add(value)
        elif HASH.match(value):
            self.fps.add(value)
            self.pii.add("email_hash")
        elif EMAIL.fullmatch(value):
            self.fps.add(fingerprint_email(value))
            self.pii.add("email")
        elif PHONE.fullmatch(value):
            self.fps.add(fingerprint_phone(value))
            self.pii.add("phone")

    def name(self, value):
        if isinstance(value, str) and value.strip():
            self.pii.add("name")

    def free_text(self, value):
        """Free text such as a support message: any email or phone inside still exposes the person."""
        if not isinstance(value, str):
            return
        for email in EMAIL.findall(value):
            self.fps.add(fingerprint_email(email))
            self.pii.add("email")
        if PHONE.search(value):
            self.pii.add("phone")


def read_record(collection, record, who):
    """Return (state, raw_state, extras, system_override, category_override, observed_override)."""
    if collection == "customer_profiles":
        for f in ("email", "phone", "customer_reference"):
            who.identifier(record.get(f))
        who.name(record.get("full_name"))
        return "PRESENT", record.get("account_status") or "listed", {}, None, None, None
    if collection == "deleted_customer_references":
        for f in ("email", "phone", "email_hash", "subject_reference"):
            who.identifier(record.get(f))
        who.name(record.get("name"))
        return ("EXPOSED" if who.pii else "REDACTED"), ("identifiers present" if who.pii else "no identifiers"), \
            {}, None, None, None
    if collection == "customer_analytics_reports":
        for f in ("email", "phone", "email_hash", "user_id"):
            who.identifier(record.get(f))
        return ("EXPOSED" if who.pii else "ANONYMIZED"), ("identifiers present" if who.pii else "no identifiers"), \
            {}, None, None, None
    if collection == "support_conversations":
        who.identifier(record.get("subject_reference"))
        text = record.get("message_text")
        who.free_text(text)
        state = "EXPOSED" if who.pii else ("REDACTED" if text == "[REDACTED]" else "PRESENT")
        return state, ("[REDACTED]" if text == "[REDACTED]" else "message text"), {}, None, None, None
    if collection == "ticket_attachments":
        who.identifier(record.get("uploaded_by"))
        extras = {"attachment_owners": tuple(sorted(who.fps))} if who.fps else {}
        return "PRESENT", "listed", extras, None, None, None
    if collection == "customer_search_documents":
        doc = record.get("document") or {}
        for f in ("email", "phone", "email_hash", "customer_reference"):
            who.identifier(doc.get(f))
        who.name(doc.get("display_name"))
        return "PRESENT", "listed", {}, None, None, None
    if collection == "customer_export_jobs":
        who.identifier(record.get("owner_email"))
        return "PRESENT", "listed", {}, None, None, None
    if collection == "security_audit_events":
        who.identifier(record.get("subject_reference"))
        return "PRESENT", "listed", {}, None, None, None
    if collection == "retained_order_records":
        who.identifier(record.get("subject_reference"))
        extras = {"legal_hold": True}
        if record.get("retained_until"):
            extras["hold_until"] = record["retained_until"]
        return "PRESENT", "listed", extras, None, None, None
    if collection == "backup_catalog":
        who.identifier(record.get("subject_reference"))
        allowed = bool(record.get("restore_allowed"))
        return ("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), f"restore_allowed: {str(allowed).lower()}", \
            {"restore_allowed": allowed}, None, None, None
    if collection == "connector_configurations":
        who.identifier(record.get("subject_selector"))
        enabled = bool(record.get("enabled"))
        return ("FLOW_ACTIVE" if enabled else "FLOW_DISABLED"), f"enabled: {str(enabled).lower()}", \
            {"enabled": enabled}, None, None, None
    if collection == "operational_record_history":
        # bundles call the identifier lookup_value; ledgers call it subject_reference
        who.identifier(record.get("lookup_value") or record.get("subject_reference"))
        category, word = record["record_type"], record["result"]
        return state_from_word(category, word), word, {}, record["system"], category, parse_time(record["recorded_at"])
    raise KeyError(collection)


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    data = json.loads(text)
    for s_index, snapshot in enumerate(data["snapshots"]):
        where = f"snapshots[{s_index}]"
        store, collection = snapshot.get("data_store"), snapshot.get("collection")
        if collection not in COLLECTIONS:
            result.notes.append(ReaderNote(file_id, where, "unknown collection; ignored"))
            continue
        try:
            captured_at = parse_time(snapshot["captured_at"])
        except (KeyError, TypeError, ValueError) as error:
            result.notes.append(ReaderNote(file_id, where, f"snapshot rejected: {type(error).__name__}"))
            continue
        if is_future_dated(captured_at, file_captured_at):
            result.notes.append(ReaderNote(file_id, where, "snapshot " + FUTURE_DATED_NOTE + "; no coverage"))
        elif collection != "operational_record_history":
            result.coverage.append(Coverage(file_id, store, f"{store}/{collection}", captured_at, file_captured_at))

        add_records(result, NAME, file_id, file_captured_at, where, store, collection,
                    snapshot.get("records") or [], captured_at)
    return result


def add_records(result, reader, file_id, file_captured_at, where, store, collection, records, captured_at,
                copy_name=None):
    """Turn the records of one collection into sightings on result (shared with record_list, sql_snapshot).

    copy_name names the exact copy (e.g. a replica table); by default it is <store>/<collection>.
    """
    for r_index, record in enumerate(records):
        location = f"{where}.records[{r_index}]" if where else f"records[{r_index}]"
        if copy_name and len(records) == 1:
            location = where
        who = Who()
        try:
            state, raw_state, extras, system, category, observed = read_record(collection, record, who)
            category = category or COLLECTIONS[collection]
            source = "label" if collection == "operational_record_history" else "section"
            if state is None:
                result.notes.append(ReaderNote(file_id, location, f"unknown state word {raw_state!r}; recorded as UNKNOWN"))
                state = "UNKNOWN"
            pii = set() if state == "ABSENT" else who.pii
            observed_at = observed or captured_at
            future = is_future_dated(observed_at, file_captured_at)
            if future and observed:  # a whole future snapshot is noted once, above
                result.notes.append(ReaderNote(file_id, location, FUTURE_DATED_NOTE))
            result.sightings.append(Sighting(
                file_id=file_id, location=location, reader=reader,
                observed_at=observed_at, time_source="observation",
                file_captured_at=file_captured_at, future_dated=future,
                system_id=system or store, store=system or copy_name or f"{store}/{collection}",
                resource_id=record["resource_id"], state=state, raw_state=raw_state,
                category=category, category_source=source,
                identifier_fps=frozenset(who.fps), user_refs=frozenset(who.refs),
                pii_fields_present=frozenset(pii), extras=extras,
            ))
        except (KeyError, TypeError, SightingError, ValueError) as error:
            result.notes.append(ReaderNote(file_id, location, f"record rejected: {type(error).__name__}"))
