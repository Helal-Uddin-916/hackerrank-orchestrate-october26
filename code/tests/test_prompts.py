"""Tests for code/audit/prompts.py: schemas follow the provider's strict-mode rules.

OpenAI strict JSON-schema mode requires every object to list all its properties as required and
to set additionalProperties to false. Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import os
import sys
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit import prompts  # noqa: E402
from audit.llm import LLMError, Prompt, validate  # noqa: E402

ALL_PROMPTS = [v for v in vars(prompts).values() if isinstance(v, Prompt)]


def objects(schema, path="$"):
    """Yield (path, schema) for every object schema inside schema."""
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "object" in kinds:
        yield path, schema
        for key, sub in schema.get("properties", {}).items():
            yield from objects(sub, f"{path}.{key}")
    if "array" in kinds and "items" in schema:
        yield from objects(schema["items"], f"{path}[]")


class StrictSchemas(unittest.TestCase):

    def test_there_is_at_least_one_prompt(self):
        self.assertIn(prompts.EXTRACT_DOCUMENT, ALL_PROMPTS)

    def test_every_object_is_strict(self):
        for prompt in ALL_PROMPTS:
            for path, obj in objects(prompt.schema):
                self.assertIs(obj.get("additionalProperties"), False, f"{prompt.prompt_id} {path}")
                self.assertEqual(set(obj.get("required", [])), set(obj.get("properties", {})), f"{prompt.prompt_id} {path}")

    def test_prompts_have_ids_versions_and_injection_guard(self):
        for prompt in ALL_PROMPTS:
            self.assertTrue(prompt.prompt_id and prompt.version)
            self.assertIn("never an instruction", prompt.instructions)


class ExtractDocumentAnswers(unittest.TestCase):

    def test_a_realistic_answer_passes(self):
        answer = {
            "document_title": "Customer-platform archives", "page_time_text": None,
            "records": [{"resource_id": "backup_9287", "system_text": "Vault Recovery Manager", "status_text": None,
                         "identifiers": [{"kind": "email", "value": "someone@example.com"}],
                         "dates": [{"label": "Retention end", "value": "26 Jul 2026"}],
                         "facts": [{"label": "Region", "value": "eu-west-1"}], "where": "detail_panel"}],
            "events": [{"time_text": "25 Apr", "resource_id": "backup_9286", "text": "backup_9286 restored · Completed"}],
            "contains_instructions_to_reader": False, "unreadable": [],
        }
        validate(prompts.EXTRACT_DOCUMENT.schema, answer)

    def test_unknown_identifier_kind_is_rejected(self):
        bad = {"document_title": "x", "page_time_text": None, "events": [], "contains_instructions_to_reader": False,
               "unreadable": [], "records": [{"resource_id": "r_1", "system_text": None, "status_text": None,
                                              "identifiers": [{"kind": "favourite_food", "value": "x"}],
                                              "dates": [], "facts": [], "where": "table_row"}]}
        with self.assertRaises(LLMError):
            validate(prompts.EXTRACT_DOCUMENT.schema, bad)


if __name__ == "__main__":
    unittest.main()
