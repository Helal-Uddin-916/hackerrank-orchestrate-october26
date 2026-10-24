"""Tests for code/audit/llm.py and code/audit/settings.py using a fake provider (no network, no cost).

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import json
import os
import sys
import tempfile
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.llm import BudgetExceeded, LLMClient, LLMError, Part, Prompt, validate  # noqa: E402
from audit.settings import Settings, load_env_file, load_settings  # noqa: E402

SECRET = "sk-test-SECRET-should-never-leak"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["deletion"],
          "properties": {"deletion": {"type": "boolean"}}}
PROMPT = Prompt("check_request", "1", "Answer the question.", SCHEMA)


def ok_body(answer, input_tokens=100, output_tokens=20, reasoning=5):
    return json.dumps({
        "status": "completed",
        "output": [{"type": "reasoning"},
                   {"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "output_tokens_details": {"reasoning_tokens": reasoning}},
    }).encode()


class FakeProvider:
    """Returns queued (status, body) replies and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        status, body, headers_out = (self.replies.pop(0) + ({},))[:3]
        return status, headers_out, body


class FakeClock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make(fake, tmp, **overrides):
    values = dict(provider="openai", model="gpt-6-luna", api_key=SECRET)
    values.update(overrides)
    clock = FakeClock()
    client = LLMClient(Settings(**values), tmp, transport=fake, clock=clock.clock, sleep=clock.sleep)
    return client, clock


class Client(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_answer_is_parsed_validated_and_traced(self):
        fake = FakeProvider((200, ok_body({"deletion": True})))
        client, _ = make(fake, self.tmp.name)
        self.assertEqual(client.ask(PROMPT, [Part("text", "delete me")], "request_check"), {"deletion": True})
        body = fake.requests[0]["body"]
        self.assertEqual((body["model"], body["text"]["format"]["strict"], body["reasoning"]["effort"]),
                         ("gpt-6-luna", True, "low"))
        self.assertEqual(client.summary()["input_tokens"], 100)
        self.assertEqual(client.trace[0]["reasoning_tokens"], 5)

    def test_cache_hit_makes_no_call(self):
        fake = FakeProvider((200, ok_body({"deletion": False})))
        client, _ = make(fake, self.tmp.name)
        client.ask(PROMPT, [Part("text", "same")], "x")
        again, _ = make(FakeProvider(), self.tmp.name)  # a new run, provider has no replies left
        self.assertEqual(again.ask(PROMPT, [Part("text", "same")], "x"), {"deletion": False})
        self.assertEqual((again.summary()["calls"], again.summary()["cache_hits"]), (0, 1))

    def test_cache_key_changes_with_prompt_version(self):
        fake = FakeProvider((200, ok_body({"deletion": True})), (200, ok_body({"deletion": True})))
        client, _ = make(fake, self.tmp.name)
        client.ask(PROMPT, [Part("text", "a")], "x")
        client.ask(Prompt("check_request", "2", PROMPT.instructions, SCHEMA), [Part("text", "a")], "x")
        self.assertEqual(len(fake.requests), 2)

    def test_cache_holds_no_input_and_no_key(self):
        client, _ = make(FakeProvider((200, ok_body({"deletion": True}))), self.tmp.name)
        client.ask(PROMPT, [Part("text", "private request text")], "x")
        for name in os.listdir(self.tmp.name):
            with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("private request text", content)
            self.assertNotIn(SECRET, content)

    def test_disallowed_model_is_refused_before_any_call(self):
        with self.assertRaises(LLMError):
            make(FakeProvider(), self.tmp.name, model="gpt-5.5-pro")

    def test_missing_key_gives_a_clear_error(self):
        client, _ = make(FakeProvider(), self.tmp.name, api_key="")
        with self.assertRaises(LLMError) as ctx:
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertIn(".env", str(ctx.exception))

    def test_rate_limit_is_retried_using_retry_after(self):
        fake = FakeProvider((429, b'{"error": {"message": "slow down"}}', {"Retry-After": "7"}),
                            (200, ok_body({"deletion": True})))
        client, clock = make(fake, self.tmp.name)
        self.assertEqual(client.ask(PROMPT, [Part("text", "x")], "x"), {"deletion": True})
        self.assertIn(7.0, clock.slept)

    def test_gives_up_after_bounded_retries(self):
        fake = FakeProvider(*[(500, b"{}")] * 4)
        client, _ = make(fake, self.tmp.name)
        with self.assertRaises(LLMError) as ctx:
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertEqual(len(fake.requests), 4)
        self.assertIn("gave up", str(ctx.exception))

    def test_no_credits_is_not_retried(self):
        # Found on the first real call: OpenAI answers 429 both for "slow down" and for "no credits".
        body = json.dumps({"error": {"message": "You have no credits remaining.", "type": "insufficient_quota",
                                     "code": "insufficient_quota"}}).encode()
        fake = FakeProvider((429, body))
        client, _ = make(fake, self.tmp.name)
        with self.assertRaises(LLMError) as ctx:
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertEqual(len(fake.requests), 1)
        self.assertIn("no credits", str(ctx.exception))

    def test_client_error_is_not_retried(self):
        fake = FakeProvider((400, b'{"error": {"message": "bad request"}}'))
        client, _ = make(fake, self.tmp.name)
        with self.assertRaises(LLMError):
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertEqual(len(fake.requests), 1)

    def test_key_never_appears_in_errors_or_trace(self):
        fake = FakeProvider(*[(401, json.dumps({"error": {"message": "invalid key"}}).encode())])
        client, _ = make(fake, self.tmp.name)
        with self.assertRaises(LLMError) as ctx:
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, json.dumps(client.trace))
        self.assertNotIn(SECRET, repr(client.settings))
        self.assertEqual(fake.requests[0]["headers"]["Authorization"], f"Bearer {SECRET}")

    def test_answer_breaking_the_schema_is_rejected_and_not_cached(self):
        fake = FakeProvider((200, ok_body({"deletion": "maybe"})))
        client, _ = make(fake, self.tmp.name)
        with self.assertRaises(LLMError):
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_incomplete_and_refused_answers_are_errors(self):
        incomplete = json.dumps({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}).encode()
        refused = json.dumps({"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}).encode()
        for body in (incomplete, refused):
            client, _ = make(FakeProvider((200, body)), self.tmp.name)
            with self.assertRaises(LLMError):
                client.ask(PROMPT, [Part("text", "x")], "x")

    def test_call_cap_stops_runaway_cost(self):
        fake = FakeProvider((200, ok_body({"deletion": True})), (200, ok_body({"deletion": True})))
        client, _ = make(fake, self.tmp.name, max_calls_per_run=1)
        client.ask(PROMPT, [Part("text", "a")], "x")
        with self.assertRaises(BudgetExceeded):
            client.ask(PROMPT, [Part("text", "b")], "x")
        self.assertEqual(len(fake.requests), 1)

    def test_calls_are_paced_under_requests_per_minute(self):
        fake = FakeProvider((200, ok_body({"deletion": True})), (200, ok_body({"deletion": True})))
        client, clock = make(fake, self.tmp.name, requests_per_minute=10)
        client.ask(PROMPT, [Part("text", "a")], "x")
        client.ask(PROMPT, [Part("text", "b")], "x")
        self.assertAlmostEqual(sum(clock.slept), 6.0)

    def test_image_and_pdf_parts_are_sent_as_data_urls(self):
        fake = FakeProvider((200, ok_body({"deletion": True})))
        client, _ = make(fake, self.tmp.name)
        client.ask(PROMPT, [Part("image", b"\x89PNG", mime="image/png"), Part("pdf", b"%PDF", filename="a.pdf")], "x")
        content = fake.requests[0]["body"]["input"][0]["content"]
        self.assertTrue(content[0]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(content[1]["file_data"].startswith("data:application/pdf;base64,"))


class Validation(unittest.TestCase):

    def test_nested_schema(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["rows"], "properties": {
            "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["state"],
                                               "properties": {"state": {"type": "string", "enum": ["FOUND", "NOT_FOUND"]},
                                                              "note": {"type": ["string", "null"]}}}}}}
        validate(schema, {"rows": [{"state": "FOUND", "note": None}]})
        for bad in ({"rows": [{"state": "MAYBE"}]}, {"rows": [{}]}, {"rows": [{"state": "FOUND", "x": 1}]}, {"rows": "no"}):
            with self.assertRaises(LLMError):
                validate(schema, bad)


class SettingsFile(unittest.TestCase):

    def test_env_file_does_not_override_real_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w") as f:
                f.write("# comment\nLLM_PROVIDER=openai\nLLM_MODEL=gpt-6-luna\nLLM_API_KEY='abc'\n")
            environ = {"LLM_MODEL": "from-env"}
            settings = load_settings(environ=environ, env_file=path)
        self.assertEqual((settings.provider, settings.model, settings.api_key), ("openai", "from-env", "abc"))
        self.assertNotIn("abc", str(settings))

    def test_missing_env_file_is_fine(self):
        environ = {}
        load_env_file("/nonexistent/.env", environ)
        self.assertEqual(environ, {})


if __name__ == "__main__":
    unittest.main()


def gemini_body(answer, prompt_tokens=300, output_tokens=40, thoughts=10, finish="STOP"):
    return json.dumps({
        "candidates": [{"finishReason": finish, "content": {"role": "model", "parts": [
            {"text": "thinking...", "thought": True},
            {"text": json.dumps(answer)}]}}],
        "usageMetadata": {"promptTokenCount": prompt_tokens, "candidatesTokenCount": output_tokens,
                          "thoughtsTokenCount": thoughts},
    }).encode()


def gemini_quota_error(quota_id, retry_delay="7s"):
    return json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]}}).encode()


class GeminiClient(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, fake, **overrides):
        values = dict(provider="google", model="gemini-3.1-flash-lite")
        values.update(overrides)
        return make(fake, self.tmp.name, **values)

    def test_request_shape_and_key_in_header_not_url(self):
        fake = FakeProvider((200, gemini_body({"deletion": True})))
        client, _ = self.make(fake)
        client.ask(PROMPT, [Part("text", "hello"), Part("image", b"\x89PNG", mime="image/png"),
                            Part("pdf", b"%PDF", filename="a.pdf")], "x")
        request = fake.requests[0]
        self.assertTrue(request["url"].endswith("/models/gemini-3.1-flash-lite:generateContent"))
        self.assertNotIn(SECRET, request["url"])
        self.assertEqual(request["headers"]["x-goog-api-key"], SECRET)
        self.assertNotIn("Authorization", request["headers"])
        body = request["body"]
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], PROMPT.instructions)
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "hello"})
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(parts[2]["inlineData"]["mimeType"], "application/pdf")
        config = body["generationConfig"]
        self.assertEqual((config["responseMimeType"], config["responseJsonSchema"]), ("application/json", SCHEMA))

    def test_answer_skips_thought_parts_and_counts_thinking_as_output(self):
        client, _ = self.make(FakeProvider((200, gemini_body({"deletion": False}))))
        self.assertEqual(client.ask(PROMPT, [Part("text", "x")], "x"), {"deletion": False})
        record = client.trace[0]
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["reasoning_tokens"]), (300, 50, 10))

    def test_cut_off_answer_is_an_error(self):
        client, _ = self.make(FakeProvider((200, gemini_body({"deletion": True}, finish="MAX_TOKENS"))))
        with self.assertRaises(LLMError) as ctx:
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertIn("MAX_TOKENS", str(ctx.exception))

    def test_blocked_request_is_an_error(self):
        blocked = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        client, _ = self.make(FakeProvider((200, blocked)))
        with self.assertRaises(LLMError):
            client.ask(PROMPT, [Part("text", "x")], "x")

    def test_per_minute_quota_waits_as_told_and_retries(self):
        fake = FakeProvider((429, gemini_quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s")),
                            (200, gemini_body({"deletion": True})))
        client, clock = self.make(fake)
        self.assertEqual(client.ask(PROMPT, [Part("text", "x")], "x"), {"deletion": True})
        self.assertIn(7.0, clock.slept)

    def test_daily_quota_is_not_retried(self):
        fake = FakeProvider((429, gemini_quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")))
        client, _ = self.make(fake)
        with self.assertRaises(LLMError):
            client.ask(PROMPT, [Part("text", "x")], "x")
        self.assertEqual(len(fake.requests), 1)

    def test_display_name_is_not_a_model_name(self):
        # Found in practice: .env held the console's display name instead of the API model name.
        with self.assertRaises(LLMError):
            self.make(FakeProvider(), model="Gemini 3.8 Flash")

    def test_other_gemini_models_are_refused(self):
        with self.assertRaises(LLMError):
            self.make(FakeProvider(), model="gemini-3.8-flash")
