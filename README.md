# Ghost Data Still Exists — deletion-audit agent

For every request in `dataset/deletion_requests.csv`, the agent decides whether the person really asked
for deletion, finds every identifier they ever had, reconstructs the state of each of their records at
the request's `audit_at` from 94 evidence files (SQL snapshots, JSON exports, logs, a text handover,
screenshots and PDFs), applies `dataset/deletion_rules.csv`, and writes `output.csv`: `COMPLETE` with
the checks that prove it, `INCOMPLETE` with findings, an ordered remediation plan and the checks that
must pass afterwards, or `NOT_APPLICABLE`. Reported statuses and activity results are treated as claims;
only evidence decides.

## Quick start

Requirements: Python 3.9 or newer. No third-party packages (`code/requirements.txt` is empty on purpose).

```bash
python3 code/main.py
```

This writes, in about two seconds:

| File | Content |
|---|---|
| `output.csv` (repo root) | 518 rows `case_id,deletion_status,steps_json`, sorted by case number; checked against the format rules before it is saved |
| `code/evaluation/warnings_report.md` | Cases and records a person should review (ids only, no personal data) |
| `code/evaluation/run_record.md` | How this output was made: data and code fingerprints, output SHA-256, model, prompt versions, counts, AI usage |

No API key is needed. AI is used for two jobs only (transcribing the 15 screenshots/PDFs, and checking
request texts), and its answers are saved in `code/llm_cache/`. Without a key the agent reads those saved
answers and makes no AI call; the output is byte-identical either way.

### Optional: making new AI calls

Only needed if the evidence images or request texts change. Copy `.env.example` to `.env` (never
committed) and set:

```
LLM_PROVIDER=google
LLM_MODEL=gemini-3.1-flash-lite
LLM_API_KEY=<your Gemini API key>
```

The code only accepts the allowlisted model and only answers missing from the cache are requested.

## Tests and quality gate

```bash
python3 code/check.py                       # one command: everything below, PASS or FAIL
python3 -m unittest discover -s code/tests  # 300 unit tests
python3 code/validate_output.py             # format check of output.csv
python3 code/evaluation/main.py output.csv dataset/sample_output.csv   # grader (use sample answers)
```

`code/check.py` also runs the agent on the 32 public samples (status and step precision must stay at
100%, recall at least 90%) and on 21 own hard cases (all must be right).

## Results

| 32 public samples | |
|---|---|
| Status accuracy | 100% |
| Step precision / recall / F1 | 100% / 90.7% / 95.1% |
| Own hard cases | 21/21 |

Full details, the failures found and the improvements made: `code/evaluation/evaluation_report.md`.
Tokens and cost: `code/evaluation/usage_report.md` (final run: 0 AI calls; creating all saved answers:
about 97,000 tokens, $0 on the free tier, about $0.08 at the paid price).

## How it works

```text
dataset/ ──► readers (one per format) ──► sightings + coverage   (built once)
                                              │
request ──► request check (word rules + AI must agree) ──► NOT_APPLICABLE if not clearly deletion
   │
   └──► find the person (current + historical identifiers)
          └──► find their records: link by identifier / log trace / cleanup timing,
               keep only files saved by audit_at, latest observation wins, replicas separate
                 └──► apply deletion_rules.csv per record (delete, redact, anonymize, keep, block, disable)
                        └──► write status + ordered steps with citations ──► output.csv
```

- `code/DESIGN.md` — architecture, data shapes, design principles, production checklist.
- `code/DECISIONS.md` — 35 decisions, each with the evidence for it (or labelled ASSUMPTION).
- Plain code makes every decision; the AI only transcribes images and reads request texts, and every
  AI answer is checked against a strict schema. Evidence text is treated as data, never as
  instructions (tested with prompt-injection cases).

## Repository layout

```text
code/
  main.py                 entry point
  check.py                quality gate
  validate_output.py      output format checker
  DESIGN.md, DECISIONS.md
  audit/                  the agent
    readers/              one reader per evidence format (SQL, JSON x3, logs, text, images/PDFs)
    sighting.py           shared data shapes          identity.py   find the person
    evidence.py           read all evidence           assemble.py   records and their state at audit_at
    activity_link.py      records without identifiers policy.py     apply deletion_rules.csv
    planner.py            status and ordered steps    request_check.py  deletion or not
    llm.py, prompts.py    AI client and versioned prompts
    catalog.py, settings.py
  llm_cache/              saved AI answers (answer and token usage only; identifiers stored as fingerprints)
  evaluation/             grader, own hard cases, evaluation and usage reports, run record, warnings
  tests/                  unit tests
```

## Packaging

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

`code.zip` holds this README and `code/` only: no dataset, no `.env`, no virtual environment.
