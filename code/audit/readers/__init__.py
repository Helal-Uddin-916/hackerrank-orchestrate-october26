"""Evidence readers: one adapter per evidence format (DESIGN.md principle 1).

Every reader has the same shape:
    can_read(path, text) -> bool
    read(file_id, path, text, file_captured_at) -> ReaderResult
and returns sightings and coverage in the shared shapes from audit.sighting, plus notes about
anything it could not understand. Nothing is skipped silently.
"""
from dataclasses import dataclass, field
from typing import List

from audit.sighting import Coverage, Sighting


# A file's own state words -> our fixed states (shared by every reader that sees these words).
# Some words depend on the kind of record: ACTIVE on a backup means restore is allowed.
STATE_WORDS = {
    "NOT_FOUND": "ABSENT",
    "FOUND": "PRESENT",
    "IDENTIFIABLE": "EXPOSED",
    "ANONYMIZED": "ANONYMIZED",
    "REDACTED": "REDACTED",
}
STATE_WORDS_BY_CATEGORY = {
    ("DATA_FLOW", "ACTIVE"): "FLOW_ACTIVE",
    ("DATA_FLOW", "DISABLED"): "FLOW_DISABLED",
    ("BACKUP_ARCHIVE", "ACTIVE"): "RESTORE_ALLOWED",
    ("BACKUP_ARCHIVE", "BLOCKED"): "RESTORE_BLOCKED",
}


def state_from_word(category, word):
    """Return our fixed state for a file's state word, or None if the word is not known."""
    return STATE_WORDS_BY_CATEGORY.get((category, word)) or STATE_WORDS.get(word)


FUTURE_DATED_NOTE = "dated after its file was saved; kept for linking only (D-019)"


def is_future_dated(observed_at, file_captured_at):
    return observed_at > file_captured_at


@dataclass(frozen=True)
class ReaderNote:
    """Something a reader saw but did not turn into a sighting. Never holds file content."""
    file_id: str
    location: str
    message: str


@dataclass
class ReaderResult:
    sightings: List[Sighting] = field(default_factory=list)
    coverage: List[Coverage] = field(default_factory=list)
    notes: List[ReaderNote] = field(default_factory=list)
