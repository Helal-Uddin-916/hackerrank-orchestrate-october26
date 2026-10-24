"""Reader for service resource exports (evidence/records/service_resource_export_*.json).

Shape: {"exported_at", "region", "source", "service_exports": {service: {section: [record, ...]}}}.
Every record carries its own observed_at. The section name tells the record kind (D-016).

State rules (checked against all 11 real export files and sample case_27):
- account profile lookup: 404 / null profile -> ABSENT; 200 with a profile -> PRESENT.
- search query: each hit document -> PRESENT; a query with no hits -> ABSENT.
- analytics profile / support ticket: any email, phone, name or hash left -> EXPOSED, otherwise
  ANONYMIZED (analytics) or REDACTED (support). A customer_reference (stable user_id) alone is
  allowed, as in sample case_27 support_4707 (VERIFY_RECORD_REDACTED).
- backup restore entry: restore_allowed -> RESTORE_ALLOWED / RESTORE_BLOCKED.
- connector: enabled -> FLOW_ACTIVE / FLOW_DISABLED.
- export job: PRESENT. Security audit record: PRESENT, with retention_until kept as an extra.
"""
import json

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated
from audit.sighting import Sighting, SightingError, fingerprint_email, fingerprint_phone, parse_time

NAME = "service_export_json"

# section -> data category (D-016, category_source "section")
SECTIONS = {
    ("account_service", "profile_lookups"): "USER_PROFILE",
    ("analytics_platform", "profiles"): "ANALYTICS_PROFILE",
    ("backup_service", "restore_entries"): "BACKUP_ARCHIVE",
    ("export_service", "exports"): "USER_EXPORT",
    ("integration_service", "connectors"): "DATA_FLOW",
    ("search_service", "index_queries"): "SEARCH_INDEX_ENTRY",
    ("security_service", "audit_records"): "SECURITY_AUDIT_LOG",
    ("support_platform", "tickets"): "SUPPORT_MESSAGE",
}
# record field -> which personal-data kind it holds
EMAIL_FIELDS = ("email", "customer_email", "owner_email", "requester_email")
PHONE_FIELDS = ("phone", "owner_phone", "requester_phone")
NAME_FIELDS = ("full_name", "display_name", "requester_name")
REF_FIELDS = ("customer_reference", "subject_reference")


def can_read(path, text):
    if not text.lstrip().startswith("{"):
        return False
    try:
        return isinstance(json.loads(text).get("service_exports"), dict)
    except ValueError:
        return False


def _who(record):
    """Return (fingerprints, user_refs, pii_fields_present) for one record's identifier fields."""
    fps, refs, pii = set(), set(), set()
    for f in EMAIL_FIELDS:
        if record.get(f):
            fps.add(fingerprint_email(record[f]))
            pii.add("email")
    for f in PHONE_FIELDS:
        if record.get(f):
            fps.add(fingerprint_phone(record[f]))
            pii.add("phone")
    if record.get("email_hash"):
        fps.add(record["email_hash"].lower())
        pii.add("email_hash")
    if any(record.get(f) for f in NAME_FIELDS):
        pii.add("name")
    for f in REF_FIELDS:
        if record.get(f):
            refs.add(record[f])
    return fps, refs, pii


def _records(service, section, record):
    """Yield (resource_id, state, raw_state, identifier record, extras) for one export entry."""
    if section == "profile_lookups":
        profile = record.get("profile")
        if record.get("status_code") == 200 and profile:
            yield record["resource_id"], "PRESENT", "200", profile, {}
        else:
            yield record["resource_id"], "ABSENT", str(record.get("status_code")), {}, {}
    elif section == "index_queries":
        docs = (record.get("hits") or {}).get("documents") or []
        if not docs:
            # The query text is the lookup key (an email); keep it only as a link.
            yield record["resource_id"], "ABSENT", "0 hits", {"email": record.get("query")}, {}
        for doc in docs:
            yield doc["resource_id"], "PRESENT", "hit", doc, {}
    elif section == "profiles":
        fps, _, pii = _who(record)
        yield record["resource_id"], ("EXPOSED" if pii else "ANONYMIZED"), \
            ("identifiers present" if pii else "no identifiers"), record, {}
    elif section == "tickets":
        fps, _, pii = _who(record)
        yield record["resource_id"], ("EXPOSED" if pii else "REDACTED"), \
            ("identifiers present" if pii else "no identifiers"), record, {}
    elif section == "restore_entries":
        allowed = bool(record.get("restore_allowed"))
        yield record["resource_id"], ("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), \
            f"restore_allowed: {str(allowed).lower()}", record, {"restore_allowed": allowed}
    elif section == "connectors":
        enabled = bool(record.get("enabled"))
        yield record["resource_id"], ("FLOW_ACTIVE" if enabled else "FLOW_DISABLED"), \
            f"enabled: {str(enabled).lower()}", record, {"enabled": enabled}
    elif section == "audit_records":
        extras = {"retention_until": record["retention_until"]} if record.get("retention_until") else {}
        yield record["resource_id"], "PRESENT", "listed", record, extras
    elif section == "exports":
        yield record["resource_id"], "PRESENT", "listed", record, {}


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    data = json.loads(text)
    for service, sections in sorted(data["service_exports"].items()):
        for section, entries in sorted(sections.items()):
            where = f"service_exports/{service}/{section}"
            category = SECTIONS.get((service, section))
            if category is None:
                result.notes.append(ReaderNote(file_id, where, "unknown service section; ignored"))
                continue
            for index, entry in enumerate(entries):
                location = f"{where}[{index}]"
                try:
                    observed_at = parse_time(entry["observed_at"])
                    future = is_future_dated(observed_at, file_captured_at)
                    if future:
                        result.notes.append(ReaderNote(file_id, location, FUTURE_DATED_NOTE))
                    for resource_id, state, raw_state, who_record, extras in _records(service, section, entry):
                        fps, refs, pii = _who(who_record)
                        if state == "ABSENT":
                            pii = set()  # a lookup key is a link, not data still held
                        result.sightings.append(Sighting(
                            file_id=file_id, location=location, reader=NAME,
                            observed_at=observed_at, time_source="observation",
                            file_captured_at=file_captured_at, future_dated=future,
                            system_id=service, store=service, resource_id=resource_id,
                            state=state, raw_state=raw_state,
                            category=category, category_source="section",
                            identifier_fps=frozenset(fps), user_refs=frozenset(refs),
                            pii_fields_present=frozenset(pii), extras=extras,
                        ))
                except (KeyError, TypeError, SightingError, ValueError) as error:
                    result.notes.append(ReaderNote(file_id, location, f"entry rejected: {type(error).__name__}"))
    return result
