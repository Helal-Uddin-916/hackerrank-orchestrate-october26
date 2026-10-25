"""The only code that talks to the AI provider (D-024, DESIGN.md section 5).

What it guarantees:
- Only allowed models are called (settings.ALLOWED_MODELS), checked before any request.
- Answers must match a strict JSON schema; anything else is an error and is never cached.
- Answers are cached by a fingerprint of prompt id + version + model + schema + inputs; a cached
  answer is reused with no call. Only the answer and token usage are stored, never inputs or keys.
- Calls are paced under the requests-per-minute limit, time out, and retry a bounded number of
  times on rate limits and server errors.
- Per-run caps on calls and tokens stop runaway cost.
- Every call (or cache hit) adds a trace record: purpose, prompt version, model, tokens, latency.
- The API key is only ever placed in a request header (never the URL); it never appears in
  errors, traces or cache files.

Each provider is a small adapter (request shape, answer and usage parsing, retry rules); everything
above is shared. Supported: OpenAI Responses API, Google Gemini generateContent.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from audit.settings import ALLOWED_MODELS

OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}
# A 429 with these error codes is about money or limits on the account, not speed: never retry.
NO_RETRY_ERROR_CODES = {"insufficient_quota", "billing_hard_limit_reached"}


class LLMError(RuntimeError):
    """An AI call failed or returned something unusable."""


class BudgetExceeded(LLMError):
    """The per-run call or token cap would be exceeded."""


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt. Change the version whenever the instructions or schema change."""
    prompt_id: str
    version: str
    instructions: str
    schema: dict


@dataclass(frozen=True)
class Part:
    """One input part: kind is 'text', 'image' or 'pdf'."""
    kind: str
    data: object  # str for text, bytes for image/pdf
    mime: str = ""
    filename: str = ""

    def fingerprint(self):
        raw = self.data.encode("utf-8") if isinstance(self.data, str) else self.data
        return hashlib.sha256(self.kind.encode() + b"\0" + raw).hexdigest()

    def to_openai(self):
        if self.kind == "text":
            return {"type": "input_text", "text": self.data}
        encoded = base64.b64encode(self.data).decode("ascii")
        if self.kind == "image":
            return {"type": "input_image", "image_url": f"data:{self.mime or 'image/png'};base64,{encoded}"}
        if self.kind == "pdf":
            return {"type": "input_file", "filename": self.filename or "document.pdf",
                    "file_data": f"data:application/pdf;base64,{encoded}"}
        raise LLMError(f"unknown part kind {self.kind!r}")

    def to_gemini(self):
        if self.kind == "text":
            return {"text": self.data}
        mime = {"image": self.mime or "image/png", "pdf": "application/pdf"}.get(self.kind)
        if mime is None:
            raise LLMError(f"unknown part kind {self.kind!r}")
        return {"inlineData": {"mimeType": mime, "data": base64.b64encode(self.data).decode("ascii")}}


def validate(schema, value, path="$"):
    """Check value against the small JSON-schema subset our prompts use; raise LLMError if it fails."""
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    type_ok = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None, None: True,
    }
    if not any(type_ok.get(k, False) for k in kinds):
        raise LLMError(f"{path}: expected {kinds}")
    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"{path}: value not in allowed list")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        missing = [k for k in schema.get("required", []) if k not in value]
        if missing:
            raise LLMError(f"{path}: missing {missing}")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise LLMError(f"{path}: unexpected fields {sorted(set(value) - set(props))}")
        for key, sub in props.items():
            if key in value:
                validate(sub, value[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(schema["items"], item, f"{path}[{i}]")


def urllib_transport(url, headers, body, timeout):
    """Default transport: one HTTPS POST. Returns (status, response_headers, body_bytes)."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


def _error(raw):
    try:
        error = json.loads(raw).get("error", {})
        return error if isinstance(error, dict) else {}
    except (ValueError, AttributeError):
        return {}


def _json_answer(texts):
    if not texts:
        raise LLMError("response had no output text")
    try:
        return json.loads("".join(texts))
    except ValueError:
        raise LLMError("output text is not valid JSON")


class OpenAIAdapter:
    """OpenAI Responses API with strict json_schema answers."""

    def url(self, model):
        return OPENAI_URL

    def headers(self, api_key):
        return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    def body(self, settings, prompt, parts):
        return {
            "model": settings.model,
            "instructions": prompt.instructions,
            "input": [{"role": "user", "content": [p.to_openai() for p in parts]}],
            "text": {"format": {"type": "json_schema", "name": prompt.prompt_id, "schema": prompt.schema, "strict": True}},
            "reasoning": {"effort": settings.reasoning_effort},
            "max_output_tokens": settings.max_output_tokens,
        }

    def answer(self, data):
        if data.get("status") not in (None, "completed"):
            reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise LLMError(f"response not completed (status {data.get('status')}, reason {reason})")
        texts = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "refusal":
                    raise LLMError("the model refused to answer")
                if content.get("type") == "output_text":
                    texts.append(content.get("text", ""))
        return _json_answer(texts)

    def usage(self, data):
        usage = data.get("usage") or {}
        details = usage.get("output_tokens_details") or {}
        return {
            "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "output_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            "reasoning_tokens": int(details.get("reasoning_tokens", 0) or 0),
        }

    def no_retry(self, raw):
        error = _error(raw)
        return (error.get("code") or error.get("type")) in NO_RETRY_ERROR_CODES

    def retry_delay(self, headers, raw):
        for name, value in (headers or {}).items():
            if name.lower() == "retry-after":
                try:
                    return min(float(value), 60.0)
                except (TypeError, ValueError):
                    return None
        return None


class GeminiAdapter:
    """Google Gemini generateContent with responseJsonSchema answers. Key goes in x-goog-api-key."""

    def url(self, model):
        return GEMINI_URL.format(model=model)

    def headers(self, api_key):
        return {"Content-Type": "application/json", "x-goog-api-key": api_key}

    def body(self, settings, prompt, parts):
        return {
            "systemInstruction": {"parts": [{"text": prompt.instructions}]},
            "contents": [{"role": "user", "parts": [p.to_gemini() for p in parts]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": prompt.schema,
                "maxOutputTokens": settings.max_output_tokens,
            },
        }

    def answer(self, data):
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMError(f"the request was blocked ({block})")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("response had no candidates")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish not in (None, "STOP"):
            raise LLMError(f"response not completed (finishReason {finish})")
        parts = (candidate.get("content") or {}).get("parts") or []
        return _json_answer([p.get("text", "") for p in parts if "text" in p and not p.get("thought")])

    def usage(self, data):
        meta = data.get("usageMetadata") or {}
        thoughts = int(meta.get("thoughtsTokenCount", 0) or 0)
        return {
            "input_tokens": int(meta.get("promptTokenCount", 0) or 0),
            "output_tokens": int(meta.get("candidatesTokenCount", 0) or 0) + thoughts,  # thinking is billed as output
            "reasoning_tokens": thoughts,
        }

    def no_retry(self, raw):
        # A daily quota will not come back by waiting; per-minute limits will.
        for detail in _error(raw).get("details") or []:
            for violation in detail.get("violations") or []:
                if "PerDay" in str(violation.get("quotaId", "")):
                    return True
        return False

    def retry_delay(self, headers, raw):
        for detail in _error(raw).get("details") or []:
            delay = str(detail.get("retryDelay", ""))
            if delay.endswith("s"):
                try:
                    return min(float(delay[:-1]), 60.0)
                except ValueError:
                    return None
        return None


ADAPTERS = {"openai": OpenAIAdapter(), "google": GeminiAdapter()}


class LLMClient:

    def __init__(self, settings, cache_dir, transport=urllib_transport, clock=time.monotonic, sleep=time.sleep):
        if settings.model not in ALLOWED_MODELS.get(settings.provider, set()):
            raise LLMError(f"model {settings.model!r} for provider {settings.provider!r} is not allowed")
        if settings.provider not in ADAPTERS:
            raise LLMError(f"provider {settings.provider!r} is not supported")
        self.adapter = ADAPTERS[settings.provider]
        self.settings = settings
        self.cache_dir = cache_dir
        self.transport, self.clock, self.sleep = transport, clock, sleep
        self.trace = []
        self.calls = 0
        self.tokens = 0
        self._last_call_at = None

    # ---- public -------------------------------------------------------------------------
    def ask(self, prompt, parts, purpose, transform=None):
        """Return the validated JSON answer for prompt + parts, from cache or from one API call.

        transform(answer) runs after validation and before caching: what is cached and returned is the
        transformed answer (used to keep personal identifiers out of the cache, D-034).
        """
        key = self._cache_key(prompt, parts)
        cached = self._read_cache(key)
        if cached is not None:
            self.trace.append(self._record(purpose, prompt, key, cached["usage"], 0.0, cache_hit=True))
            return cached["answer"]

        if not self.settings.api_key:
            raise LLMError("LLM_API_KEY is not set (put it in .env)")
        if self.calls >= self.settings.max_calls_per_run:
            raise BudgetExceeded(f"call cap of {self.settings.max_calls_per_run} reached")
        if self.tokens >= self.settings.max_tokens_per_run:
            raise BudgetExceeded(f"token cap of {self.settings.max_tokens_per_run} reached")

        started = self.clock()
        data = self._post(self.adapter.body(self.settings, prompt, parts))
        latency = self.clock() - started
        usage = self.adapter.usage(data)
        self.calls += 1
        self.tokens += usage["input_tokens"] + usage["output_tokens"]
        answer = self.adapter.answer(data)
        validate(prompt.schema, answer)
        if transform is not None:
            answer = transform(answer)
            validate(prompt.schema, answer)
        self._write_cache(key, prompt, answer, usage)
        self.trace.append(self._record(purpose, prompt, key, usage, latency, cache_hit=False))
        return answer

    def summary(self):
        """Totals for usage_report.md."""
        live = [t for t in self.trace if not t["cache_hit"]]
        return {
            "provider": self.settings.provider, "model": self.settings.model,
            "calls": len(live), "cache_hits": len(self.trace) - len(live),
            "input_tokens": sum(t["input_tokens"] for t in live),
            "output_tokens": sum(t["output_tokens"] for t in live),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in live),
        }

    # ---- request ------------------------------------------------------------------------
    def _pace(self):
        gap = 60.0 / max(1, self.settings.requests_per_minute)
        if self._last_call_at is not None:
            wait = self._last_call_at + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last_call_at = self.clock()

    def _post(self, body):
        headers = self.adapter.headers(self.settings.api_key)
        url = self.adapter.url(self.settings.model)
        payload = json.dumps(body).encode("utf-8")
        last_problem = "no attempt made"
        for attempt in range(self.settings.max_retries + 1):
            self._pace()
            try:
                status, response_headers, raw = self.transport(url, headers, payload, self.settings.timeout_seconds)
            except (OSError, urllib.error.URLError) as error:
                status, response_headers, raw = None, {}, b""
                last_problem = f"network error: {type(error).__name__}"
            if status == 200:
                try:
                    return json.loads(raw)
                except ValueError:
                    raise LLMError("provider returned a body that is not JSON")
            if status is not None:
                last_problem = f"HTTP {status}: {self._error_message(raw)}"
                if status not in RETRY_STATUSES or self.adapter.no_retry(raw):
                    raise LLMError(last_problem)
            if attempt < self.settings.max_retries:
                retry_after = self.adapter.retry_delay(response_headers, raw) if status is not None else None
                self.sleep(retry_after if retry_after is not None else 2 ** attempt)
        raise LLMError(f"gave up after {self.settings.max_retries + 1} attempts; last: {last_problem}")

    @staticmethod
    def _error_message(raw):
        try:
            message = json.loads(raw).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        return (message or "no message")[:200]

    # ---- cache and trace ----------------------------------------------------------------
    def _cache_key(self, prompt, parts):
        material = json.dumps({
            "prompt_id": prompt.prompt_id, "version": prompt.version, "model": self.settings.model,
            "schema": prompt.schema, "parts": [p.fingerprint() for p in parts],
        }, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cache_path(self, key):
        return os.path.join(self.cache_dir, f"{key}.json")

    def _read_cache(self, key):
        path = self._cache_path(key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _write_cache(self, key, prompt, answer, usage):
        os.makedirs(self.cache_dir, exist_ok=True)
        record = {"prompt_id": prompt.prompt_id, "prompt_version": prompt.version,
                  "model": self.settings.model, "answer": answer, "usage": usage}
        tmp = self._cache_path(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, sort_keys=True)
        os.replace(tmp, self._cache_path(key))

    def _record(self, purpose, prompt, key, usage, latency, cache_hit):
        return {"purpose": purpose, "prompt_id": prompt.prompt_id, "prompt_version": prompt.version,
                "model": self.settings.model, "cache_key": key[:16], "cache_hit": cache_hit,
                "latency_seconds": round(latency, 3), **usage}
