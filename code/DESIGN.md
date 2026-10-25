# DESIGN

Purpose: architecture of the deletion-audit agent (components, data flow, interfaces); filled in during Phase 2.

Decisions referenced as D-xxx are in [DECISIONS.md](DECISIONS.md).

## 1. The parts of the program

The program handles each request in eight parts, in this order. Parts 1–2 run once for the
whole dataset; parts 3–8 run once per request.

### Part 1: Read the files
- **In:** everything under `dataset/`: the requests, identities, identity history, activity,
  rules, evidence manifest, and evidence files.
- **Out:** the same data loaded into memory, unchanged.
- **Follows:** SQL files are checked against a list of allowed statement types before they are
  loaded, each into its own in-memory database. Evidence text is treated as data, never as
  instructions.

### Part 2: Turn all evidence into one simple shape
- **In:** evidence of every type: SQL, JSON, four log styles, text, images, PDFs.
- **Out:** one list of **sightings**. Each sighting says: *which system, which record, what
  state, which identifiers it shows, when it was seen, which file it came from.*
- **Follows:** each sighting keeps its own time and its file's saved time; both matter (D-017).
  Images and PDFs are read once by an AI and the results are saved and reused (D-003). Every later
  part reads only sightings, so a new file format only changes this part.

### Part 3: Check the request: deletion or not?
- **In:** the request text.
- **Out:** *deletion* or *not deletion*. Not deletion means the answer is `NOT_APPLICABLE` with `[]`.
- **Follows:** an AI and a simple word check both decide. If they disagree, the request is listed
  for a person to review (D-010).

### Part 4: Find the person
- **In:** the request email, current identities, identity history.
- **Out:** the person's stable `user_id` plus all their emails, phones and email hashes, old and new.
- **Follows:** emails are compared after trimming spaces and lowercasing. No match, or more than
  one person matching, is flagged as a warning.

### Part 5: Find their data
- **In:** the person's identifiers, the sightings, the activity stream, and the audit date.
- **Out:** every record that belongs to the person, with its state on the audit date and the
  file that proves it.
- **Follows:**
  - links come through identifiers or `user_id`; records with no identifier use D-005;
  - use only files saved on or before the audit date, then trust the latest sighting in them (D-017);
  - "not seen" is not "gone" (D-008);
  - when sightings disagree, take the worse one (D-009).

### Part 6: Apply the rules
- **In:** each record found, and the rules.
- **Out:** for each record, what is still wrong (if anything) and what it must become (gone,
  details hidden, anonymized, kept, flow off, restore blocked).
- **Follows:**
  - the matching table (D-016);
  - keep-until dates and legal holds are checked against the audit date (D-013);
  - shared attachments are flagged (D-014);
  - records that fit no rule go to a side report (D-015).

### Part 7: Write the answer
- **In:** the results of part 6.
- **Out:** the status (`COMPLETE` or `INCOMPLETE`) and the ordered steps.
- **Follows:**
  - steps are ordered by phase, then operation, then system, then record;
  - each step cites the file that proves it (D-011);
  - `INCOMPLETE` remediation and verification steps cite `""`;
  - a deletion request with no evidence is flagged (D-012).

### Part 8: Check the answer
- **In:** all rows.
- **Out:** `output.csv` at the repo root, plus a warnings report.
- **Follows:**
  - exactly 518 rows, in number order of `case_id` (D-006);
  - only allowed operation/state pairs, steps numbered from 1;
  - no cited file captured after the audit date;
  - the same input always gives the same output.

## 2. How the parts connect

```text
                         dataset/  (read only)
                              |
                     [1] Read the files
                              |
          +-------------------+--------------------+
          |                                        |
 [2] Turn evidence into sightings          requests, identities,
     (images/PDFs: AI, saved once) ---+    history, activity, rules
          |                           |            |
          |                    saved AI results    |
          |                    (in code/, reused)  |
          v                                        v
  ======================  for each request  ======================
          |
  [3] Check the request ----(AI + word check; saved)----+
          |                                             |
          | deletion                                    | not deletion
          v                                             v
  [4] Find the person                          NOT_APPLICABLE, []
          |                                             |
  [5] Find their data  <-- sightings, activity          |
          |                                             |
  [6] Apply the rules  <-- matching table (D-016)       |
          |                                             |
  [7] Write the answer (status + ordered steps)         |
          |                                             |
          +-----------------------+---------------------+
                                  |
                       [8] Check the answer
                                  |
                 output.csv  +  warnings report
```

Where the AI is used: only part 2 (reading images/PDFs) and part 3 (reading request text).
Both save their answers, so a normal run reads saved answers and makes no AI calls.
Everything else is plain code that gives the same result every time.

Warnings from parts 3–8 (request disagreement, no person match, no evidence found, ended
retention, shared attachment, record fitting no rule, conflicting sightings) all go into one
warnings report, which a person reads after each run.

## 3. Design principles that make the agent better

### A. Correct
1. **One adapter per evidence format.** Each format (SQL, JSON, each log style, text,
   images/PDFs) has its own small reader. All readers output the same sighting shape (part 2).
   A bug in one reader cannot break another, and a new format means one new adapter.
2. **Check data at every handover.** Each part validates what it receives (a sighting has a
   system, record, state, time and file; an AI answer has the expected fields and values).
   Bad data is caught where it starts, not several parts later.
3. **Rules as a table, not buried in code.** The record-to-rule matching (D-016) and the
   state-to-step mapping live in one readable table in a `.py` module; the code looks them up.

### B. Explainable
4. **A "why" trail for every case.** For each request the program records the person found,
   records found, the sighting trusted for each, the rule applied, and why the status was
   chosen. Used for debugging mismatches and for the interview.
5. **Proof travels with the data.** Every sighting carries its file_id, location in the file and
   its own time from the start, so citations (D-011) and the audit-date cutoff (D-017) are
   automatic.

### C. Safe and steady AI
6. **Locked-down AI calls.** Fixed model version, temperature 0, versioned prompts kept in a
   `.py` or `.md` file, and answers in a strict format that is validated (principle 2). Answers
   are cached by a SHA-256 fingerprint of the content (D-003, D-010).
7. **Plan for AI failure.** Time limit, a few retries, then fall back to the word check and
   raise a warning. One failed call never stops the run.
8. **Evidence is data, never commands.** Evidence text is passed to the AI clearly marked as
   content to read, never as instructions, and the reply is validated.

### D. Hard to break
9. **One bad file or request does not stop the run.** It is skipped with a warning; every
   other request still gets its row.
10. **Tests as a safety net.** Every change is checked against the 32 sample answers and our
    own hard cases (custom evaluation cases), so a fix in one place cannot silently break another.

### E. Easy to run and watch
11. **Settings separate from code.** Paths, model name and limits live in one settings module;
    secrets come only from environment variables (`.env`, never committed).
12. **A run summary.** Each run reports counts: requests, statuses, warnings, AI calls, input
    and output tokens, and estimated cost. These feed `evaluation/usage_report.md`.

## 4. The two data shapes every part shares

### 4.1 Sighting: one record seen at one moment in one file
| Group | Field | Meaning |
|---|---|---|
| Where from | `file_id` | evidence file, e.g. `evidence_03` |
| | `location` | line number, JSON path, or SQL table + row, so the source can always be re-checked |
| | `reader` | which adapter produced it (principle 1) |
| When | `observed_at` | time on the sighting itself; picks the winning sighting (D-017) |
| | `file_captured_at` | when the file was saved (manifest); files saved after the audit date are not eligible (D-017) |
| | `future_dated` | the sighting is dated after its own file was saved: kept for linking only, never for state or citation (D-019) |
| | `time_source` | `observation` or `file` (fallback when the sighting has no time) |
| Which copy | `system_id` | service, e.g. `search_service`; used in output steps |
| | `store` | exact copy: primary, read replica, regional replica, failover, export, … |
| | `resource_id` | e.g. `search_052` |
| What kind | `category` | data_category from deletion_rules.csv, or empty |
| | `category_source` | `label`, `section` or `prefix` (D-016) |
| What state | `state` | one of: `PRESENT`, `ABSENT`, `EXPOSED`, `REDACTED`, `ANONYMIZED`, `FLOW_ACTIVE`, `FLOW_DISABLED`, `RESTORE_ALLOWED`, `RESTORE_BLOCKED`, `UNKNOWN` |
| | `raw_state` | the file's own wording (`FOUND`, `404`, `enabled: false`), kept for the why trail |
| Who | `identifier_fps` | SHA-256 fingerprints of normalized emails, phones and hashes shown (never raw values) |
| | `user_refs` | user_id references named in the record (`customer_reference`, `subject_reference`, …) |
| | `pii_fields_present` | which of name, email, phone, email_hash are non-empty |
| Extra facts | `extras` | only when present: `retention_until`, `legal_hold`, `restore_allowed`, `enabled`, `attachment_owners` |

Examples (no personal data):
- `evidence_03` line 26 → `search_service` / `search_052`, category `SEARCH_INDEX_ENTRY` (label),
  state `PRESENT` (raw `FOUND`), observed 2026-10-01T12:00:00Z (observation), 1 email
  fingerprint, pii_fields_present = [email].
- `evidence_92` `service_exports/backup_service/restore_entries` → `backup_service` /
  `backup_5970`, category `BACKUP_ARCHIVE` (section), state `RESTORE_BLOCKED`
  (raw `restore_allowed: false`), observed 2026-09-15T10:39:37Z (observation),
  user_refs = [user_27], pii_fields_present = [].

### 4.2 Coverage: a file checked a place
| Field | Meaning |
|---|---|
| `file_id` | evidence file that did the check |
| `system_id`, `store` | the place that was checked |
| `checked_at` | when it was checked |

Used by D-008: a record counts as absent from a place only if a coverage entry for that place
exists at or before the audit date and no sighting of the record appears in it.
Example: `evidence_54` covers the account failover copy at 2026-09-02T23:00:00Z.

## 5. Production checklist

Compared with a general enterprise AI-agent checklist, this agent deliberately skips a UI,
PostgreSQL/Redis, vector search/RAG, agent frameworks, multi-agent setups and Docker: it is a
single-command batch audit over a small, structured dataset where exact identifier matching is
required (similarity search would pull in the wrong people and make answers vary). The items
below are the ones that do apply.

| # | Item | How | Status |
|---|---|---|---|
| 1 | Prompt-injection defence is tested | Hard cases where evidence text and a request text try to give the agent orders (case_920, case_930) | done: agent passes both (code/check.py) |
| 2 | Every AI call is traced | One trace record per call: time, purpose, model, prompt version, input/output tokens, latency, cache hit, error. Totals feed `evaluation/usage_report.md` | done: code/audit/llm.py trace; totals in code/evaluation/run_record.md |
| 3 | One-command quality gate | `python3 code/check.py` runs all tests, builds and checks the hard cases, checks `output.csv` if present, and grades the agent when it exists; non-zero exit on any failure | done: tests, hard cases, output.csv format, agent graded on samples and hard cases |
| 4 | The AI request check is measured on its own | A labelled set of request texts (deletion yes/no, including all 19 hard-case texts and the 10 real non-deletion texts) scored separately before it affects any answer | done: word check and AI agree on all 550 requests (D-010, D-033) |
| 5 | Model choice and budgets | A small fast model for request text, a vision-capable model for images/PDFs; hard caps on calls and tokens per run; bounded retries | done: Gemini Flash-Lite (D-025), 10 RPM pacing, call and token caps |
| 6 | Prompts are versioned | Each prompt has an id and version in a `.py` file; cached answers are keyed by content fingerprint + prompt version + model, so a changed prompt never reuses old answers | done: prompt id + version in every cache key; v1 answers removed when v2 replaced it |
| 7 | Every run is reproducible | A run record: dataset fingerprint, code version, model ids, prompt versions, counts, warnings | done: code/evaluation/run_record.md; three runs gave a byte-identical output.csv |

Already covered elsewhere: deterministic workflow with AI only for unclear inputs (sections 1–2),
guardrails and validation (principles 2, 8; SQL allowlist), human review instead of guessing
(D-009, D-012–D-015), golden datasets and regression tests (Phase 3), caching, bounded retries
and timeouts (principles 6–7), secrets only from the environment, provenance on every step.
