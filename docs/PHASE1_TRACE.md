# Phase 1 — Hand trace of two sample cases

Purpose: reproduce two `sample_output.csv` answers by hand from participant-facing files only,
and record what the trace teaches about linking, time, policy and step planning.
No personal data appears here: subjects are named by `user_id`, records by `resource_id`.

Cases traced:
- `case_11`: INCOMPLETE, 4 findings / 4 remediations / 6 verifications, 6 systems.
- `case_27`: COMPLETE, 6 verifications, 6 systems (includes backup and data flow).

Method: resolve request email -> `user_id` (current snapshot first, then `user_audit_log.csv`
email changes); collect current + historical email/phone/hash; sweep every text evidence file
and `system_activity.csv` for those identifiers and the `user_id`; then read the matching
files in full. Masking tools lived in the session scratchpad and are not part of the solution.

---

## case_11 (expected INCOMPLETE)

- Request: explicit "erased" -> deletion authorized. `reported_status=COMPLETE` (a false claim).
- Subject: `user_32`, matched on current email. No audit-log changes.
- `audit_at` 2026-10-01T12:00:00Z.
- Only evidence mentioning the subject: `evidence_03`
  (`records/customer_account_handover_20261001.txt`, captured 2026-10-01T12:00:00Z = audit_at).
- No `system_activity.csv` row mentions the subject's identifiers.

`evidence_03` is a pipe-delimited list: `time | system | resource | DATA_CATEGORY | state | identifier`.
It lists ~40 rows for many people; 6 link to the subject (5 by email, 1 by `user_32`).
Every one of the 6 is in the expected answer, and nothing else is.

| resource | category (from file) | state at audit | rule | finding | remediation | verification |
|---|---|---|---|---|---|---|
| account_service/account_4248 | USER_PROFILE | NOT_FOUND | rule_01 DELETE | — (already compliant) | — | VERIFY_RECORD_ABSENT |
| search_service/search_052 | SEARCH_INDEX_ENTRY | FOUND | rule_03 DELETE | REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT |
| analytics_platform/analytics_2034 | ANALYTICS_PROFILE | IDENTIFIABLE | rule_07 ANONYMIZE | REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED |
| support_platform/support_864 | SUPPORT_MESSAGE | IDENTIFIABLE | rule_05 REDACT | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
| integration_service/data_flow_9522 | DATA_FLOW | ACTIVE | (recreation path) | REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |
| security_service/audit_record_9394 | SECURITY_AUDIT_LOG | FOUND | rule_12 RETAIN | — | — | VERIFY_RECORD_PRESENT |

Assembling the steps with the problem-statement sort (phase -> operation-table order ->
system_id -> resource_id) reproduces the expected 14 steps exactly. Findings cite `evidence_03`;
remediation and verification have `evidence_id=""`.

## case_27 (expected COMPLETE)

- Request: "confirmation that the account was fully removed" -> treated as deletion (the
  request already happened; the person asks for an audit of it). `reported_status=COMPLETE`.
- Subject: `user_27`, matched on current email. No audit-log changes.
- `audit_at` 2026-09-15T12:00:00Z.
- Evidence mentioning the subject:
  - `evidence_92` (`records/service_resource_export_20260915_114455.json`, captured 11:44:55,
    before audit_at). Links only by `customer_reference`/`subject_reference = user_27`;
    the subject's email/phone/hash do not appear.
  - `evidence_02` (`records/customer_data_snapshot_bundle_20260917.json`, exported 2026-09-17,
    AFTER audit_at). Holds 11 inner snapshots with their own `captured_at`: one 09-09, one
    09-15T09:00, nine at 09-15T12:00 (= audit_at), one 09-17.
- No `system_activity.csv` row mentions the subject's identifiers; rows reference resources only.

| resource | state at audit (evidence_92) | how it links to user_27 | expected step |
|---|---|---|---|
| account_service/account_3866 | 404, `profile: null` (observed 09:59:48) | **not in evidence_92**; see Issue A | VERIFY_RECORD_ABSENT |
| security_service/audit_record_3830 | present, `retention_until` 2034-09-30 | subject_reference | VERIFY_RECORD_PRESENT |
| support_platform/support_4707 | requester email/name/phone all null | customer_reference | VERIFY_RECORD_REDACTED |
| analytics_platform/analytics_4574 | all identifiers null | **not in any text evidence**; see Issue A | VERIFY_RECORD_ANONYMIZED |
| integration_service/data_flow_2295 | `enabled: false`, `stopped` | customer_reference | VERIFY_DATA_FLOW_DISABLED |
| backup_service/backup_5970 | `restore_allowed: false` | customer_reference | VERIFY_RESTORE_BLOCKED |

All six steps cite `evidence_92`. None cite `evidence_02`, which was captured after audit_at.

The 09-17 snapshot in `evidence_02` shows `account_3866` **ACTIVE with full PII**: the account
was recreated after audit_at. The expected answer is still COMPLETE, which confirms that
later state must not change the answer.

---

## Observations (inputs to Phase 2 DESIGN/DECISIONS; not yet decisions)

1. **Some evidence labels the data category directly.** `evidence_03` carries
   `USER_PROFILE`, `SEARCH_INDEX_ENTRY`, ... per row. JSON exports label it by section
   (`profile_lookups`, `profiles`, `restore_entries`, `connectors`, `index_queries`,
   `audit_records`, `tickets`). The record-to-rule mapping (open question 1) can start from these.
2. **State vocabulary seen so far:** `NOT_FOUND` / HTTP 404 / `profile: null` -> absent;
   `FOUND` -> present; `IDENTIFIABLE` -> identifiers exposed; `ANONYMIZED`, `REDACTED`,
   `BLOCKED`, `DISABLED`, `ACTIVE`; `enabled` and `restore_allowed` booleans; null identifier fields.
3. **Mapping observed (state x rule -> steps):**
   - must-delete and present -> REPORT_REMAINING_RECORD / DELETE_RECORD / VERIFY_RECORD_ABSENT;
   - must-delete and absent -> VERIFY_RECORD_ABSENT only;
   - redact/anonymize and identifiers exposed -> REPORT_EXPOSED_PII / REDACT or ANONYMIZE / VERIFY_*;
   - redact/anonymize already done -> VERIFY_* only;
   - retain -> VERIFY_RECORD_PRESENT only;
   - active flow -> REPORT_ACTIVE_DATA_FLOW / DISABLE_DATA_FLOW / VERIFY_DATA_FLOW_DISABLED;
   - disabled flow / blocked backup -> VERIFY_* only.
   Verification covers every relevant record, including ones already compliant.
4. **The cutoff includes audit_at itself.** `evidence_03` (captured == audit_at) is cited,
   and nine `evidence_02` inner snapshots are stamped exactly audit_at.
5. **The post-audit snapshot is ignored for state** (`account_3866` recreated 09-17).
6. **Activity results lie both ways.** activity_3529 says DELETE `account_5482` FAILED, yet
   `evidence_92` shows 404 and `case_28` expects VERIFY_RECORD_ABSENT.
7. **Decoys are everywhere.** Both files list many other people's FOUND/IDENTIFIABLE/ACTIVE
   records (e.g. other search entries, active connectors keyed by other emails). Only rows
   linked to the subject count.
8. **No ACCOUNT_TOMBSTONE** in either case, although the account is deleted and a security
   record is retained. Tombstones appear only in samples that have an `identity_service` record.
9. **rule_12's retention condition is not visible in `evidence_03`** (no `retention_until`
   there), yet the record is retained. `evidence_92` does show `retention_until` (2034).
10. **rule_05's "shared context" condition is not shown** for `support_864`; REDACT (not DELETE)
    is still expected.
11. **One citation per case.** In both cases a single file covers every step; the cited file
    is the one at or before audit_at that holds the state.
12. **Ordering:** no downstream-before-source conflict arises in these two cases; plain
    operation-table order reproduces both answers.

## Issue A: linking records that carry no identifier (new open question)

In `case_27`, two expected records cannot be linked to the subject from evidence available
at or before audit_at:

- `account_3866`: at audit_at it is a 404 with no profile. The only link to `user_27` is
  the 09-17 snapshot in `evidence_02` (captured after audit_at).
- `analytics_4574`: every identifier is null in both files. The only connection is
  `activity_3533` (ANONYMIZE_RECORD, deletion_worker, 2026-08-30), which names the resource
  but not the person. The same 08-30 batch also processed `user_28`'s resources
  (`case_28` expects `analytics_6077`, `support_2351`, `data_flow_2337`, `backup_838`), so
  the batch alone does not separate the two people. Elimination against other subjects'
  linked resources does.

Possible approaches (to decide with the user):
1. Use post-audit evidence **for identity linking only** (who owns a resource), never for
   state or citation. Covers `account_3866`, not `analytics_4574`.
2. Treat `deletion_worker` activity in the request window (requested_at..reported_at) as a
   linking lead, resolved by elimination against resources already linked to other
   subjects. Covers both, but is heuristic and order-dependent.
3. Both, documented as ASSUMPTION with tests built from case_26/27/28 (three subjects in one batch).

## Other checks done in Phase 1

- Evidence counts on disk match the handoff: 24 sql, 28 json, 26 log, 11 png, 4 pdf, 1 txt;
  94 manifest rows; no missing paths.
- `pdftotext` and `tesseract` are not installed; PDF/PNG extraction needs a tool decision.
- Screenshot `deleted_account_reference_20260310.png` (a customer-reference registry page for
  another subject) was viewed; it does not bear on these two cases.
- `problem_statement.md` in the repo was read in full. Earlier pasted copies are not
  available in this session, so a diff was not possible. One wording to note: it says
  "Findings and verification steps for `COMPLETE` cases must cite..." while AGENTS.md §6.6
  says findings always cite and COMPLETE verifications cite.
