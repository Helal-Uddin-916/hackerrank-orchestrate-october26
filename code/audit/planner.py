"""Turn per-record decisions into the answer row: status and ordered steps (DESIGN.md part 7).

- COMPLETE when no record needs a fix: only verification steps, each citing the file that proves
  the record's state at audit_at (D-011).
- INCOMPLETE otherwise: findings citing their proof, then the remediation plan and the checks that
  must pass afterwards, both with evidence_id "".
- Order: phase, then the operation order of the contract (data flows disabled and restores blocked
  before records are changed), then system_id, then resource_id, as plain strings; steps numbered
  from 1 (AGENTS.md section 6.3-6.5).
- Records with no evidence at audit_at give only their required end-state check (D-031): kept in an
  INCOMPLETE plan with evidence_id "", left out of a COMPLETE plan (it cannot be proven) with a warning.
- Nothing provable for a deletion request: INCOMPLETE with the unproven end-state checks, or [] if
  there are none, plus a warning (D-032). Never COMPLETE without proof.
"""
from dataclasses import dataclass, field
from typing import List, Optional

# The contract's operation order (problem_statement.md "Allowed Operations"); test_planner.py checks
# it matches code/validate_output.py.
OPERATION_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
STATE_OF = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
PHASE_OF = {op: ("FINDING" if op.startswith("REPORT") else "VERIFICATION" if op.startswith("VERIFY") else "REMEDIATION")
            for op in OPERATION_ORDER}


@dataclass
class Plan:
    status: Optional[str]
    steps: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _step(operation, system_id, resource_id, evidence_id):
    return {"phase": PHASE_OF[operation], "system_id": system_id, "resource_id": resource_id,
            "operation": operation, "state": STATE_OF[operation], "evidence_id": evidence_id or ""}


def plan(decisions):
    """Return a Plan from an iterable of policy Decisions for one request."""
    decisions = list(decisions)
    warnings = [f"{d.resource_id}: {w}" for d in decisions for w in d.warnings]
    usable = [d for d in decisions if d.verification]
    if not usable:
        return Plan("INCOMPLETE", [], warnings + ["deletion request but nothing can be proven at audit_at (D-032)"])

    complete = all(d.finding is None for d in usable)
    if complete:
        proven = [d for d in usable if not d.unproven]
        if not proven:
            # Nothing provable: not confirmed, so INCOMPLETE with the required end-state checks (D-032).
            complete = False
            warnings.append("deletion request but nothing can be proven at audit_at (D-032)")
        else:
            warnings += [f"{d.resource_id}: cannot be proven at audit_at; left out of the COMPLETE answer (D-031)"
                         for d in usable if d.unproven]
            usable = proven
    raw = []
    for d in usable:
        if complete:
            if not d.evidence_id:
                warnings.append(f"{d.resource_id}: COMPLETE check has no proof file")
            raw.append(_step(d.verification, d.system_id, d.resource_id, d.evidence_id))
            continue
        if d.finding:
            raw.append(_step(d.finding, d.system_id, d.resource_id, d.evidence_id))
            raw.append(_step(d.remediation, d.system_id, d.resource_id, ""))
        raw.append(_step(d.verification, d.system_id, d.resource_id, ""))

    raw.sort(key=lambda s: (OPERATION_ORDER.index(s["operation"]), s["system_id"], s["resource_id"]))
    steps = [dict(step_no=i, **s) for i, s in enumerate(raw, start=1)]
    return Plan("COMPLETE" if complete else "INCOMPLETE", steps, warnings)
