# Interview prep — Ghost Data Still Exists

For you, not the judges (this file is outside `code/`, so it is not in `code.zip`).
Simple words first; numbers and decision ids so you can point at real evidence.

---

## 1. The 2-minute story

> "Northstar Shop marks deletion requests 'Complete', but copies of the data survive in replicas,
> search, support, analytics, exports and backups, and syncs or restores can bring data back.
> My agent audits each of the 518 requests the way a careful investigator would.
>
> First it checks that the person really asked for deletion — plain word rules and an AI must agree,
> otherwise the answer is NOT_APPLICABLE. Then it finds every email, phone and hash the person ever
> had. It turns all 94 evidence files — databases, JSON, six log styles, a text file, screenshots and
> PDFs — into one simple note shape I call a sighting. It keeps only files that existed on the audit
> date, lets the latest note win, keeps replica copies separate, and never treats 'not seen' as
> 'gone' unless the file really checked that place. Then it applies the deletion rules table, and
> writes COMPLETE with proof, or INCOMPLETE with findings, an ordered fix plan and the checks to run.
>
> Plain code makes every decision. The AI only reads screenshots and request texts, its answers are
> checked against a strict schema and saved, so the run is free, repeatable and byte-identical.
> On the 32 public samples: 100% status accuracy, 100% step precision, 91% recall, 21 of 21 of my own
> hard cases — and every rule is written down with its evidence in DECISIONS.md."

---

## 2. Architecture in one picture

```text
94 evidence files ─► 7 readers (one per format) ─► 2,914 sightings + 371 coverage notes
request ─► deletion? (word check + AI agree) ─► find person ─► find their records at audit_at
        ─► apply deletion_rules.csv per record ─► status + ordered steps + citations ─► output.csv
```

Why this shape (good to say out loud):
- **"Deterministic workflow + AI"**: the fixed rules are code; AI only where code cannot read
  (images) or language is fuzzy (request text). Repeatable and testable.
- **One shared note shape (sighting)**: a new file format only needs a new reader; nothing else changes.
- **Every step can be traced** back to a file, a line and a rule.

---

## 3. Likely questions and short answers

**Q: Why not RAG / a vector database / an agent framework?**
We need *exact* matches (this email, this record id) and the same answer every run. Similarity search
would pull in look-alike people (the data has look-alike decoy emails). The workflow is fixed, so a
framework adds weight without adding control. (DESIGN.md section 5.)

**Q: How do you handle time?**
Two rules: only files **saved by** `audit_at` can decide a record's state; inside them the **latest**
note wins (D-017). Files saved later, and rows dated after their own file, may only tell *whose* a
record is, never its state (D-005, D-019). Example: case_27's account came back two days after the
audit — the answer is still COMPLETE.

**Q: How do you find all of a person's data?**
Current and historical emails/phones/hashes from the identity history (100 requests use an old email;
D-026). The dataset's email hash is simply SHA-256 of the email, so hashes match directly. Log lines
without a name are linked through their trace id in the same file (D-020). Records with no identifier
at all are linked through cleanup-job timing, but only when exactly one request fits (D-028).

**Q: How do you know something is really deleted?**
"Not seen" is not "gone" (D-008). A record is absent only if a file actually checked that place:
a 404, NOT_FOUND, or a complete table copy without it. Snapshot extracts only count for the people
they are about (D-027); only plain single-table copies count for everyone (D-030). I found this by
measuring: the first version marked 15 kept security records as gone.

**Q: Replicas, syncs, backups?**
Replica tables are separate copies, so a row left only in a replica keeps the record present
(case_16). An active sync or a restorable backup is a recreation path: report it and **disable /
block it before** deleting dependent records (operation order in the plan). A connector that is off
but still scheduled counts as on (D-023).

**Q: How do you map records to rules?**
One catalog (D-016): written label first, then section/table name, then the id prefix. Context beats
the plain kind: an analytics record used as an opted-in marketing contact is a marketing profile →
delete (D-021); a replica copy whose deletion marker never arrived is a leftover identifier → delete
(D-022). Both learned from official sample answers, not guessed.

**Q: What about tombstones and kept records?**
A tombstone may keep the stable user id but no name/email/phone/hash; a hash left over is exposed
(hard case 914). Security records and legal holds are kept and only verified present; if their
keep-until date has passed, the agent flags it instead of guessing (D-013).

**Q: What if the evidence cannot prove anything?**
Then the answer is INCOMPLETE ("not confirmed"), never COMPLETE without proof (D-032) — that is the
exact failure the company had.

**Q: How did you evaluate it?**
A format checker, a grader (status, step precision/recall, field and sequence accuracy), 21 own hard
cases written as code, 300 unit tests, and one quality gate (`code/check.py`) that fails if the sample
scores drop. Results: 100% status, 100% precision, 90.7% recall on samples; 21/21 own cases.

**Q: Why is recall only 91%?**
18 sample records can't be linked without guessing: 15 have no identifier and their cleanup timing
fits several people equally (e.g. two requests closed at the same minute), 3 appear in no evidence at
all. I chose to leave them out (warning instead) rather than add wrong steps — I measured that
linking to every candidate would add hundreds of wrong steps across the 518 requests.

**Q: How do you protect against prompt injection?**
Evidence and request texts are data, never instructions; the AI only transcribes or classifies, and
code makes decisions. Two hard cases test it (a planted "note to AI auditor" in a file, a fake "system
instruction" in a request). A text that tries to steer the audit goes to human review → NOT_APPLICABLE.

**Q: Cost and tokens?**
Final run: 0 AI calls (saved answers). Creating the saved answers: 32 calls, ~97k tokens, $0 on the
free tier, ~$0.08 at the paid price (~$0.00015 per request). Batches of 25 texts per call, caps on
calls and tokens, no retries on billing errors.

**Q: Privacy?**
No personal data in logs or reports; identifiers stored as fingerprints; saved AI answers are scrubbed
before caching (D-034); secrets only from `.env` (never committed or zipped).

**Q: How would it scale?**
Build the sightings once (ingestion), index by identifier, answer each request independently (easy
to parallelise), cache AI answers by content fingerprint so only new files cost money.

**Q: What would you improve with more time?**
Better disambiguation for the 15 unlinkable records (more evidence sources, not guessing), proof of
presence for security records written minutes before the audit (34 warnings), and a human review UI
for the warnings report.

---

## 4. Trade-offs and limitations to state honestly

- Precision over recall: leave a record out rather than put a wrong one into someone's answer.
- Rules derived from 32 samples; a few are labelled ASSUMPTION (e.g. `marketing_service`,
  the 90/10-minute cleanup window).
- Screenshots/PDFs depend on one AI transcription (validated, cached, repaired dates in code).
- Free-tier AI may use prompts for training — acceptable only because the people are fictional.

---

## 5. "Tell me about a time…" stories (all real)

1. **Measuring caught a wrong rule.** "Checked, not there" first counted for everyone and hid 15 kept
   security records. Tracing one record showed snapshot files are extracts about specific people.
   Fixed with D-027 → 84/84 records right. Later a real run showed 127 more false absences; narrowed
   again (D-030) → 34, and those are timing, not bugs.
2. **The AI needed measuring before use.** Prompt v1 flagged ordinary wording ("please treat this
   message as a deletion request") as an attack. Measured before it touched any answer, fixed in v2:
   0 false flags, full agreement with the word rules on all 550 texts.
3. **A data trap.** Two files hide a row dated two days after the file was saved, about a person whose
   audit is exactly at that file's time. Kept for linking only (D-019).
4. **Testing the tests.** The grader counted one missing step as nine; a deliberately broken copy
   revealed it. Every bug got a regression test, and I checked that the bug test fails on the old code.
5. **Rejecting the best-scoring idea.** 45% of real answers lacked an account check. Assigning the
   unnamed accounts by case-number order scored 8 right / 1 wrong on known answers — but a case number is
   an id, not evidence, so I rejected it and added only the evidence-based rule (D-035). Precision stayed
   100%, recall rose a little. In an audit, a wrong record in someone's answer is worse than a missing one.
6. **Honesty about my own mistakes.** I once wrote "all tests pass" in the log before reading the
   result — two tests had failed. I added a correction entry (the log is append-only) and changed my
   habit: read the result, then report.

---

## 6. Numbers cheat sheet

| | |
|---|---|
| Requests / evidence files | 518 / 94 (7 readers, 6 log styles) |
| Sightings / coverage notes | 2,914 / 371 |
| Output | 357 INCOMPLETE, 151 COMPLETE, 10 NOT_APPLICABLE |
| Samples | status 100%, precision 100%, recall 90.7%, exact 18/32 |
| Own hard cases | 21/21 |
| Tests / decisions | 300 / 35 |
| AI | gemini-3.1-flash-lite, 2 jobs, 32 calls once, ~97k tokens, final run 0 calls |
| Determinism | byte-identical output across runs, with or without an API key |

## 7. If they ask for a demo

```bash
python3 code/main.py      # ~2 s, writes output.csv + run record + warnings report
python3 code/check.py     # all checks PASS
```
Open `code/DECISIONS.md` at any D-number they ask about; open `code/evaluation/evaluation_report.md`
section 6 for the failures-and-fixes table.
