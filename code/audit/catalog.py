"""The one table of record kinds (D-016, DESIGN.md principle 3).

Record identifiers look like <prefix>_<number>. The prefix tells the record's kind and its system
when the evidence gives no better clue. Readers that know the kind from a label or section name
use that first; this table is the fallback, and the only copy of it.
"""

# prefix -> (data_category from deletion_rules.csv, system_id used in output steps)
PREFIXES = {
    "account": ("USER_PROFILE", "account_service"),
    "search": ("SEARCH_INDEX_ENTRY", "search_service"),
    "export": ("USER_EXPORT", "export_service"),
    "analytics": ("ANALYTICS_PROFILE", "analytics_platform"),
    "support": ("SUPPORT_MESSAGE", "support_platform"),  # SUPPORT_ATTACHMENT when the evidence says so (D-016)
    "audit_record": ("SECURITY_AUDIT_LOG", "security_service"),
    "legal_hold": ("LEGAL_HOLD_RECORD", "legal_service"),
    "backup": ("BACKUP_ARCHIVE", "backup_service"),
    "tombstone": ("ACCOUNT_TOMBSTONE", "identity_service"),
    "data_flow": ("DATA_FLOW", "integration_service"),
    "marketing": ("MARKETING_PROFILE", "marketing_service"),  # ASSUMPTION: no sample names this system
}

# Kinds that come from context, not from the identifier (D-021, D-022, attachment clue in D-016).
# When any sighting of a record shows one of these, it decides the record's kind.
CONTEXT_CATEGORIES = ("MARKETING_PROFILE", "SECONDARY_IDENTIFIER", "SUPPORT_ATTACHMENT")


def prefix_of(resource_id):
    return resource_id.rsplit("_", 1)[0]


def category_for_prefix(resource_id):
    entry = PREFIXES.get(prefix_of(resource_id))
    return entry[0] if entry else ""


def system_for_prefix(resource_id):
    entry = PREFIXES.get(prefix_of(resource_id))
    return entry[1] if entry else ""
