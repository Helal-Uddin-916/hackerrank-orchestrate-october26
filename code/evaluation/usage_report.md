# Usage report

Token usage and cost of the AI model behind the final `output.csv`. Source of the numbers: the trace
of every AI call (`code/audit/llm.py`), the token counts stored with each saved answer in
`code/llm_cache/`, and `code/evaluation/run_record.md` for the final run.

## 1. Provider and model

| Provider | Model | Used for | Prompt (version) |
|---|---|---|---|
| Google (Gemini API, free tier) | `gemini-3.1-flash-lite` | Transcribing the 15 screenshots and PDFs | `extract_document` (v1) |
| Google (Gemini API, free tier) | `gemini-3.1-flash-lite` | Checking whether each request text asks for deletion | `check_requests` (v2) |

One model is used for both jobs. Everything else in the agent (all other evidence formats, identity,
time rules, policy, step planning) is plain code with no AI.

## 2. Final full-dataset run (the run that produced `output.csv`)

All AI answers come from the saved cache, so the run makes no live AI call.

| Prompt | Live calls | Cache hits | Input tokens billed | Output tokens billed |
|---|---|---|---|---|
| extract_document v1 | 0 | 15 | 0 | 0 |
| check_requests v2 | 0 | 16 | 0 | 0 |
| **Total** | **0** | **31** | **0** | **0** |

Cost of the final run: **$0.00**. Tokens per request in the final run: **0**.

## 3. One-time cost of creating the saved answers

These calls created the cached answers that the final run reuses. Token counts are those reported
by the API and stored with each answer.

| Prompt | Calls | Input tokens | Output tokens | Thinking tokens | Total tokens |
|---|---|---|---|---|---|
| extract_document v1 (15 files) | 15 | 24,448 | 18,110 | 0 | 42,558 |
| check_requests v2 (16 batches for the 398 distinct dataset texts + 1 batch for the 21 hard-case texts) | 17 | 29,017 | 25,269 | 0 | 54,286 |
| **Total (one model)** | **32** | **53,465** | **43,379** | **0** | **96,844** |

The answers the final run reuses (15 documents + 16 request batches) were made with 52,384 input and
42,081 output tokens; the hard-case batch (1,081 input, 1,298 output) is used only by the tests.

Per request (518 requests answered in `output.csv`): **187 tokens** on average (103 input, 84 output),
counting all 32 calls. Per call: 1,671 input and 1,356 output tokens on average.

### Estimated cost

The free tier was used, so the actual cost was **$0.00**. At the standard paid price for
`gemini-3.1-flash-lite` (Google's pricing page: $0.25 per 1M input tokens, $1.50 per 1M output tokens,
thinking included):

| | Input | Output | Total |
|---|---|---|---|
| Creating the saved answers | $0.0134 | $0.0651 | **$0.0784** |
| Per request (÷ 518) | | | **$0.00015** |
| Final run | | | **$0.00** |

## 4. Other AI calls made during development (not in the cache)

| Call | Count | Tokens | Cost | Why it is not used |
|---|---|---|---|---|
| OpenAI `gpt-6-luna`, extract_document v1 | 1 | 0 (refused: no API credit) | $0.00 | Provider changed to Gemini free tier (D-025) |
| Gemini `check_requests` v1 | 17 | not recorded separately; about the same as v2 (~54,000) | $0.00 (free tier); ~$0.08 at paid price | v1 wrongly flagged ordinary wording; replaced by v2 and its answers removed |
| Google ListModels (model names only) | 1 | none | $0.00 | Choosing the model |

## 5. Controls that keep usage small

- AI only for the two jobs plain code cannot do; every answer validated against a strict JSON schema.
- Answers cached by a fingerprint of prompt id + version + model + input; a changed prompt never reuses
  an old answer, and an unchanged one is never paid for twice.
- Request texts sent in batches of 25 (16 calls for the 398 distinct dataset texts instead of 398).
- Calls paced under the account's 10 requests per minute; per-run caps on calls (60) and tokens
  (600,000); bounded retries; quota and billing errors are not retried.
- Without an API key the agent runs from the saved answers only.

Pricing source: Gemini Developer API pricing, https://ai.google.dev/gemini-api/docs/pricing
