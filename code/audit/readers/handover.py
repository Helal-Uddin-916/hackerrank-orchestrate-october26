"""Reader for shift-handover text files (e.g. evidence/records/customer_account_handover_*.txt).

Format:
    CUSTOMER PLATFORM - SHIFT HANDOVER
    Snapshot: <time>

    Account and downstream checks completed during the shift:

    - <time> | <system_id> | <resource_id> | <CATEGORY> | <STATE> | <who>

<who> is an email, a user_<n> reference, an email hash, or "no direct identifier".
Any other non-blank line becomes a note; its text is never copied (it may hold personal data
or planted instructions).
"""
import re

from audit.readers import FUTURE_DATED_NOTE, ReaderNote, ReaderResult, is_future_dated, state_from_word
from audit.sighting import Sighting, SightingError, fingerprint_email, parse_time

NAME = "handover_text"
TITLE = "CUSTOMER PLATFORM - SHIFT HANDOVER"
EXPECTED_HEADER = re.compile(r"^(CUSTOMER PLATFORM - SHIFT HANDOVER|Snapshot: \S+|"
                             r"Account and downstream checks completed during the shift:)$")
ROW = re.compile(r"^- (?P<time>\S+) \| (?P<system>[a-z_]+) \| (?P<resource>[a-z_]+_\d+) \| "
                 r"(?P<category>[A-Z_]+) \| (?P<state>[A-Z_]+) \| (?P<who>.+)$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_REF = re.compile(r"^user_\d+$")
HASH = re.compile(r"^[0-9a-f]{64}$")
NO_IDENTIFIER = "no direct identifier"


def can_read(path, text):
    return text.lstrip().startswith(TITLE)


def read(file_id, path, text, file_captured_at):
    result = ReaderResult()
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        location = f"line {line_no}"
        if not line or EXPECTED_HEADER.match(line):
            continue
        row = ROW.match(line)
        if not row:
            result.notes.append(ReaderNote(file_id, location, "not a record row; ignored"))
            continue

        category, word, who = row["category"], row["state"], row["who"].strip()
        state = state_from_word(category, word)
        if state is None:
            result.notes.append(ReaderNote(file_id, location, f"unknown state word {word!r}; recorded as UNKNOWN"))
            state = "UNKNOWN"

        fps, refs, pii = set(), set(), set()
        if EMAIL.match(who):
            fps.add(fingerprint_email(who))
            pii.add("email")
        elif HASH.match(who):
            fps.add(who)
            pii.add("email_hash")
        elif USER_REF.match(who):
            refs.add(who)
        elif who != NO_IDENTIFIER:
            result.notes.append(ReaderNote(file_id, location, "identifier column not understood; no link recorded"))
        if state == "ABSENT":
            pii.clear()  # on a NOT_FOUND row the email is only the lookup key, not data still held

        try:
            observed_at = parse_time(row["time"])
            future = is_future_dated(observed_at, file_captured_at)
            if future:
                result.notes.append(ReaderNote(file_id, location, FUTURE_DATED_NOTE))
            result.sightings.append(Sighting(
                file_id=file_id, location=location, reader=NAME,
                observed_at=observed_at, time_source="observation",
                file_captured_at=file_captured_at, future_dated=future,
                system_id=row["system"], store=row["system"], resource_id=row["resource"],
                state=state, raw_state=word,
                category=category, category_source="label",
                identifier_fps=frozenset(fps), user_refs=frozenset(refs), pii_fields_present=frozenset(pii),
            ))
        except (SightingError, ValueError) as error:
            result.notes.append(ReaderNote(file_id, location, f"row rejected: {error}"))
    return result
