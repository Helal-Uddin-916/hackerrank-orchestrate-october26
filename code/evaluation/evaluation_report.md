# Evaluation report

Ghost Data Still Exists — deletion-audit agent. All numbers below are produced by the code in this
repository and can be reproduced with the commands at the end. Design: `code/DESIGN.md`; every rule
and its evidence: `code/DECISIONS.md` (D-001 … D-035).

## 1. Summary

| Measure (32 public samples) | Result |
|---|---|
| Case-level status accuracy | **100.0%** (32/32) |
| Step precision (steps written that are in the answer) | **100.0%** — no wrong step written |
| Step recall (answer steps that were found) | **90.7%** |
| Step F1 | 95.1% |
| Sequence accuracy (whole step list exactly right, in order) | 56.2% (18/32) |
| Exact case match (status and steps) | 56.2% (18/32) |
| Own hard cases (21) | **21/21** correct |
| Full run on 518 requests | 518 valid rows; 357 INCOMPLETE, 151 COMPLETE, 10 NOT_APPLICABLE |
| Determinism | 3 runs (one without an API key) gave a byte-identical `output.csv` |

Every sample miss has one cause: 18 records that no participant-visible evidence links to the
person without guessing (section 3.3). The agent is designed to leave a record out rather than add a
wrong one, so precision is 100% and the misses show up only as recall.

## 2. How the agent is evaluated

| Tool | What it checks |
|---|---|
| `code/validate_output.py` | Contract format: header, 518 rows in case-number order, allowed statuses, operations and states, step numbering, phase and operation order, citation rules, cited ids exist and are not newer than `audit_at`. `main.py` refuses to save an `output.csv` that fails it. |
| `code/evaluation/main.py` | Grader: status accuracy, step precision/recall/F1 (order-free, `step_no` ignored), field accuracy per field (position by position), sequence accuracy, exact match, per-case missing and extra steps. |
| `code/evaluation/custom_cases.py` | 21 hand-built hard cases (section 4), written as code, each with its expected answer and the trap it tests. |
| `code/tests/` | 300 unit tests: every reader on all real files of its format, every rule, the grader and checker themselves, regression tests for each bug in section 6. |
| `code/check.py` | One-command quality gate: tests, hard-case build, `output.csv` format, agent on samples (floors: status 100%, precision 100%, recall ≥ 90%), agent on hard cases (all exact). |

Grader definitions: step precision/recall compare whole steps without `step_no`, so one missing step
counts once. Field accuracy compares step *i* with step *i*, so a missing step also lowers the
position-based fields of every later step in that case.

## 3. Results on the 32 public samples

### 3.1 Status

| Expected → predicted | Cases |
|---|---|
| COMPLETE → COMPLETE | 10 |
| INCOMPLETE → INCOMPLETE | 22 |

### 3.2 Steps and fields

| Measure | Result |
|---|---|
| Step precision / recall / F1 | 100.0% / 90.7% / 95.1% |
| Sequence accuracy | 56.2% |
| Field accuracy: step_no / phase | 90.7% / 90.7% |
| Field accuracy: system_id / resource_id | 74.6% / 74.6% |
| Field accuracy: operation / state | 77.7% / 77.7% |
| Field accuracy: evidence_id | 90.2% |

For the 93 sample records the agent links, the state, the finding/fix/check and the cited file match
the sample answer in every case (93/93 states, 93/93 operation triples, 65/65 required citations).

### 3.3 What is missed and why

| Cause | Records | Cases |
|---|---|---|
| Record carries no identifier; its only trace is a cleanup-job row whose timing fits 2–6 requests equally, and no file about exactly one of them shows it (D-028, D-035: not linked, warning instead) | 15 | 03, 18, 20, 21 (2), 22, 23, 25, 26, 27, 28 (2), 29, 31, 32 |
| Record appears in no evidence file and no activity row at all | 3 | 04 (`account_004`), 18 (`audit_record_6645`), 20 (`audit_record_5569`) |

Linking those 15 would mean picking between people who finished their requests at the same minute
(e.g. case_18 and case_20 both report at 11:00 with one unnamed account each). Linking to all
candidates instead was measured to add hundreds of wrong steps across the 518 requests.

### 3.4 What these numbers do not prove

The hidden answers for the 518 requests are not available, so their accuracy cannot be measured. The
sample scores are optimistic: several rules were derived from the samples (D-021, D-022, D-027 to
D-031, D-035), so the samples are not an independent test, and 32 cases is a small set. One measurable
warning sign: every sample answer contains an account check, but only 282 of our 508 real deletion
answers do. Most of the others have an unnamed account whose cleanup timing fits several requests
(D-028), and we chose not to guess. Expected effect on the hidden score: step precision should stay
high by design; status accuracy should stay high but can drop where a record we could not link was
still present; step recall and exact matches will be lower than on the samples (our estimate: recall
roughly 75-85%).

## 4. Own evaluation cases (21)

Built by `code/evaluation/custom_cases.py` as a small dataset with the real layout (text, JSON bundle,
JSON export and SQL evidence). `code/tests/test_custom_cases.py` checks that each case still contains
its trap; `code/check.py` runs the agent on them.

| Case | Situation tested | Expected | Result |
|---|---|---|---|
| case_901 | Request uses an old email | INCOMPLETE | exact |
| case_902 | Request email has capitals and spaces | COMPLETE | exact |
| case_903 | Clean person, messy neighbour in the same file | COMPLETE | exact |
| case_904 | Same person, first of two requests | INCOMPLETE | exact |
| case_905 | Gone before the audit date, back again after it | COMPLETE | exact |
| case_906 | Decisive note saved exactly at the audit time | COMPLETE | exact |
| case_907 | Activity results lie both ways | INCOMPLETE | exact |
| case_908 | Primary is clean but a read replica still has the row | INCOMPLETE | exact |
| case_909 | Newest inventory did not cover the search system | INCOMPLETE | exact |
| case_910 | Same person, second request after the clean-up | COMPLETE | exact |
| case_911 | Backup that can still be restored | INCOMPLETE | exact |
| case_912 | Sync connection still on, plus a leftover search entry | INCOMPLETE | exact |
| case_913 | Two files disagree about the same moment (worse state + warning) | INCOMPLETE | exact |
| case_914 | Tombstone still holding only the email hash | INCOMPLETE | exact |
| case_915 | Security record whose keep-until date has passed | warning only (D-013) | warning raised |
| case_916 | Order record that fits none of the rules (side report) | COMPLETE | exact |
| case_917 | Non-deletion request in Spanish | NOT_APPLICABLE | exact |
| case_918 | "No longer" is not a refusal | COMPLETE | exact |
| case_919 | "Do not close the account" | NOT_APPLICABLE | exact |
| case_920 | Evidence file tries to give the agent orders (prompt injection) | INCOMPLETE | exact |
| case_930 | Request text tries to give the agent orders (prompt injection) | NOT_APPLICABLE | exact |

## 5. Full run on the 518 requests

| Status | Rows |
|---|---|
| INCOMPLETE | 357 |
| COMPLETE | 151 |
| NOT_APPLICABLE | 10 |

- Format check: 0 problems. Run time about 2 seconds; 0 live AI calls (all AI answers from the saved
  cache); details in `code/evaluation/run_record.md`.
- Request check: the word check and the AI check agree on all 550 request texts (518 + 32 samples);
  the 10 NOT_APPLICABLE requests are the 10 texts asking for a correction, a copy, information,
  login help or a marketing opt-out.
- 8 requests have nothing provable at `audit_at` (their evidence is all later, or no record is
  linked) and are answered INCOMPLETE, never COMPLETE without proof (D-032).
- Everything a person should look at is listed in `code/evaluation/warnings_report.md`.

## 6. Failures found and improvements made

Each item was found by a test, a measurement against the samples, or a real run; each fix has a test.

| # | Failure found | How it was found | Improvement | Effect |
|---|---|---|---|---|
| 1 | Grader counted one removed step as 9 missing (renumbering shifted every later step) | Grading a deliberately broken copy | Compare steps without `step_no`; regression test | One missing step counts once |
| 2 | "Latest note wins" alone let a file saved after `audit_at` decide state (notes inside it were stamped at `audit_at`) | Writing hard case 5 against sample case_27 | D-017: only files saved by `audit_at` decide state; later files only link | case_27 cites the right file |
| 3 | Ledger files name their person field `subject_reference`; every ledger row lost its link | New reader test on links | Read both field names; regression test | All ledger rows linked |
| 4 | Shared record code would have merged primary and replica tables into one copy | Code review before shipping | Explicit copy names | Replica-only rows found (case_16) |
| 5 | Rows dated two days after their own file (case_135, case_383 traps) | Reader self-check rejected them | D-019: keep for linking only | Post-audit facts never change answers |
| 6 | AI client retried "no credits" errors 4 times | First real AI call | Read the error type; never retry quota or billing errors | Fails fast with a clear message |
| 7 | AI left PDF times split ("10 24 UTC") | Checking extractions | Repair dates in code, not in the prompt | Reliable keep-until dates |
| 8 | 15 kept security records wrongly marked gone by snapshots about other people | Measuring against the samples | D-027: "checked, not there" counts only for people the file is about | 84/84 linked records correct |
| 9 | A months-old "not found" lookup linked a second account to case_17 | Measuring extra records | D-028: keep only the most recent lookup-only account | 0 extra records |
| 10 | Citations across copies picked the lowest file number instead of the newest proof | Measuring citations | Cite the most recent proof first (D-029) | Citations 59/64 → 64/64 |
| 11 | Treating all SQL dumps as complete copies marked 127 kept records gone in the real run | Warning counts on the 518 requests | D-030: only plain single-table copies are complete | 127 → 34 (remaining are timing, see 7) |
| 12 | After 11, case_23 lost its required "verify kept" step (measurement script missed it, tests caught it) | Unit tests | D-031: unproven required end-state checks | Samples back to 90.2% recall |
| 13 | 8 deletion requests had no valid answer | Real run | D-032: INCOMPLETE when nothing can be proven | 0 undecided |
| 14 | Word check left 4 natural deletion phrasings unclear | Running it on all texts | Added general phrasings ("close permanently", "remove that search result") | 0 unclear |
| 15 | AI prompt v1 flagged ordinary customer wording as prompt injection | Measuring the AI on its own before use | Prompt v2 defines the flag narrowly; v1 answers removed from the cache | 0 false flags; full agreement with the word check |
| 16 | Saved AI transcriptions held ~33 raw (fictional) emails and names, the only raw identifiers stored anywhere | Inspecting code.zip before submission | D-034: fingerprint tokens before caching; existing answers converted | 0 emails/phones in the cache; output byte-identical |
| 17 | Scrubbing a second time re-fingerprinted fingerprints | New idempotence test | Tokens are never touched again; regression test | Scrubbing is safe to repeat |
| 18 | 45% of real deletion answers had no account check (every sample answer has one) | Comparing real output with the samples | Investigated 223 ambiguous accounts; tested ordering by case number (8 right / 1 wrong) and by request time (5 / 4) and rejected both as guesses; added the only evidence-based rule, file co-occurrence (D-035) | Recall 90.2% → 90.7%, precision still 100% |

## 7. Known limitations

- **Unlinkable records:** 15 sample records are left out because their owner cannot be told apart
  from other requests without guessing, and 3 appear in no evidence. On the real requests the same
  situation produces warnings listing the candidate requests.
- **Timing of security records:** for 34 real requests an inventory taken minutes before the security
  record was written cannot prove that record present at `audit_at`. This changes no status; COMPLETE
  answers leave out a check they cannot prove (D-031) and the case is in the warnings report.
- **Screenshots and PDFs** are transcribed once by an AI model (cached); only real record identifiers
  become evidence. Orders, tickets and files without record identifiers go to the side report.
- **Assumptions without a sample to confirm them** are labelled ASSUMPTION in `DECISIONS.md` (for
  example the system name `marketing_service`, the 90/10-minute cleanup window in D-028).
- **AI on the free tier:** Google may use free-tier prompts to improve its products; acceptable here
  only because every person in the dataset is fictional.

## 8. Reproduce

```bash
python3 code/main.py                       # writes output.csv and the two reports in code/evaluation/
python3 code/check.py                      # quality gate: all checks must PASS
python3 -m unittest discover -s code/tests
python3 code/evaluation/main.py <answers.csv> dataset/sample_output.csv --details
```
