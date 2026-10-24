# Run record

How the latest `output.csv` was made. Rerunning with the same data, code and saved AI answers
gives a byte-identical `output.csv` (see `output_sha256`).

| Item | Value |
|---|---|
| started_at | 2026-10-24T22:44:35Z |
| dataset_fingerprint | 47ba4199732b31cc63e16283f03294d0c542eb7926f96bea82d3013351f0edc4 |
| code_fingerprint | 355943bad99ba62b735c551deaf0bd3870d41221860a529c5a1c48b02143da80 |
| requests | 518 |
| output_sha256 | 45a9af1e900bdb6ebdff749b7153e46c27d60e45d10a4634771d934d8a4fe58c |
| ai_provider | google |
| ai_model | gemini-3.1-flash-lite |
| ai_mode | saved answers only (no LLM_API_KEY) |

## Prompts

| Prompt | Version |
|---|---|
| extract_document | 1 |
| check_requests | 2 |

## Statuses

| Status | Count |
|---|---|
| COMPLETE | 151 |
| INCOMPLETE | 357 |
| NOT_APPLICABLE | 10 |

## AI usage in this run

| Prompt | Live calls | Cache hits | Input tokens | Output tokens |
|---|---|---|---|---|
| check_requests v2 | 0 | 16 | 27936 | 23971 |
| extract_document v1 | 0 | 15 | 24448 | 18110 |
| **total** | 0 | 31 | 52384 | 42081 |

Token counts of cache hits are those recorded when the answer was first made; a cache hit costs nothing in this run.

## Evidence readers

| Reader | Files |
|---|---|
| ai_document | 15 |
| handover_text | 1 |
| log_events | 26 |
| record_list_json | 5 |
| service_export_json | 11 |
| snapshot_bundle_json | 12 |
| sql_snapshot | 24 |
