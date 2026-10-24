# DECISIONS

Purpose: every decision with its evidence (file, column, or rule text), or labeled ASSUMPTION if unverified.

## D-001 Sync with upstream starter repo
Decision: fast-forward `main` and `dev` to `origin/main` (commit 0eddbbd).
Evidence: the only upstream change fixes the submission URL typo in AGENTS.md (`wwww.` -> `www.`). User approved.

## D-002 Where planning documents live
Decision: `DESIGN.md` and `DECISIONS.md` live in `code/`, so the standard packaging command
(`zip -r code.zip README.md code ...`) includes them. Session working notes (`docs/CONTEXT.md`,
`docs/PHASE1_TRACE.md`) stay outside `code/` and are not shipped.
Evidence: README.md "Packaging" builds code.zip from `README.md` and `code/` only; README.md
"Evaluation" says content must be in source or Markdown to be visible to the code judge and AI
interviewer; AGENTS.md §6.9 interview covers architecture and approach. User approved.

## D-003 Cache image/PDF extraction results and ship them
Decision: extract text/facts from PNG/PDF evidence once, save the results inside `code/`, commit
them, and have normal runs read the saved results instead of calling a model again. Only the
`.cache/` and `.pytest_cache/` tool folders stay ignored. Exact location and format: Phase 4.
Evidence: AGENTS.md §6.7 "Keep behavior deterministic where possible"; model calls cost money and
can vary between runs. Dataset people are fictional (AGENTS.md §7), so saved extractions hold no
real personal data. User approved.

## D-004 Python version
Decision: use the system `python3` (3.9.6) in `.venv`; code must stay compatible with Python 3.9.
Evidence: README.md expected run command `python3 code/main.py`. User approved.

## D-005 Linking records that carry no identifier (Issue A in docs/PHASE1_TRACE.md)
Decision: use both linking methods, each labelled ASSUMPTION and covered by tests built from
sample cases 26, 27 and 28.
- (a) Evidence captured after audit_at may be used only to learn who owns a resource, never
  for the resource's state and never as a citation.
- (b) deletion_worker activity between requested_at and reported_at is a linking lead, kept
  only after removing resources already linked to other people.
Evidence: case_27 expects account_3866 and analytics_4574, which have no subject link in any
evidence at or before audit_at (docs/PHASE1_TRACE.md, Issue A). ASSUMPTION: the problem files
do not say which method is intended. User approved option 3.

## D-006 Row order in output.csv
Decision: sort output rows by the number in case_id (case_33, case_34, ... case_550), not as text.
Evidence: dataset/deletion_requests.csv is already in this order (518 unique ids, 33 to 550,
no gaps); case_ids have no leading zeros, so text order would differ (case_100 before case_33).
The rule "sorted by case_id" (problem_statement.md, AGENTS.md §6.3) does not say which order.
ASSUMPTION: the grader expects number order. User approved.

## D-007 Which sighting of a record to trust (REVISED by D-017)
Decision: a record's state at audit_at is its latest sighting at or before audit_at (a sighting
at exactly audit_at counts). Sightings after audit_at are ignored for state and never cited.
"Sighting time" is the time on the observation itself when there is one, not the file's time.
Evidence: case_27 expects account_3866 ABSENT (404 at 2026-09-15T09:59:48Z) although a
2026-09-17 snapshot shows it ACTIVE; case_11 cites evidence_03 captured exactly at audit_at
(docs/PHASE1_TRACE.md). AGENTS.md §6.2 "Use only information available at or before audit_at".
User approved.

## D-008 "Not seen" is not "gone"
Decision: a record counts as absent only when evidence actually checked that place and found
nothing: an explicit NOT_FOUND, a 404 / null lookup, or an inventory whose snapshot_scope covers
that system and has no row for the record. If no evidence checked that system, the state is
unknown and we keep looking in other evidence.
Evidence: SQL inventory snapshots carry snapshot_scope listing only the systems captured
(docs/CONTEXT.md §3); case_27 proves absence with a 404 lookup and case_11 with NOT_FOUND
(docs/PHASE1_TRACE.md). User approved.

## D-009 Two sightings disagree at the same moment
Decision: when evidence about the same record at the same time disagrees, take the less safe
state (data present, identifiers exposed, flow active, restore allowed). The record is then
reported and fixed rather than wrongly called compliant. The evidence step will count how often
this happens, and the count goes into the evaluation report.
Evidence: no such conflict seen yet in Phase 1 (ASSUMPTION that it may occur). Rationale:
AGENTS.md §6.5 says a deletion is COMPLETE only when every copy is proven compliant. User approved.

## D-010 Deciding whether a request authorizes deletion
Decision: two independent checks. (1) An LLM reads request_text and answers deletion yes/no.
(2) A deterministic word check (deletion words in several languages, minus explicit "do not
delete / not asking for deletion / only marketing" style limits) gives a second opinion.
Agreement: use it. Disagreement: list the request for human review, and record each human
decision with its reason in this file. LLM answers are cached per distinct text.
Evidence: 518 requests use 375 distinct texts; texts in 6 languages; 10 clearly non-deletion
texts found (correction, export/copy, information request, marketing opt-out, login help).
"I no longer use this account ... delete it" defeats naive negation checks. User approved.

## D-011 Which evidence file a step cites
Decision: each step cites the file holding the sighting used for that record's state (D-007).
If several files hold that sighting at exactly the same time, cite the one with the lowest
file_id number. Remediation and future verification steps for INCOMPLETE cases cite "".
Evidence: sample_output.csv cites per record (case_04 cites evidence_54 for the account and
evidence_56 for analytics); in samples the other files mentioning cited records were captured
after audit_at, so no sample shows a tie (tie rule is an ASSUMPTION for repeatability).
User approved.

## D-012 Deletion request but no evidence found about the person (deferred; RESOLVED by D-032)
Decision: no output rule yet. The program lists such cases as warnings; we investigate each by
hand (most likely a search or identity-matching gap) and fix the cause. If a case still has no
evidence after that, we decide its output then, using that real example, and record it here.
Evidence: COMPLETE needs cited verification, INCOMPLETE needs cited findings, NOT_APPLICABLE is
only for non-deletion requests (AGENTS.md §6.3, §6.6), so no status fits an empty result.
User approved.

## D-013 Retention period or legal hold already ended at audit_at (flag, don't guess)
Decision: always compare retention_until / hold dates with audit_at. Still active: RETAIN
(rule_12 / rule_13) and verify present. Already ended: no rule says what to do, so the program
flags a warning instead of guessing; we decide from the real example if one appears.
Evidence: deletion_rules.csv rule_12 and rule_13 only cover active retention/holds; every end
date seen so far (SQL inventories, two PDFs, evidence_92) is after every audit_at. User approved.

## D-014 Support attachment that is shared or of unclear ownership (flag, don't guess)
Decision: an attachment that belongs only to the deletion subject is DELETE (rule_06). If it is
shared with other people, or ownership cannot be established, the program flags a warning instead
of guessing; we decide from the real example if one appears.
Evidence: deletion_rules.csv rule_06 condition "Attachment belongs only to the deletion subject";
no rule covers shared attachments. User approved.

## D-015 Records whose type fits none of the 15 rules
Decision: no step is produced for a record whose type matches no data_category in
deletion_rules.csv (e.g. orders, finance records). The program lists such records in a side
report so they are visible for review, never silently dropped.
Evidence: deletion_rules.csv has 15 categories and none covers orders/finance; no sample answer
includes such records (sample_output.csv); a screenshot shows orders and finance records linked
to a customer reference. User approved.

## D-016 Matching records to rules (first draft)
Decision: a record's rule comes from, in order: (1) a category label written in the evidence
(e.g. record_type / pipe-text column / log token such as SEARCH_INDEX_ENTRY); (2) the section,
table or collection name; (3) the resource_id prefix:

| resource_id prefix | data_category (rule) | required action |
|---|---|---|
| account_ | USER_PROFILE (rule_01) | DELETE |
| search_ | SEARCH_INDEX_ENTRY (rule_03) | DELETE |
| export_ | USER_EXPORT (rule_09) | DELETE |
| analytics_ | ANALYTICS_PROFILE (rule_07) | ANONYMIZE if attributable |
| support_ | SUPPORT_MESSAGE (rule_05) or SUPPORT_ATTACHMENT (rule_06), see below | REDACT / DELETE |
| audit_record_ | SECURITY_AUDIT_LOG (rule_12) | RETAIN within retention (D-013) |
| legal_hold_ | LEGAL_HOLD_RECORD (rule_13) | RETAIN while hold active (D-013) |
| backup_ | BACKUP_ARCHIVE (rule_14) | RETAIN, restore must be blocked |
| tombstone_ | ACCOUNT_TOMBSTONE (rule_15) | REDACT (keep stable user_id only) |
| data_flow_ | not a data category: recreation path | disable if active |

support_ records: use the evidence's own clue: collection support_conversations -> message,
ticket_attachments -> attachment, log event attachment_pii_scan -> attachment, or an explicit
SUPPORT_MESSAGE / SUPPORT_ATTACHMENT label. No clue: treat as SUPPORT_MESSAGE and flag a warning
(redacting keeps the record, so it is the smaller mistake).
Not yet seen in any text evidence: CACHE_ENTRY, MARKETING_PROFILE, EMAIL_DELIVERY_LOG,
PRODUCT_TELEMETRY, SECONDARY_IDENTIFIER; revisit after image/PDF extraction.
Evidence: read-only inventory of all 94 evidence files (prefixes consistent across SQL, JSON, logs
and text; labels record_type / collection_name / log tokens); sample_output.csv system_ids.
ASSUMPTION: prefix fallback is reliable where no label exists. User approved.

## D-017 Which files may decide a record's state (revises D-007)
Decision: two filters, in this order.
1. Eligible files: only evidence files whose manifest captured_at is at or before audit_at.
   A file saved later did not exist on audit day, even if notes inside it carry earlier times.
2. Winning sighting: among sightings from eligible files, the one with the latest own time
   (observed_at) at or before audit_at decides the state. A sighting exactly at audit_at counts.
Files saved after audit_at may be used only to learn who owns a resource (D-005), never for
state and never as a citation.
Evidence: case_27 audit_at 2026-09-15T12:00:00Z. evidence_02 (captured 2026-09-17) holds inner
snapshots stamped exactly 2026-09-15T12:00:00Z; under D-007 alone those would win and be cited,
but sample_output.csv cites evidence_92 (captured 2026-09-15T11:44:55Z) for all six steps.
AGENTS.md §6.6 "Never cite ... an evidence item captured after the case's audit_at".
Found while writing hard test case 5. User approved.

## D-018 A stable user_id reference left in a record is not exposed personal data
Decision: customer_reference / subject_reference (a user_<n> id) is a permitted stable reference.
A record counts as EXPOSED only if an email, phone, name or email hash is still filled; a record
with only the user_id reference counts as REDACTED (support) or ANONYMIZED (analytics) or a
compliant tombstone. The reference is still used to link the record to the person.
Evidence: sample case_27 expects VERIFY_RECORD_REDACTED for support_4707, which in evidence_92
has customer_reference user_27 and null email/name/phone; rule_15 lets a tombstone keep the stable
user_id; across all 11 export files, 11 support tickets have only a customer_reference.

## D-019 A row dated after its own file was saved is kept for linking only
Decision: such a row becomes a sighting marked future_dated, plus a reader note. Like evidence
saved after audit_at (D-017), it may only help learn who owns a resource (D-005); it never
decides a record's state and is never cited. A snapshot newer than its file gives no coverage.
Evidence: evidence_13 (connector_configurations_20260812.json, manifest captured_at
2026-08-12T09:00:00Z) has 1 of 39 rows dated 2026-08-14T09:00:00Z: account_1417 FOUND, belonging
to the person in case_135 (audit_at 2026-08-12T09:00:00Z, equal to the file time). No other file
mentions account_1417. Using its state would let post-audit information change case_135
(AGENTS.md §6.2). User chose this option over rejecting the row or trusting its date.

## D-020 Log lines link to a person through a shared trace_id (same file only)
Decision: a log line without an identifier is linked to the person named on another line with
the same trace_id in the same file (typically a *_started line with lookup_key / subject_key and a
*_completed line with the resource_id). Trace ids are never matched across files.
Evidence: sample case_17 expects REPORT_RESTORE_RISK for backup_3245; in evidence_01 the line
naming backup_3245 (restore_candidate_evaluated) has no identifier, and only its trace_id ties it
to restore_policy_check_started, which carries the person's email (docs/PHASE4_LOG_TRACE.md).
Same pattern in cases 12, 15, 19. User approved.

## D-021 A record used as an opted-in marketing contact is a MARKETING_PROFILE (delete)
Decision: when evidence shows a record answering a contact lookup with email_opt_in=true (seen on
analytics_platform), its category is MARKETING_PROFILE (rule_10, DELETE) instead of
ANALYTICS_PROFILE (rule_07, ANONYMIZE). Steps: REPORT_REMAINING_RECORD / DELETE_RECORD /
VERIFY_RECORD_ABSENT, with the record's own system_id kept.
Evidence: sample case_12 expects REPORT_REMAINING_RECORD, DELETE_RECORD and VERIFY_RECORD_ABSENT
for analytics_9914; evidence_01 shows contact_lookup_completed for analytics_9914 with
email_opt_in=true, linked to the person by an email-hash correlation_key. Only line of its kind in
all 26 logs (docs/PHASE4_LOG_TRACE.md). User approved.

## D-022 A legacy contact-index / replica copy with a missing deletion marker is a SECONDARY_IDENTIFIER (delete)
Decision: when a replica or legacy contact index still returns a person's record and the deletion
marker for it is missing (marker_found=false), the copy is a leftover identifier copy:
SECONDARY_IDENTIFIER (rule_02, DELETE), not SUPPORT_MESSAGE (rule_05, REDACT). Steps:
REPORT_REMAINING_RECORD / DELETE_RECORD / VERIFY_RECORD_ABSENT, keeping the record's system_id.
Evidence: sample case_10 expects REPORT_REMAINING_RECORD, DELETE_RECORD and VERIFY_RECORD_ABSENT
for support_platform/support_002; evidence_61 (support_replica.log, source legacy_contact_index)
shows contact_query_completed rows=1 and deletion_marker_check marker_found=false for support_002,
last written 2026-09-07, after the 2026-08-31 request. The two other copies in that log belong to
no known person. User approved.

## D-023 A connector is ACTIVE if either enabled or scheduler_registered is true
Decision: for connector log events reporting both flags, the data flow is FLOW_ACTIVE if
enabled=true OR scheduler_registered=true, and FLOW_DISABLED only if both are false. If the two
flags disagree, the reader adds a warning note.
Evidence: across all 26 logs the flags always agree (24 both true, 5 both false), so this changes
no current answer; sample case_15 expects REPORT_ACTIVE_DATA_FLOW for data_flow_9846 with both
true. Rationale: a connector that is switched off but still scheduled can still recreate data;
same "assume the less safe state" principle as D-009. User approved.

## D-024 AI provider and model: OpenAI gpt-6-luna, locked down (REVISED by D-025)
Decision: LLM_PROVIDER=openai, LLM_MODEL=gpt-6-luna for both AI jobs (reading images/PDFs, checking
request texts). The OpenAI project allows only this model and has a small budget limit (set by the
user); the code also refuses any model not on its own allowlist (defense in depth). Calls use the
Responses API with strict JSON-schema answers, low reasoning effort, bounded retries, a timeout,
pacing under the account's 10 requests/minute, per-run call and token caps, and a trace record per
call. Answers are cached by fingerprint of prompt id + version + model + schema + input (D-003),
and only the answer and usage are stored, never the input content or the key.
Evidence: OpenAI model docs list gpt-6-luna with text+image input and structured outputs, at
$0.10 / $0.50 per 1M input/output tokens; gpt-5.4-mini has the same features at $0.75 / $4.50. The
user's rate-limit table gives gpt-6-luna 60,000 TPM and 10 RPM. User approved.

## D-025 AI provider and model: Google gemini-3.1-flash-lite on the free tier (revises D-024)
Decision: LLM_PROVIDER=google, LLM_MODEL=gemini-3.1-flash-lite for both AI jobs. All D-024
safeguards stay (code allowlist, strict JSON-schema answers via responseJsonSchema, cache, pacing,
bounded retries, caps, trace, key only in a header). The client now has one adapter per provider;
Gemini specifics: key in the x-goog-api-key header, thinking tokens counted as output, a daily
quota error is never retried, a per-minute quota error waits for the server's retryDelay.
Evidence: the first real OpenAI call (D-024) was refused with HTTP 429 "You have no credits
remaining", and the user wants a free option. Google's pricing page lists a free tier for Flash
models; the model page lists image and PDF input and structured outputs for Flash-Lite, and Google's
migration guide recommends 3.1 Flash-Lite for new work. The user's AI Studio limits: 15 requests per
minute and 500 per day for gemini-3.1-flash-lite, against 20 per day for the Flash models; the key's
model list (ListModels, 46 generateContent models) includes models/gemini-3.1-flash-lite.
Trade-off, stated to the user: on the free tier Google may use prompts and responses to improve its
products; acceptable here only because every person in the dataset is fictional (AGENTS.md section 7).
User approved the switch to Gemini.

## D-026 Finding the person behind a request
Decision: compare emails after trimming and lowercasing; a request email matches a person by
their current email first, otherwise by any old or new email in user_audit_log.csv. The person's
identifiers are all current and historical emails, phones and email hashes (as fingerprints) plus
the stable user_id, including changes after audit_at. No match, or several people matching, gives a
warning and no guess.
Evidence: all 518 requests match exactly one person (100 only through a historical email, 20 only
after trimming/lowercasing); no email in the dataset ever belonged to two people, and no request
email changed after its audit_at (read-only checks, Phase 2); the email hash equals the email
fingerprint for all 850 identities. Hard cases case_901, case_902 and case_904/910 cover old,
messy and repeated emails. User approved the approach.

## D-027 "Checked, not there" only counts for people the file is about (refines D-008)
Decision: a coverage entry (a snapshot, inventory or table that checked a place) proves a record
ABSENT only for a person that the same file is about, i.e. some sighting in that file links to the
person. Snapshot and replica files are extracts about specific people, not complete copies of a
system. Replica tables remain separate copies; all other stores of a system form its one live copy.
Evidence: with D-008 alone, 15 security records that sample answers keep were wrongly marked ABSENT
by later snapshots about other people (e.g. case_27 audit_record_3830 is PRESENT at 09:31 in
evidence_92, but evidence_51's 12:00 security snapshot, about the case_31/32 people, does not list
it). With the person scope, every directly linked sample record (84) gets the expected state and
every required citation (61) matches the sample answer; evidence_51's empty account snapshot still
proves absence for the people it is about (case_31, case_32).

## D-028 Linking records without identifiers through cleanup-job timing (makes D-005 concrete)
Decision: a record that no evidence links to anyone is linked to a request only if a deletion_worker
activity row on it falls between 90 minutes before and 10 minutes after that request's reported_at,
and exactly one request fits after removing requests whose person already has a directly linked
record of the same kind (same id prefix). If several requests fit, nothing is linked and each
fitting request gets a warning naming the record (human review, no guess). Separately, when a person
has more than one account linked only through "not found" lookups, only the most recently observed
one is kept, with a warning.
Evidence: of 27 sample records without identifiers, 24 have one deletion_worker row in that window
around their own request's report; 3 appear nowhere. The window alone fits 2-6 requests for 16 of the
24 (symmetric pairs such as case_18 and case_20, which report at the same minute), so only 8 can be
linked without guessing. The plain request-to-report window fits 3+ requests for most rows. case_17's
extra account_6523 is linked only by a March "not found" lookup of the person's email, while the
sample expects account_4092; 2 of 550 requests have two such accounts. User chose option (a).

## D-029 SQL snapshot files are complete copies (refines D-027); cite the most recent proof (NARROWED by D-030)
Decision: coverage from SQL snapshot files (primary, replica, failover, inventory tables) proves a
record ABSENT for everyone, because each file is a complete copy of its tables; the "only for people
the file is about" scope of D-027 applies to snapshot bundles and record lists, which are extracts.
When several copies of a record are in the same state, the most recent proof is cited, with the
lowest file number only as the final tie-break (D-011).
Evidence: sample case_01 and case_02 expect VERIFY_RECORD_ABSENT citing evidence_54
(customer_accounts_failover_replica_20260902.sql), where the account is in neither table and the
person is not otherwise mentioned. With this rule all 92 linkable sample records have the expected
state and all 64 required citations match (before: 59, because ties across copies picked the lowest
file number instead of the newest proof, e.g. evidence_10 of 2026-08-27 instead of evidence_54 of
2026-09-02). No previously correct state changed.

## D-030 Only plain single-table SQL copies are complete (narrows D-029)
Decision: absence proven for everyone comes only from the plain single-table SQL copies
(customer_profiles_primary, customer_profiles_failover_replica, search_documents_primary). The
multi-collection replica dumps (customer_data_* tables) and inventories are extracts about specific
people, like bundles and record lists, so their "checked, not there" counts only for people the file
is about (D-027). The citation rule of D-029 (most recent proof across copies) stays.
Evidence: with D-029, 127 must-keep security records among the 518 requests came out ABSENT because a
replica dump or inventory about other people did not list them (e.g. case_34: audit_record_3753 seen
PRESENT on 2026-09-15 in evidence_01, then missing from the 2026-09-21 replica dump evidence_05). With
D-030 the samples are unchanged (92/92 states, 64/64 citations; case_01/02 still proven by the plain
table file evidence_54) and the count drops to 34. Those 34 are timing: e.g. case_65's inventory
(08:37) predates the security record, first seen at 08:58 in a log saved after audit_at (09:00).
They change no status (kept records never create findings) and stay as warnings; known limitation.

## D-031 A linked record with no evidence at audit_at gets only its required end-state check
Decision: when a record is linked to the person but no eligible evidence shows its state at audit_at,
the policy still gives its required end-state check (VERIFY_RECORD_PRESENT for kept records,
VERIFY_RECORD_ABSENT for must-delete records, and so on), marked unproven, with no finding. An
INCOMPLETE plan includes it with evidence_id ""; a COMPLETE plan cannot prove it, so it is left out
with a warning.
Evidence: sample case_23 (INCOMPLETE) expects VERIFY_RECORD_PRESENT for audit_record_4128, which has
no evidence before its audit_at once D-030 stopped a replica dump about other people from marking it
ABSENT. INCOMPLETE verification steps describe the required state after remediation and carry no
proof (AGENTS.md section 6.6). Samples: status 100%, step precision 100%, recall 90.2%.

## D-032 A deletion request that nothing can prove is INCOMPLETE (resolves D-012)
Decision: when no step of a deletion request can be proven at audit_at, the answer is INCOMPLETE,
never COMPLETE: its steps are the required end-state checks of any linked records (evidence_id ""),
or [] when no record is linked; a warning goes to the warnings report. The same applies when no
person matches the request email. Our own output checker therefore accepts an INCOMPLETE row without
a FINDING and with zero steps (those two checks were our additions, not contract rules); COMPLETE
still needs at least one cited verification step.
Evidence: 8 of 518 requests are genuine deletion requests (texts read, masked) with no provable step:
case_46, case_50, case_53, case_61 have linked records whose evidence all postdates their early
audit_at; case_63, case_265, case_267, case_271 have no linked record. Answering COMPLETE without proof
is the failure the challenge describes (problem_statement.md); AGENTS.md section 6.5 allows COMPLETE
only when every copy is proven. User chose this option.

## D-033 A request that goes to review is answered NOT_APPLICABLE, with a warning
Decision: the agent treats a request as a deletion request only when the word check and the AI check
both say so (D-010). "review" (unclear wording, text that tries to steer the audit, or disagreement)
is answered NOT_APPLICABLE with steps [], and the case is listed in the warnings report for a person.
Without an AI client, or if the AI call fails, the word check decides alone and unclear goes to review.
Evidence: the contract asks whether request_text "actually authorizes deletion" (AGENTS.md section 6.5);
if the two independent checks do not clearly agree, deletion is not clearly authorized. In the data,
word check and AI agree on all 550 requests (0 review cases); the only review case is our injection
hard case case_930, whose expected answer is NOT_APPLICABLE. User approved.

## D-034 Saved AI transcriptions hold fingerprints, never raw identifiers
Decision: before an extract_document answer is cached, emails, phones and email hashes become
fingerprint tokens (fp:email:<sha256>, fp:phone:<sha256>, fp:email_hash:<hash>), names become fp:name,
non-record entries lose their names, and facts and event texts the reader does not use are removed
(only the uploader fact and events naming a record or a restore stay). User references (user_<n>),
record ids, dates and status words stay. The reader accepts fingerprint tokens directly. The 15
existing transcriptions were converted with the same function.
Evidence: inspecting code.zip found about 33 fictional dataset emails (and names) copied by the AI
into code/llm_cache, the one place the agent stored raw identifiers. After the change output.csv is
byte-identical (sha256 66326e32...), the cache holds 0 email addresses and 0 phone numbers, and tests
check that scrubbing keeps the reader's results unchanged. AGENTS.md section 7 makes all people
fictional, so this is privacy by design rather than a legal need. User chose this option.

## D-035 An unnamed record seen in a file about exactly one candidate belongs to that candidate
Decision: when cleanup timing leaves several requests that could own a record without identifiers
(D-028), and a file that shows the record is about exactly one of those requests' people (some
sighting in it links to that person), the record is linked to that request. Otherwise nothing changes:
no link, warning. Ordering by case number was tested and rejected (not a real-world signal).
Evidence: of 16 ambiguous sample records with known owners, co-occurrence links 1 (account_3136 →
case_25, via evidence_92) correctly and none wrongly; it resolves 2 of the ambiguous real accounts.
Ordering by case number scored 8 right / 1 wrong / 7 undecidable and ordering by request time 5 right /
4 wrong, so both were rejected as guesses (case numbers are identifiers, not timing). User chose to
keep the precision-first approach and add this rule.
