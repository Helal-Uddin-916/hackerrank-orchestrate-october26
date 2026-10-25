"""Reader for log files (evidence/logs/*.log), built on log_lines.parse_line.

Only events that state something about a record become sightings; their meanings were confirmed
against the 8 log-cited samples (docs/PHASE4_LOG_TRACE.md). Every other event is counted in one
note per file, never dropped silently.

Linking: a line's own identifier fields, plus the identifiers of every line with the same
trace_id in the same file (D-020). Identifiers on lookup keys only link; personal data still held
(pii_fields_present) comes only from fields that show record content.

Context rules: an opted-in contact lookup is a MARKETING_PROFILE (D-021); a replica copy whose
deletion marker is missing is a SECONDARY_IDENTIFIER (D-022); a connector is active if enabled or
still scheduled (D-023). Log exports that report dropped events get a note: silence in a log is
never taken as absence (D-008).
"""
import collections

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated, state_from_word
from audit.readers.log_lines import parse_line
from audit.readers.snapshot_bundle import Who
from audit.sighting import Sighting, SightingError

NAME = "log_events"

# Fields that only point at a person (link, not data still held).
LINK_FIELDS = ("correlation_key", "lookup_key", "subject_key", "match_key", "owner_key", "scan_key",
               "subject_ref", "subject_reference", "customer_reference")
# Fields that show record content: a value here is personal data still held by the record.
CONTENT_FIELDS = ("email", "phone", "email_hash", "customer_email", "requester_email", "requester_phone",
                  "owner_email", "owner_phone", "matched_value")
NAME_FIELDS = ("requester_name", "display_name", "full_name")
SERVICE_SYSTEMS = {"support_replica": "support_platform"}


def can_read(path, text):
    return path.endswith(".log")


def _value(fields, key):
    value = fields.get(key)
    return None if value in (None, "", "null", "None") else value


def _true(fields, key):
    return str(fields.get(key, "")).lower() == "true"


def _number(fields, key, default=None):
    try:
        return int(fields.get(key))
    except (TypeError, ValueError):
        return default


def _identifiers(fields, who_link, who_content):
    for key in LINK_FIELDS:
        who_link.identifier(_value(fields, key))
    for key in CONTENT_FIELDS:
        who_content.identifier(_value(fields, key))
    for key in NAME_FIELDS:
        who_content.name(_value(fields, key))


def _interpret(line, trace_marker_missing, notes_for_line):
    """Return (category, state, raw_state, extras, system_override) or None if the event states nothing."""
    f, event = line.fields, line.event
    pii = Who()
    _identifiers(f, Who(), pii)
    exposed = bool(pii.pii)

    if event == "profile_lookup_completed":
        absent = f.get("http_status") == "404" or _number(f, "rows") == 0
        return "USER_PROFILE", ("ABSENT" if absent else "PRESENT"), f"http_status {f.get('http_status')}", {}, None
    if event == "index_query_completed":
        hit = f.get("result") == "hit" or (_number(f, "total_hits", 0) or 0) > 0
        return "SEARCH_INDEX_ENTRY", ("PRESENT" if hit else "ABSENT"), f"result {f.get('result')}", {}, None
    if event == "audit_record_read_completed":
        extras = {"retention_until": f["retention_until"]} if _value(f, "retention_until") else {}
        present = (_number(f, "rows", 1) or 0) > 0
        return "SECURITY_AUDIT_LOG", ("PRESENT" if present else "ABSENT"), f"rows {f.get('rows')}", extras, None
    if event in ("analytics_profile_read_completed", "analytics_profile_read", "analytics_identity_scan"):
        exposed = exposed or (_number(f, "direct_identifiers", 0) or 0) > 0
        return "ANALYTICS_PROFILE", ("EXPOSED" if exposed else "ANONYMIZED"), \
            ("identifiers present" if exposed else "no identifiers"), {}, None
    if event == "contact_lookup_completed":
        if _true(f, "email_opt_in") and (_number(f, "rows", 0) or 0) > 0:
            return "MARKETING_PROFILE", "PRESENT", "email_opt_in true", {}, None  # D-021
        notes_for_line.append("contact lookup without an opt-in; no state recorded")
        return None
    if event == "support_ticket_read_completed":
        return "SUPPORT_MESSAGE", ("EXPOSED" if exposed else "REDACTED"), \
            ("identifiers present" if exposed else "no identifiers"), {}, None
    if event == "message_pii_scan":
        return "SUPPORT_MESSAGE", "EXPOSED", "pii scan match", {}, None
    if event == "message_read":
        text = f.get("message_text", "")
        state = "REDACTED" if text == "[REDACTED]" else ("EXPOSED" if exposed else "PRESENT")
        return "SUPPORT_MESSAGE", state, ("[REDACTED]" if text == "[REDACTED]" else "message text"), {}, None
    if event == "attachment_pii_scan":
        owners = tuple(sorted(pii.fps))
        return "SUPPORT_ATTACHMENT", "PRESENT", "pii scan match", ({"attachment_owners": owners} if owners else {}), None
    if event == "contact_query_completed":
        if (_number(f, "rows", 0) or 0) == 0:
            return "SECONDARY_IDENTIFIER", "ABSENT", "rows 0", {}, None
        if trace_marker_missing:
            return "SECONDARY_IDENTIFIER", "PRESENT", "deletion marker missing", {}, None  # D-022
        return "", "PRESENT", "rows 1", {}, None
    if event == "deletion_marker_check":
        missing = not _true(f, "marker_found")
        return "SECONDARY_IDENTIFIER", ("PRESENT" if missing else "ABSENT"), f"marker_found {f.get('marker_found')}", {}, None
    if event in ("connector_heartbeat", "connector_config_loaded"):
        enabled, scheduled = _true(f, "enabled"), _true(f, "scheduler_registered")
        if enabled != scheduled:
            notes_for_line.append("connector enabled and scheduler_registered disagree; treated as active (D-023)")
        active = enabled or scheduled
        return "DATA_FLOW", ("FLOW_ACTIVE" if active else "FLOW_DISABLED"), \
            f"enabled {f.get('enabled')}, scheduler_registered {f.get('scheduler_registered')}", {"enabled": active}, None
    if event == "connector_config_read_completed":
        enabled = _true(f, "enabled")
        return "DATA_FLOW", ("FLOW_ACTIVE" if enabled else "FLOW_DISABLED"), f"enabled {f.get('enabled')}", \
            {"enabled": enabled}, None
    if event == "restore_candidate_evaluated":
        allowed = _true(f, "eligible") and not _true(f, "deletion_block")
        return "BACKUP_ARCHIVE", ("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), \
            f"eligible {f.get('eligible')}, deletion_block {f.get('deletion_block')}", {"restore_allowed": allowed}, None
    if event == "restore_authorization_completed":
        allowed = f.get("decision") == "allow"
        return "BACKUP_ARCHIVE", ("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), f"decision {f.get('decision')}", \
            {"restore_allowed": allowed}, None
    if event == "restore_policy_read_completed":
        allowed = _true(f, "restore_allowed")
        return "BACKUP_ARCHIVE", ("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), \
            f"restore_allowed {f.get('restore_allowed')}", {"restore_allowed": allowed}, None
    if event in ("export_metadata_read_completed", "export_download_check_completed"):
        available = _true(f, "available")
        return "USER_EXPORT", ("PRESENT" if available else "ABSENT"), f"available {f.get('available')}", {}, None
    if event == "record_lookup":
        category, word = f.get("record_type", ""), f.get("result", "")
        return category, state_from_word(category, word), word, {}, f.get("system")
    return None


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    lines, unparsed = [], 0
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parsed = parse_line(raw)
        if parsed is None:
            unparsed += 1
            result.notes.append(ReaderNote(file_id, f"line {line_no}", "line not understood; ignored"))
            continue
        lines.append((line_no, parsed))

    # D-020: identifiers per trace_id, within this file only.
    trace_links = collections.defaultdict(lambda: (set(), set()))
    marker_missing = set()
    for _, line in lines:
        trace = line.fields.get("trace_id")
        if not trace:
            continue
        link, content = Who(), Who()
        _identifiers(line.fields, link, content)
        fps, refs = trace_links[trace]
        fps |= link.fps | content.fps
        refs |= link.refs | content.refs
        if line.event == "deletion_marker_check" and not _true(line.fields, "marker_found"):
            marker_missing.add(trace)

    ignored = collections.Counter()
    exports = {}
    for line_no, line in lines:
        location = f"line {line_no}"
        f = line.fields
        if line.event == "export_started":
            exports[f.get("instance")] = _number(f, "expected_source_events")
        if line.event == "export_completed":
            expected, delivered = exports.get(f.get("instance")), _number(f, "source_events")
            dropped = (_number(f, "dropped", 0) or 0) + (_number(f, "dropped_auxiliary", 0) or 0)
            if dropped or (expected is not None and delivered is not None and delivered < expected):
                result.notes.append(ReaderNote(file_id, location, "log export reported missing or dropped events"))

        line_notes = []
        trace = f.get("trace_id")
        interpreted = _interpret(line, trace in marker_missing, line_notes) if _value(f, "resource_id") else None
        for message in line_notes:
            result.notes.append(ReaderNote(file_id, location, message))
        if interpreted is None:
            ignored[line.event or "(no event)"] += 1
            continue
        category, state, raw_state, extras, system = interpreted
        if state is None:
            result.notes.append(ReaderNote(file_id, location, f"unknown state word {raw_state!r}; recorded as UNKNOWN"))
            state = "UNKNOWN"

        link, content = Who(), Who()
        _identifiers(f, link, content)
        fps, refs = set(link.fps | content.fps), set(link.refs | content.refs)
        if trace in trace_links:
            fps |= trace_links[trace][0]
            refs |= trace_links[trace][1]
        system_id = system or SERVICE_SYSTEMS.get(line.service, line.service)
        future = is_future_dated(line.time, file_captured_at)
        if future:
            result.notes.append(ReaderNote(file_id, location, FUTURE_DATED_NOTE))
        try:
            result.sightings.append(Sighting(
                file_id=file_id, location=location, reader=NAME,
                observed_at=line.time, time_source="observation", file_captured_at=file_captured_at,
                system_id=system_id, store=system_id, resource_id=f["resource_id"],
                state=state, raw_state=raw_state,
                category=category, category_source=("label" if line.event == "record_lookup" else ("section" if category else "")),
                identifier_fps=frozenset(fps), user_refs=frozenset(refs),
                pii_fields_present=frozenset(set() if state == "ABSENT" else content.pii),
                extras=extras, future_dated=future,
            ))
        except (KeyError, SightingError, ValueError) as error:
            result.notes.append(ReaderNote(file_id, location, f"line rejected: {type(error).__name__}"))

    if ignored:
        summary = ", ".join(f"{event} x{n}" for event, n in sorted(ignored.items()))
        result.notes.append(ReaderNote(file_id, "file", f"{sum(ignored.values())} lines state nothing about a record: {summary}"))
    return result
