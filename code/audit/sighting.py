"""The two data shapes every part shares (DESIGN.md section 4).

Sighting: one record seen at one moment in one evidence file.
Coverage: one evidence file checked one place at one moment (needed for D-008).

Both check themselves when created and raise SightingError if anything is wrong, so bad data
is caught in the reader that produced it (principle 2). Personal identifiers are never stored
raw: only SHA-256 fingerprints (principle: privacy by design).
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, FrozenSet, Tuple

STATES = frozenset({
    "PRESENT",          # record exists
    "ABSENT",           # checked and not there (NOT_FOUND, 404, null lookup)
    "EXPOSED",          # record kept but still shows identifiers that policy removes
    "REDACTED",         # identifiers removed, record kept
    "ANONYMIZED",       # no longer linkable to the person
    "FLOW_ACTIVE",      # sync/connector can still copy data
    "FLOW_DISABLED",
    "RESTORE_ALLOWED",  # backup can still bring data back
    "RESTORE_BLOCKED",
    "UNKNOWN",          # the file mentions the record but its state cannot be read
})
TIME_SOURCES = frozenset({"observation", "file"})
CATEGORY_SOURCES = frozenset({"label", "section", "prefix", ""})
PII_FIELDS = frozenset({"name", "email", "phone", "email_hash"})
EXTRA_KEYS = frozenset({"retention_until", "legal_hold", "hold_until", "restore_allowed",
                        "enabled", "attachment_owners"})

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_USER_REF = re.compile(r"^user_\d+$")
_CATEGORY = re.compile(r"^[A-Z][A-Z_]*$")


class SightingError(ValueError):
    """A sighting or coverage entry broke one of the shape rules."""


def parse_time(value):
    """Parse an ISO-8601 UTC time ('...Z' or '+00:00') into a timezone-aware datetime."""
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise SightingError(f"time without timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def normalize_email(email):
    return email.strip().lower()


def fingerprint_email(email):
    # Same as dataset email_hash (SHA-256 of the lowercase email), so hashes in evidence match directly.
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def fingerprint_phone(phone):
    digits = re.sub(r"\D", "", phone)
    return hashlib.sha256(("phone:" + digits).encode("utf-8")).hexdigest()


def _require_text(obj, *names):
    for name in names:
        value = getattr(obj, name)
        if not isinstance(value, str) or not value.strip():
            raise SightingError(f"{type(obj).__name__}.{name} must be a non-empty string, got {value!r}")


def _require_time(obj, *names):
    for name in names:
        value = getattr(obj, name)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise SightingError(f"{type(obj).__name__}.{name} must be a timezone-aware datetime")


def _no_raw_identifier(where, text):
    if "@" in text:
        raise SightingError(f"{where} looks like it holds a raw email address")


@dataclass(frozen=True)
class Sighting:
    # Where from
    file_id: str
    location: str
    reader: str
    # When
    observed_at: datetime
    time_source: str
    file_captured_at: datetime
    # Which copy
    system_id: str
    store: str
    resource_id: str
    # What state
    state: str
    raw_state: str
    # What kind
    category: str = ""
    category_source: str = ""
    # Who
    identifier_fps: FrozenSet[str] = frozenset()
    user_refs: FrozenSet[str] = frozenset()
    pii_fields_present: FrozenSet[str] = frozenset()
    # Extra facts, only when present
    extras: Dict[str, object] = field(default_factory=dict)
    # Dated after its own file was saved: kept for linking only, never for state or citation (D-019)
    future_dated: bool = False

    def __post_init__(self):
        _require_text(self, "file_id", "location", "reader", "system_id", "store", "resource_id", "raw_state")
        _require_time(self, "observed_at", "file_captured_at")
        if (self.observed_at > self.file_captured_at) != self.future_dated:
            raise SightingError(f"{self.file_id} {self.resource_id}: future_dated must be set exactly when "
                                "the sighting is dated after its file was saved (D-019)")
        if self.time_source not in TIME_SOURCES:
            raise SightingError(f"time_source {self.time_source!r} not in {sorted(TIME_SOURCES)}")
        if self.state not in STATES:
            raise SightingError(f"state {self.state!r} not in {sorted(STATES)}")
        if self.category and not _CATEGORY.match(self.category):
            raise SightingError(f"category {self.category!r} must be UPPER_SNAKE_CASE")
        if self.category_source not in CATEGORY_SOURCES:
            raise SightingError(f"category_source {self.category_source!r} not in {sorted(CATEGORY_SOURCES)}")
        if bool(self.category) != bool(self.category_source):
            raise SightingError("category and category_source must be both set or both empty")
        bad_fps = [fp for fp in self.identifier_fps if not _FINGERPRINT.match(fp)]
        if bad_fps:
            raise SightingError("identifier_fps must be SHA-256 fingerprints, never raw values")
        bad_refs = [r for r in self.user_refs if not _USER_REF.match(r)]
        if bad_refs:
            raise SightingError(f"user_refs must look like user_<number>, got {bad_refs}")
        if not set(self.pii_fields_present) <= PII_FIELDS:
            raise SightingError(f"pii_fields_present must be within {sorted(PII_FIELDS)}")
        if not set(self.extras) <= EXTRA_KEYS:
            raise SightingError(f"unknown extras {sorted(set(self.extras) - EXTRA_KEYS)}")
        for name in ("location", "raw_state"):
            _no_raw_identifier(f"{self.file_id} {name}", getattr(self, name))
        for key, value in self.extras.items():
            if isinstance(value, str):
                _no_raw_identifier(f"{self.file_id} extras[{key}]", value)


@dataclass(frozen=True)
class Coverage:
    file_id: str
    system_id: str
    store: str
    checked_at: datetime
    file_captured_at: datetime

    def __post_init__(self):
        _require_text(self, "file_id", "system_id", "store")
        _require_time(self, "checked_at", "file_captured_at")
        if self.checked_at > self.file_captured_at:
            raise SightingError(f"{self.file_id}: coverage checked after its file was saved")
