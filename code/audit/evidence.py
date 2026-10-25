"""Read every evidence file in the manifest into one set of sightings and coverage (DESIGN.md part 2).

Each file goes to exactly one reader, chosen by file type and the reader's own can_read check.
A file that no reader claims, a missing file, or a reader failure becomes a note and the run goes
on (principle 9). Images and PDFs are read through the AI client, normally from the saved cache;
without a client they are noted as unread, never silently skipped.
"""
import csv
import os
from dataclasses import dataclass, field
from typing import Dict, List

from audit.llm import LLMError
from audit.readers import ReaderNote, ReaderResult, ai_document, handover, log_events, record_list, \
    service_export, snapshot_bundle, sql_snapshot
from audit.sighting import Coverage, Sighting, parse_time

TEXT_READERS = {
    ".sql": [sql_snapshot],
    ".log": [log_events],
    ".txt": [handover],
    ".json": [service_export, snapshot_bundle, record_list],
}


@dataclass
class EvidenceSet:
    sightings: List[Sighting] = field(default_factory=list)
    coverage: List[Coverage] = field(default_factory=list)
    notes: List[ReaderNote] = field(default_factory=list)
    reader_by_file: Dict[str, str] = field(default_factory=dict)
    captured_at: Dict[str, object] = field(default_factory=dict)

    def add(self, file_id, reader_name, result):
        self.reader_by_file[file_id] = reader_name
        self.sightings.extend(result.sightings)
        self.coverage.extend(result.coverage)
        self.notes.extend(result.notes)

    def summary(self):
        per_reader = {}
        for file_id, reader in self.reader_by_file.items():
            stats = per_reader.setdefault(reader, {"files": 0, "sightings": 0, "coverage": 0, "notes": 0})
            stats["files"] += 1
        for s in self.sightings:
            per_reader[self.reader_by_file[s.file_id]]["sightings"] += 1
        for c in self.coverage:
            per_reader[self.reader_by_file[c.file_id]]["coverage"] += 1
        for n in self.notes:
            reader = self.reader_by_file.get(n.file_id, "unread")
            per_reader.setdefault(reader, {"files": 0, "sightings": 0, "coverage": 0, "notes": 0})["notes"] += 1
        return per_reader


def _read_one(entry, dataset_dir, client):
    """Return (reader_name, ReaderResult) for one manifest entry."""
    file_id = entry["file_id"]
    path = os.path.join(dataset_dir, entry["file_path"])
    captured_at = parse_time(entry["captured_at"])
    extension = os.path.splitext(path)[1].lower()
    if not os.path.exists(path):
        return "unread", ReaderResult(notes=[ReaderNote(file_id, "file", "file listed in the manifest is missing")])

    if ai_document.can_read(path):
        if client is None:
            return "unread", ReaderResult(notes=[ReaderNote(file_id, "file", "image/PDF not read: no AI client")])
        try:
            answer = ai_document.extract(client, path)
        except LLMError as error:
            return "unread", ReaderResult(notes=[ReaderNote(file_id, "file", f"image/PDF not read: {error}")])
        return ai_document.NAME, ai_document.read_answer(file_id, path, answer, captured_at)

    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    claimed = [r for r in TEXT_READERS.get(extension, []) if r.can_read(path, text)]
    if len(claimed) != 1:
        message = "no reader recognises this file" if not claimed else "more than one reader claims this file"
        return "unread", ReaderResult(notes=[ReaderNote(file_id, "file", message)])
    reader = claimed[0]
    try:
        return reader.NAME, reader.read(file_id, path, text, captured_at)
    except Exception as error:  # one bad file never stops the run (principle 9)
        return "unread", ReaderResult(notes=[ReaderNote(file_id, "file", f"reader failed: {type(error).__name__}")])


def load_evidence(dataset_dir, client=None):
    """Read every file in dataset_dir/evidence_files.csv into one EvidenceSet."""
    evidence = EvidenceSet()
    with open(os.path.join(dataset_dir, "evidence_files.csv"), encoding="utf-8", newline="") as f:
        manifest = list(csv.DictReader(f))
    for entry in sorted(manifest, key=lambda e: e["file_id"]):
        evidence.captured_at[entry["file_id"]] = parse_time(entry["captured_at"])
        reader_name, result = _read_one(entry, dataset_dir, client)
        evidence.add(entry["file_id"], reader_name, result)
    return evidence
