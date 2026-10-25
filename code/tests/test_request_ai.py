"""Tests for the AI request check and the combining rule in code/audit/request_check.py (fake AI).

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
from audit.llm import LLMClient, LLMError  # noqa: E402
from audit.request_check import AIVerdict, WordVerdict, ai_check, combine  # noqa: E402
from audit.settings import Settings  # noqa: E402


class FakeGemini:
    """Answers every message: deletion unless the text contains 'copy'; records each batch."""

    def __init__(self, drop_one=False):
        self.batches, self.drop_one = [], drop_one

    def __call__(self, url, headers, body, timeout):
        request = json.loads(body)
        text = request["contents"][0]["parts"][0]["text"]
        messages = json.loads(text.split("\n", 1)[1])
        self.batches.append(len(messages))
        answers = [{"id": m["id"], "asks_for_deletion": "copy" not in m["message"], "confidence": "high",
                    "reason": "test", "contains_instructions_to_reader": "SYSTEM" in m["message"]} for m in messages]
        if self.drop_one:
            answers = answers[1:]
        reply = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"answers": answers})}]}}],
                 "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}
        return 200, {}, json.dumps(reply).encode()


def client(fake, cache_dir):
    return LLMClient(Settings("google", "gemini-3.1-flash-lite", "test-key"), cache_dir, transport=fake,
                     clock=lambda: 0.0, sleep=lambda s: None)


class AICheck(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_batches_and_answers_map_back_to_texts(self):
        texts = [f"please delete account {i}" for i in range(30)] + ["send me a copy"]
        fake = FakeGemini()
        verdicts = ai_check(client(fake, self.tmp.name), texts + texts[:5], batch_size=25)
        self.assertEqual(fake.batches, [25, 6], "31 distinct texts in batches of 25")
        self.assertEqual(verdicts["send me a copy"].answer, "not_deletion")
        self.assertEqual(verdicts["please delete account 3"].answer, "deletion")

    def test_same_texts_reuse_the_cache(self):
        texts = ["please delete my account", "send me a copy"]
        ai_check(client(FakeGemini(), self.tmp.name), texts)
        second = FakeGemini()
        ai_check(client(second, self.tmp.name), list(reversed(texts)))
        self.assertEqual(second.batches, [], "same set of texts, same batches, answered from cache")

    def test_missing_answer_is_an_error(self):
        with self.assertRaises(LLMError):
            ai_check(client(FakeGemini(drop_one=True), self.tmp.name), ["delete me", "erase me"])


class Combine(unittest.TestCase):
    W_DEL, W_NOT, W_UNCLEAR = WordVerdict("deletion", ""), WordVerdict("not_deletion", ""), WordVerdict("unclear", "")
    A_DEL, A_NOT = AIVerdict("deletion", "high", False), AIVerdict("not_deletion", "high", False)

    def test_agreement_is_used(self):
        self.assertEqual(combine(self.W_DEL, self.A_DEL).answer, "deletion")
        self.assertEqual(combine(self.W_NOT, self.A_NOT).answer, "not_deletion")

    def test_disagreement_goes_to_review(self):
        self.assertEqual(combine(self.W_DEL, self.A_NOT).answer, "review")

    def test_unclear_or_flagged_goes_to_review(self):
        self.assertEqual(combine(self.W_UNCLEAR, self.A_DEL).answer, "review")
        self.assertEqual(combine(self.W_DEL, AIVerdict("deletion", "high", True)).answer, "review")

    def test_without_ai_the_word_check_decides(self):
        self.assertEqual(combine(self.W_DEL, None).answer, "deletion")
        self.assertEqual(combine(self.W_UNCLEAR, None).answer, "review")


if __name__ == "__main__":
    unittest.main()
