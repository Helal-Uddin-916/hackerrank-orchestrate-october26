"""Reader for flat record lists in evidence/records/*.json. Two shapes exist:

A. Ledger: {"exported_at", "origin", "record_count", "records": [{"recorded_at", "system",
   "resource_id", "record_type", "result", "subject_reference"}, ...]}
   (connector_configurations_*, customer_export_manifest_*, security_audit_ledger_*).
   File names can mislead: these list lookups across many systems. Each row is read exactly like
   a bundle lookup-history row.
B. Single collection: {"captured_at", "source", "collection", "records": [...]}
   (customer_analytics_report_*, customer_export_jobs_*). Read like one bundle snapshot of that
   collection, including coverage.

Record rules are shared with snapshot_bundle, so both formats always agree.
"""
import json

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated
from audit.readers.snapshot_bundle import COLLECTIONS, add_records
from audit.sighting import Coverage, SightingError, parse_time

NAME = "record_list_json"

# Single-collection files do not name their system; these collections belong to these systems.
COLLECTION_SYSTEMS = {
    "customer_analytics_reports": "analytics_platform",
    "customer_export_jobs": "export_service",
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "support_conversations": "support_platform",
    "ticket_attachments": "support_platform",
    "security_audit_events": "security_service",
    "backup_catalog": "backup_service",
    "connector_configurations": "integration_service",
    "deleted_customer_references": "identity_service",
    "retained_order_records": "legal_service",
}
LEDGER_FIELDS = {"recorded_at", "system", "resource_id", "record_type", "result"}


def _shape(data):
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return None
    if "snapshots" in data or "service_exports" in data:
        return None
    if isinstance(data.get("collection"), str) and "captured_at" in data:
        return "collection"
    if "exported_at" in data and all(LEDGER_FIELDS <= set(r) for r in data["records"]):
        return "ledger"
    return None


def can_read(path, text):
    if not text.lstrip().startswith("{"):
        return False
    try:
        return _shape(json.loads(text)) is not None
    except ValueError:
        return False


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    data = json.loads(text)
    shape = _shape(data)

    if shape == "ledger":
        if data.get("record_count") not in (None, len(data["records"])):
            result.notes.append(ReaderNote(file_id, "record_count", "record_count does not match the records listed"))
        add_records(result, NAME, file_id, file_captured_at, "", data.get("origin", "ledger"),
                    "operational_record_history", data["records"], file_captured_at)
        return result

    collection = data["collection"]
    system = COLLECTION_SYSTEMS.get(collection)
    if collection not in COLLECTIONS or system is None:
        result.notes.append(ReaderNote(file_id, "collection", "unknown collection; ignored"))
        return result
    try:
        captured_at = parse_time(data["captured_at"])
    except (SightingError, ValueError) as error:
        result.notes.append(ReaderNote(file_id, "captured_at", f"file rejected: {type(error).__name__}"))
        return result
    if is_future_dated(captured_at, file_captured_at):
        result.notes.append(ReaderNote(file_id, "captured_at", "collection " + FUTURE_DATED_NOTE + "; no coverage"))
    else:
        result.coverage.append(Coverage(file_id, system, f"{system}/{collection}", captured_at, file_captured_at))
    add_records(result, NAME, file_id, file_captured_at, "", system, collection, data["records"], captured_at)
    return result
