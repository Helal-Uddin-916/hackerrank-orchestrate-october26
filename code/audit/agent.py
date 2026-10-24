"""The deletion-audit agent: one object per dataset folder, one call per request (DESIGN.md section 1).

Built once: evidence (all readers), identity index, record assembler, activity linker, policy.
Per request: check that it really asks for deletion (word check + AI, D-010, D-033), find the
person, find their records at audit_at, apply the rules, write the steps.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from audit.activity_link import ActivityLinker, load_requests
from audit.llm import LLMError
from audit.assemble import Assembler
from audit.evidence import load_evidence
from audit.identity import IdentityIndex
from audit.planner import plan
from audit.policy import Policy
from audit.request_check import ai_check, combine, word_check
from audit.sighting import parse_time


@dataclass
class CaseResult:
    case_id: str
    status: Optional[str]
    steps: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class Agent:

    def __init__(self, dataset_dir, client=None):
        self.evidence = load_evidence(dataset_dir, client)
        self.assembler = Assembler(self.evidence)
        self.identity = IdentityIndex(dataset_dir)
        self.linker = ActivityLinker(self.assembler, self.identity, load_requests(dataset_dir), dataset_dir)
        self.policy = Policy(dataset_dir)
        self.warnings = []
        self.ai_verdicts = {}
        if client is not None:
            try:  # one pass over every request text in the folder; normally answered from the cache
                self.ai_verdicts = ai_check(client, [r["request_text"] for r in load_requests(dataset_dir)])
            except LLMError as error:
                self.warnings.append(f"AI request check unavailable, word check only: {error}")

    def run_case(self, request):
        case_id = request["case_id"]
        text = request["request_text"]
        asked = combine(word_check(text), self.ai_verdicts.get(text))
        if asked.answer != "deletion":
            warnings = [] if asked.answer == "not_deletion" else [f"request needs human review: {asked.reason} (D-033)"]
            return CaseResult(case_id, "NOT_APPLICABLE", [], warnings)
        person = self.identity.resolve(request["email"])
        if not person.found:
            return CaseResult(case_id, "INCOMPLETE", [], list(person.warnings) + ["nothing can be proven (D-032)"])
        audit_at = parse_time(request["audit_at"])
        extra, link_warnings = self.linker.link(case_id)
        records = self.assembler.assemble(person, audit_at, extra)
        decisions = [self.policy.decide(record, person, audit_at) for _, record in sorted(records.items())]
        result = plan(decisions)
        return CaseResult(case_id, result.status, result.steps, link_warnings + result.warnings)
