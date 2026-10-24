# Phase 4.5b: Hand trace of the log-cited sample cases

Purpose: learn what log events mean from official answers before writing the log reader.
No personal data here: people are named by user_id, records by resource_id.
Traced with a masked scratch tool built on `code/audit/readers/log_lines.py`.

Cases traced (all 8 samples citing log files):

| Case | Status | Cited log |
|---|---|---|
| case_03 | COMPLETE | evidence_94 `resource_lookup_trace_20260926_103856.log` |
| case_10 | INCOMPLETE | evidence_61 `support_replica.log` |
| case_12 | INCOMPLETE | evidence_01 `service_events_ap_south_20260915.log` |
| case_15 | INCOMPLETE | evidence_01 |
| case_17 | INCOMPLETE | evidence_01 |
| case_18 | INCOMPLETE | evidence_94 |
| case_19 | INCOMPLETE | evidence_01 |
| case_20 | INCOMPLETE | evidence_94 |

## How log lines link to a person

1. Directly, through identifier fields: `correlation_key`, `lookup_key`, `subject_key`,
   `match_key`, `subject_ref`, `subject_reference`, `customer_reference`, `email`,
   `customer_email`, `requester_email`, `matched_value`. Values are emails, hashes or user ids.
   The literal text `null` means "no value".
2. Through the trace: a `*_started` line names the person (`lookup_key` / `subject_key`), and the
   `*_completed` line with the same `trace_id` names the record. Example, case_17:
   `restore_policy_check_started subject_key=<person>` → same trace →
   `restore_candidate_evaluated resource_id=backup_3245`.

## What each event means (confirmed by the official answers)

| Event | Fields that matter | Meaning | Confirmed by |
|---|---|---|---|
| `profile_lookup_completed` | `http_status=404`, `rows=0` | account ABSENT | case_03, 12, 15, 17, 19 |
| | `http_status=200`, `rows=1` | account PRESENT | (328 lines; no sample) |
| `index_query_completed` | `result=hit` | search entry PRESENT | case_18, 19, 20 |
| `audit_record_read_completed` | `rows=1`, `retention_until` | security record PRESENT (kept) | case_03, 12, 15, 17, 19 |
| `analytics_profile_read_completed` | email/phone/hash all `null` | analytics ANONYMIZED | case_03 |
| | email present | analytics EXPOSED | case_18 |
| `support_ticket_read_completed` | requester email present | support message EXPOSED | case_18 |
| `message_pii_scan` | `matched_value` = the person's email | support message EXPOSED | case_19 |
| `connector_heartbeat` | `enabled=true`, `scheduler_registered=true` | data flow ACTIVE | case_15 |
| `connector_config_read_completed` | `enabled=true` | data flow ACTIVE | case_18, 20 |
| `restore_candidate_evaluated` | `eligible=true`, `deletion_block=false` | restore ALLOWED (risk) | case_17 |
| `contact_lookup_completed` (analytics_platform) | `email_opt_in=true`, `rows=1` | record must be **deleted**, not anonymized | case_12 |
| `contact_query_completed` + `deletion_marker_check` (support_replica) | `rows=1`, `marker_found=false` | replica copy must be **deleted**, not redacted | case_10 |

Expected answers also verify records that are **not in the cited log** (e.g. case_18 and
case_20 verify an account and a security record that evidence_94 does not mention). INCOMPLETE
verification covers the person's full final state, gathered from all evidence.

## Value combinations across all 26 logs (counts)

- Connectors: `enabled` and `scheduler_registered` always agree (24 both true, 5 both false).
  `connector_config_read_completed`: 23 `enabled=true/delivered`, 11 `enabled=false/stopped`.
- Restore: `restore_candidate_evaluated` always `eligible=true, deletion_block=false` (8);
  `restore_authorization_completed` always `decision=deny` (5);
  `restore_policy_read_completed` `restore_allowed=false` (11) / `true` (4).
- Exports: `available=true` in all 13 export lines.
- `email_opt_in=true` appears once (case_12). `deletion_marker_check` appears once (case_10);
  two other `contact_query_completed` lines in support_replica.log have no marker check.
- Log exports (`export_started` / `export_completed`) report expected versus delivered events and
  dropped counts; logs never give "checked everything" coverage, so absence is only taken from
  explicit lookups (404, `rows=0`, `NOT_FOUND`), in line with D-008.
- Noise (no record state): connection pools, retries, cache lookups without a resource, timers,
  certificates, disk latency, scheduler leases and similar.

## Decisions needed (raised with the user one at a time)

1. Linking through `trace_id` (started line names the person, completed line names the record).
2. `email_opt_in=true` on an analytics contact lookup: treat as a marketing contact (rule_10,
   DELETE) instead of an analytics profile (rule_07, ANONYMIZE). Evidence: case_12.
3. A support replica copy with `marker_found=false`: delete it (the upstream deletion never
   arrived) instead of redacting. Evidence: case_10.
4. Connector state when `enabled` and `scheduler_registered` disagree (never happens in the
   data): ACTIVE if either is true (the less safe state, as in D-009).
