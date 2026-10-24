"""Turn an AI transcription of a screenshot or PDF (prompt extract_document) into sightings.

The AI only copied what it saw (audit/prompts.py). Every interpretation happens here, in plain
code, with the same rules as the other readers:
- Only real record identifiers (word_number, e.g. backup_9287, support_001) become sightings.
  Orders, tickets, file names and similar are counted in one note (like D-015's side report).
- The record kind comes from the identifier prefix (D-016). For support_ records the document's
  own clue decides: a customer-uploaded attachment document makes it a SUPPORT_ATTACHMENT
  (sample case_07: support_001 in delivery_correction_attachment.pdf must be deleted), otherwise
  it is a SUPPORT_MESSAGE with a note, as D-016 says for "no clue".
- Screens rarely show full dates, so the sighting time is the file's saved time (time_source
  "file"). Dates are only used as facts (keep-until), repaired when split ("10 24 UTC" -> 10:24)
  and completed with the file's year when the year is missing.
- A backup named in a completed "restored" event can bring data back: RESTORE_ALLOWED. A backup
  without restore information is UNKNOWN with a note, never a guess. A data flow is active unless
  its status says it is paused, disabled or stopped (the less safe state, as in D-009/D-023).
"""
import re
from datetime import datetime, timezone

from audit.catalog import PREFIXES
from audit.sighting import fingerprint_email, fingerprint_phone
from audit.readers import ReaderNote, ReaderResult
from audit.readers.snapshot_bundle import Who
from audit.sighting import Sighting, SightingError

NAME = "ai_document"

RECORD_ID = re.compile(r"\b(?!user_\d)([a-z]+(?:_[a-z]+)*)_(\d+)\b")
EXPOSABLE = {"ANALYTICS_PROFILE", "SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE"}
FLOW_OFF_WORDS = ("paused", "disabled", "stopped", "inactive", "off")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAY_MONTH = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?(?:\s+(\d{4}))?(?:,?\s+(\d{1,2})[:\s](\d{2})(?::(\d{2}))?)?")
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?")


EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Never match inside an existing fp:<kind>:<hex> token, so scrubbing twice changes nothing.
PHONE_IN_TEXT = re.compile(r"(?<![\w:])\+?\d[\d\s().-]{7,}\d(?!\w)")
HASH_IN_TEXT = re.compile(r"(?<![\w:])[0-9a-f]{64}(?!\w)")
USER_REF_TEXT = re.compile(r"^user_\d+$")
KEEP_FACTS = ("uploader", "uploaded by")  # the only facts the reader uses
EVENT_WORDS = ("restor",)                  # event texts the reader uses


def _fp_token(kind, value):
    value = value.strip()
    if value.startswith("fp:"):
        return value  # already a fingerprint token
    if kind == "email":
        return "fp:email:" + fingerprint_email(value)
    if kind == "phone":
        return "fp:phone:" + fingerprint_phone(value)
    if kind == "email_hash":
        return "fp:email_hash:" + value.lower()
    if kind == "name":
        return "fp:name"
    return value  # user_reference: a stable internal id, not personal data (D-018)


def _scrub_text(text):
    text = EMAIL_IN_TEXT.sub(lambda m: _fp_token("email", m.group(0)), text)
    text = HASH_IN_TEXT.sub(lambda m: "fp:email_hash:" + m.group(0), text)
    return PHONE_IN_TEXT.sub(lambda m: _fp_token("phone", m.group(0)) if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0), text)


def scrub_answer(answer):
    """Keep only what the reader needs; personal identifiers become fingerprint tokens (D-034).

    Record ids, user references, dates and status words stay. Emails, phones and email hashes become
    fp:<kind>:<fingerprint>; names become fp:name. Facts other than the uploader and event texts that
    name no record and no restore are removed, since they can hold names, addresses and messages.
    """
    out = dict(answer)
    records = []
    for record in answer.get("records") or []:
        r = dict(record)
        ids = [m.group(0) for m in RECORD_ID.finditer(record.get("resource_id") or "")]
        r["resource_id"] = " / ".join(ids) if ids else "[entry without record id]"
        r["identifiers"] = [{"kind": i["kind"], "value": _fp_token(i["kind"], i["value"])}
                            for i in record.get("identifiers") or []]
        r["facts"] = [{"label": f["label"], "value": f["value"] if f["label"].strip().lower() in KEEP_FACTS else "[removed]"}
                      for f in record.get("facts") or []]
        r["status_text"] = _scrub_text(record["status_text"]) if record.get("status_text") else record.get("status_text")
        records.append(r)
    out["records"] = records
    events = []
    for event in answer.get("events") or []:
        text, rid = event.get("text") or "", event.get("resource_id")
        keep = bool(RECORD_ID.search(text) or RECORD_ID.search(rid or "")) or any(w in text.lower() for w in EVENT_WORDS)
        events.append({"time_text": event.get("time_text"),
                       "resource_id": rid if rid and RECORD_ID.search(rid) else None,
                       "text": _scrub_text(text) if keep else "[removed]"})
    out["events"] = events
    return out


def can_read(path, text=None):
    return path.endswith((".png", ".pdf"))


def parse_loose_date(text, default_year):
    """Read a date as written on a screen or PDF; return ISO-8601 UTC text or None.

    Handles "25 Aug 2027 04:03 UTC", "26 Jul 2026", "26 Apr 02:00" (year taken from default_year),
    split times "10 24 UTC", and ISO forms. Never invents a time: date-only stays midnight.
    """
    if not text:
        return None
    m = ISO.search(text)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
    else:
        m = DAY_MONTH.search(text)
        if not m or m.group(2).lower()[:3] not in MONTHS:
            return None
        d, month, y, hh, mm, ss = m.groups()
        mo, y = MONTHS[month.lower()[:3]], (y or default_year)
    try:
        value = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_attachment_document(file_path, answer):
    title = (answer.get("document_title") or "").lower()
    if "attachment" in file_path.lower() or "attachment" in title:
        return True
    for record in answer.get("records", []):
        for fact in record.get("facts", []):
            if fact["label"].lower() in ("uploader", "uploaded by") and fact["value"].strip().lower() == "customer":
                return True
    return False


def read_answer(file_id, file_path, answer, file_captured_at):
    """Return a ReaderResult for one cached extract_document answer."""
    result = ReaderResult()
    default_year = file_captured_at.year
    attachment_doc = _is_attachment_document(file_path, answer)
    if answer.get("contains_instructions_to_reader"):
        result.notes.append(ReaderNote(file_id, "document", "AI flagged text addressed to the reader; ignored (data only)"))
    for item in answer.get("unreadable") or []:
        result.notes.append(ReaderNote(file_id, "document", "AI could not read part of the document"))

    restored = set()
    for event in answer.get("events") or []:
        text = event.get("text") or ""
        if "restored" in text.lower() and ("completed" in text.lower() or "complete" in text.lower()):
            restored |= {m.group(0) for m in RECORD_ID.finditer(text) if m.group(1) == "backup"}
            restored |= {m.group(0) for m in RECORD_ID.finditer(event.get("resource_id") or "") if m.group(1) == "backup"}

    merged, other_kinds = {}, 0
    for index, record in enumerate(answer.get("records") or []):
        ids = [m.group(0) for m in RECORD_ID.finditer(record.get("resource_id") or "")]
        if not ids:
            other_kinds += 1
            continue
        for rid in ids:
            entry = merged.setdefault(rid, {"who": Who(), "statuses": [], "dates": {}, "facts": {}, "where": []})
            entry["where"].append(f"records[{index}]")
            for ident in record.get("identifiers") or []:
                (entry["who"].name if ident["kind"] == "name" else entry["who"].identifier)(ident["value"])
            if record.get("status_text"):
                entry["statuses"].append(record["status_text"])
            for d in record.get("dates") or []:
                entry["dates"][d["label"].strip().lower()] = d["value"]
            for f in record.get("facts") or []:
                entry["facts"][f["label"].strip().lower()] = f["value"]
    for rid in restored - set(merged):
        merged[rid] = {"who": Who(), "statuses": [], "dates": {}, "facts": {}, "where": ["events"]}

    if other_kinds:
        result.notes.append(ReaderNote(file_id, "document", f"{other_kinds} entries without a record identifier "
                                                            "(orders, tickets, files, people); side report only"))

    for rid, entry in sorted(merged.items()):
        prefix = RECORD_ID.match(rid).group(1)
        location = ",".join(entry["where"])
        if prefix not in PREFIXES:
            result.notes.append(ReaderNote(file_id, location, f"record prefix {prefix!r} has no rule; side report only"))
            continue
        category, system_id = PREFIXES[prefix]
        if prefix == "support":
            if attachment_doc:
                category = "SUPPORT_ATTACHMENT"
            else:
                result.notes.append(ReaderNote(file_id, location, "support record without attachment clue; treated as message (D-016)"))
        who, statuses = entry["who"], " ".join(entry["statuses"]).lower()
        extras, state, raw = {}, "PRESENT", (entry["statuses"][0] if entry["statuses"] else "shown")

        if category == "DATA_FLOW":
            active = not any(word in statuses for word in FLOW_OFF_WORDS)
            state, extras = ("FLOW_ACTIVE" if active else "FLOW_DISABLED"), {"enabled": active}
        elif category == "BACKUP_ARCHIVE":
            if rid in restored:
                state, raw, extras = "RESTORE_ALLOWED", "restored event completed", {"restore_allowed": True}
            else:
                state = "UNKNOWN"
                result.notes.append(ReaderNote(file_id, location, "backup shown without restore information; UNKNOWN"))
        elif category in EXPOSABLE:
            state = "EXPOSED" if who.pii else ("ANONYMIZED" if category == "ANALYTICS_PROFILE" else "REDACTED")
        if category == "SUPPORT_ATTACHMENT" and who.fps:
            extras = {"attachment_owners": tuple(sorted(who.fps))}
        for label, value in entry["dates"].items():
            if "retain" in label or "retention" in label:
                parsed = parse_loose_date(value, default_year)
                if parsed:
                    extras["retention_until"] = parsed
                else:
                    result.notes.append(ReaderNote(file_id, location, "keep-until date could not be read"))
        try:
            result.sightings.append(Sighting(
                file_id=file_id, location=location, reader=NAME,
                observed_at=file_captured_at, time_source="file", file_captured_at=file_captured_at,
                system_id=system_id, store=system_id, resource_id=rid,
                state=state, raw_state=re.sub(r"\S+@\S+", "<email>", raw)[:60],
                category=category, category_source="prefix",
                identifier_fps=frozenset(who.fps), user_refs=frozenset(who.refs),
                pii_fields_present=frozenset(who.pii), extras=extras,
            ))
        except SightingError as error:
            result.notes.append(ReaderNote(file_id, location, f"record rejected: {type(error).__name__}"))
    return result


def extract(client, file_path):
    """Ask the AI (or its cache) to transcribe one PNG/PDF. The request shape is fixed so cache keys stay stable."""
    from audit.llm import Part
    from audit.prompts import EXTRACT_DOCUMENT
    import os

    name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    media = Part("image", data, mime="image/png") if file_path.endswith(".png") else Part("pdf", data, filename=name)
    return client.ask(EXTRACT_DOCUMENT, [Part("text", f"Document file name: {name}"), media], f"extract_document:{name}",
                      transform=scrub_answer)
