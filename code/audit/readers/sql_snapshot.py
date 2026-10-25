"""Reader for SQL snapshot exports (evidence/databases/*.sql).

Safety first: every statement is checked against an allowlist (CREATE TABLE, CREATE INDEX,
INSERT INTO, BEGIN, COMMIT, PRAGMA foreign_keys=ON) before anything runs. If any statement is
not allowed, the whole file is refused with a note. Each file loads into its own in-memory
SQLite database, so files never mix.

Three families (all 24 real files checked):
1. Inventory: a snapshot_scope table lists the systems captured, plus 8 tables with plain
   columns. Coverage is produced ONLY for systems in snapshot_scope (D-008): an empty table for a
   system outside the scope proves nothing (real example: customer_data_inventory_20260905.sql).
2. Primary vs replica: tables with record_json; each table is one copy (primary, read, reporting,
   regional or failover replica). record_json uses the same collections as snapshot bundles, so
   the bundle rules are reused. Each table covers every system that appears in the file, which is
   how a row present in a replica but missing from the primary is caught (sample case_16).
3. Plain tables: customer_profiles_primary / *_failover_replica and search_documents_primary.

Sighting time is the snapshot time from the file header ("Captured at:" or
snapshot_completed_at, or the snapshot_scope time); without one, the manifest time is used.
A row's updated_at is when it last changed, not when it was seen, so it is not used.
"""
import json
import re
import sqlite3

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated
from audit.readers.snapshot_bundle import COLLECTIONS, Who, add_records
from audit.sighting import Coverage, Sighting, SightingError, parse_time

NAME = "sql_snapshot"

ALLOWED = re.compile(r"^\s*(CREATE TABLE|CREATE INDEX|INSERT INTO|BEGIN|COMMIT|PRAGMA foreign_keys\s*=\s*ON)\b", re.I)
HEADER_TIME = re.compile(r"^--\s*(?:Captured at:\s*|snapshot_completed_at=)(\S+)", re.M)

# Inventory tables: table -> (system, category)
INVENTORY_TABLES = {
    "customer_profiles": ("account_service", "USER_PROFILE"),
    "customer_search_documents": ("search_service", "SEARCH_INDEX_ENTRY"),
    "customer_analytics_profiles": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support_records": ("support_platform", "SUPPORT_MESSAGE"),
    "customer_exports": ("export_service", "USER_EXPORT"),
    "connector_subscriptions": ("integration_service", "DATA_FLOW"),
    "backup_restore_entries": ("backup_service", "BACKUP_ARCHIVE"),
    "security_audit_records": ("security_service", "SECURITY_AUDIT_LOG"),
}
# Plain (non-inventory, non-record_json) tables: table -> (system, category)
PLAIN_TABLES = {
    "customer_profiles_primary": ("account_service", "USER_PROFILE"),
    "customer_profiles_failover_replica": ("account_service", "USER_PROFILE"),
    "search_documents_primary": ("search_service", "SEARCH_INDEX_ENTRY"),
}
IDENTIFIER_COLUMNS = ("email", "phone", "email_hash", "customer_reference", "subject_reference", "user_id",
                      "requester_email", "requester_phone", "owner_email", "owner_phone", "customer_email")
NAME_COLUMNS = ("full_name", "display_name", "requester_name")


def can_read(path, text):
    return path.endswith(".sql")


def load(text):
    """Return an in-memory database built only from allowed statements, or raise ValueError."""
    db = sqlite3.connect(":memory:")
    buf = []
    for line in text.splitlines(keepends=True):
        if not buf and line.strip().startswith("--"):
            continue
        buf.append(line)
        statement = "".join(buf)
        if sqlite3.complete_statement(statement):
            if not ALLOWED.match(statement):
                raise ValueError("statement type not allowed")
            db.execute(statement)
            buf = []
    if "".join(buf).strip():
        raise ValueError("incomplete statement at end of file")
    return db


def _rows(db, table):
    cursor = db.execute(f'SELECT * FROM "{table}"')
    columns = [c[0] for c in cursor.description]
    for index, row in enumerate(cursor):
        yield index, dict(zip(columns, row))


def _plain_state(table, row, who):
    """State for an inventory or plain-table row, after who has collected its identifiers."""
    if table in ("customer_analytics_profiles",):
        return ("EXPOSED" if who.pii else "ANONYMIZED"), ("identifiers present" if who.pii else "no identifiers"), {}
    if table == "support_records":
        return ("EXPOSED" if who.pii else "REDACTED"), ("identifiers present" if who.pii else "no identifiers"), {}
    if table == "connector_subscriptions":
        enabled = bool(row.get("enabled"))
        return ("FLOW_ACTIVE" if enabled else "FLOW_DISABLED"), f"enabled: {str(enabled).lower()}", {"enabled": enabled}
    if table == "backup_restore_entries":
        allowed = bool(row.get("restore_allowed"))
        return (("RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"), f"restore_allowed: {str(allowed).lower()}",
                {"restore_allowed": allowed})
    if table == "security_audit_records":
        return "PRESENT", "listed", ({"retention_until": row["retention_until"]} if row.get("retention_until") else {})
    return "PRESENT", (row.get("account_status") or "listed"), {}


def _add_plain_rows(result, file_id, file_captured_at, db, table, system, category, store, observed_at):
    for index, row in _rows(db, table):
        location = f"{table}[{index}]"
        who = Who()
        for column in IDENTIFIER_COLUMNS:
            who.identifier(row.get(column))
        for column in NAME_COLUMNS:
            who.name(row.get(column))
        try:
            state, raw_state, extras = _plain_state(table, row, who)
            future = is_future_dated(observed_at, file_captured_at)
            result.sightings.append(Sighting(
                file_id=file_id, location=location, reader=NAME,
                observed_at=observed_at, time_source="observation", file_captured_at=file_captured_at,
                system_id=system, store=store, resource_id=row["resource_id"],
                state=state, raw_state=raw_state, category=category, category_source="section",
                identifier_fps=frozenset(who.fps), user_refs=frozenset(who.refs),
                pii_fields_present=frozenset(who.pii), extras=extras, future_dated=future,
            ))
        except (KeyError, TypeError, SightingError, ValueError) as error:
            result.notes.append(ReaderNote(file_id, location, f"row rejected: {type(error).__name__}"))


def _cover(result, file_id, system, store, checked_at, file_captured_at):
    if is_future_dated(checked_at, file_captured_at):
        result.notes.append(ReaderNote(file_id, store, "coverage " + FUTURE_DATED_NOTE + "; no coverage"))
    else:
        result.coverage.append(Coverage(file_id, system, store, checked_at, file_captured_at))


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    try:
        db = load(text)
    except (ValueError, sqlite3.Error) as error:
        result.notes.append(ReaderNote(file_id, "file", f"file refused: {error}"))
        return result

    header = HEADER_TIME.search(text)
    snapshot_at = parse_time(header.group(1)) if header else file_captured_at
    tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    if "snapshot_scope" in tables:  # 1. inventory
        scope = {system: parse_time(at) for system, at in db.execute("SELECT origin_system, captured_at FROM snapshot_scope")}
        for system, checked_at in sorted(scope.items()):
            _cover(result, file_id, system, f"{system}/inventory", checked_at, file_captured_at)
        for table in tables:
            if table == "snapshot_scope":
                continue
            if table not in INVENTORY_TABLES:
                result.notes.append(ReaderNote(file_id, table, "unknown inventory table; ignored"))
                continue
            system, category = INVENTORY_TABLES[table]
            observed_at = scope.get(system, snapshot_at)
            if system not in scope and db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
                result.notes.append(ReaderNote(file_id, table, "rows for a system outside snapshot_scope"))
            _add_plain_rows(result, file_id, file_captured_at, db, table, system, category,
                            f"{system}/inventory", observed_at)
        return result

    for table in tables:
        columns = [c[1] for c in db.execute(f'PRAGMA table_info("{table}")')]
        if "record_json" in columns:  # 2. primary vs replica copies
            systems_in_file = {s for t in tables
                               if "record_json" in [c[1] for c in db.execute(f'PRAGMA table_info("{t}")')]
                               for (s,) in db.execute(f'SELECT DISTINCT origin_system FROM "{t}"')}
            for system in sorted(systems_in_file):
                _cover(result, file_id, system, f"{system}/{table}", snapshot_at, file_captured_at)
            by_collection = {}
            for index, row in _rows(db, table):
                collection = row.get("collection_name")
                if collection not in COLLECTIONS:
                    result.notes.append(ReaderNote(file_id, f"{table}[{index}]", "unknown collection; ignored"))
                    continue
                try:
                    record = json.loads(row["record_json"])
                except (TypeError, ValueError):
                    result.notes.append(ReaderNote(file_id, f"{table}[{index}]", "record_json is not valid JSON"))
                    continue
                by_collection.setdefault((row["origin_system"], collection), []).append((index, record))
            for (system, collection), items in sorted(by_collection.items()):
                for index, record in items:
                    add_records(result, NAME, file_id, file_captured_at, f"{table}[{index}]", system, collection,
                                [record], snapshot_at, copy_name=f"{system}/{table}")
            continue
        if table in PLAIN_TABLES:  # 3. plain tables
            system, category = PLAIN_TABLES[table]
            store = f"{system}/{table}"
            _cover(result, file_id, system, store, snapshot_at, file_captured_at)
            _add_plain_rows(result, file_id, file_captured_at, db, table, system, category, store, snapshot_at)
            continue
        result.notes.append(ReaderNote(file_id, table, "unknown table; ignored"))
    return result
