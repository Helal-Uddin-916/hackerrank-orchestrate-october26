"""Apply deletion_rules.csv to each record: what it needs, whether it is already fine (DESIGN.md part 6).

The required action per data category is read from dataset/deletion_rules.csv. The table below says
how each action is checked and fixed, as (finding, remediation, verification) operations. A record
that already has the required treatment gets only its verification; otherwise it gets a finding, a
fix and a verification. Data flows and backups follow the same pattern with their own operations.

Flag, don't guess (D-012 to D-015): an ended retention or hold, a shared attachment and a kind no rule
covers produce warnings or a side-report entry and no steps. A linked record with no evidence at
audit_at gets only its required end-state check, marked unproven (D-031): an INCOMPLETE plan includes
it with no proof, a COMPLETE plan cannot prove it and leaves it out with a warning.
"""
import csv
import os
from dataclasses import dataclass, field
from typing import List, Optional

from audit.sighting import parse_time

# action -> {current state: (finding, remediation, verification)}; states not listed use "otherwise".
ACTIONS = {
    "DELETE": {
        "ABSENT": (None, None, "VERIFY_RECORD_ABSENT"),
        "otherwise": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    },
    "REDACT": {
        "REDACTED": (None, None, "VERIFY_RECORD_REDACTED"),
        "ABSENT": (None, None, "VERIFY_RECORD_ABSENT"),
        "otherwise": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    },
    "ANONYMIZE": {
        "ANONYMIZED": (None, None, "VERIFY_RECORD_ANONYMIZED"),
        "ABSENT": (None, None, "VERIFY_RECORD_ABSENT"),
        "otherwise": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    },
    "RETAIN": {
        "otherwise": (None, None, "VERIFY_RECORD_PRESENT"),
    },
    "RETAIN_BLOCK_RESTORE": {  # BACKUP_ARCHIVE: kept, but restore must be blocked (rule_14)
        "RESTORE_BLOCKED": (None, None, "VERIFY_RESTORE_BLOCKED"),
        "otherwise": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    },
    "DISABLE_FLOW": {  # data flows are recreation paths, not data categories
        "FLOW_DISABLED": (None, None, "VERIFY_DATA_FLOW_DISABLED"),
        "otherwise": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    },
}
UNSURE_STATES = {"UNKNOWN"}  # treated as the less safe case, with a warning (D-009)


@dataclass
class Decision:
    system_id: str
    resource_id: str
    category: str
    action: Optional[str]
    finding: Optional[str] = None
    remediation: Optional[str] = None
    verification: Optional[str] = None
    evidence_id: Optional[str] = None
    side_report: bool = False
    unproven: bool = False  # no evidence at audit_at: only the required end-state check (D-031)
    warnings: List[str] = field(default_factory=list)

    @property
    def compliant(self):
        return self.finding is None


def load_rules(dataset_dir):
    """data_category -> (rule_id, required_action) from deletion_rules.csv."""
    with open(os.path.join(dataset_dir, "deletion_rules.csv"), encoding="utf-8", newline="") as f:
        rules = {r["data_category"]: (r["rule_id"], r["required_action"]) for r in csv.DictReader(f)}
    unknown = {action for _, action in rules.values()} - {"DELETE", "REDACT", "ANONYMIZE", "RETAIN"}
    if unknown:
        raise ValueError(f"deletion_rules.csv has actions the code does not handle: {sorted(unknown)}")
    return rules


class Policy:

    def __init__(self, dataset_dir):
        self.rules = load_rules(dataset_dir)

    def decide(self, record, person, audit_at):
        category = record.category
        decision = Decision(record.system_id, record.resource_id, category, None, evidence_id=record.evidence_id)
        decision.warnings.extend(record.warnings)
        if category == "DATA_FLOW":
            action = "DISABLE_FLOW"
            if record.state == "ABSENT":
                decision.warnings.append("data flow no longer exists; no step")
                return decision
        elif category not in self.rules:
            decision.side_report = True
            decision.warnings.append(f"kind {category or 'unknown'} fits no rule; side report only (D-015)")
            return decision
        else:
            rule_id, action = self.rules[category]
            if category == "BACKUP_ARCHIVE":
                action = "RETAIN_BLOCK_RESTORE"
                if record.state == "ABSENT":
                    decision.warnings.append("backup no longer exists; nothing to block")
                    return decision
            if category == "SUPPORT_ATTACHMENT":
                owners = set(record.extras.get("attachment_owners") or ())
                if owners - set(person.fingerprints):
                    decision.warnings.append("attachment belongs to other people too; no step (D-014)")
                    return decision
            if action == "RETAIN":
                ends = record.extras.get("retention_until") or record.extras.get("hold_until")
                if ends and parse_time(ends) < audit_at:
                    decision.warnings.append("RETENTION_ENDED: keep-until date passed before audit_at; no step (D-013)")
                    return decision
                if record.state is not None and record.state not in ("PRESENT", "EXPOSED"):
                    decision.warnings.append(f"record that must be kept is {record.state} at audit_at")

        decision.action = action
        table = ACTIONS[action]
        if record.state is None:
            decision.unproven = True
            decision.verification = table["otherwise"][2]
            decision.warnings.append("no evidence of this record at audit_at; required end-state check only (D-031)")
            return decision
        decision.finding, decision.remediation, decision.verification = table.get(record.state, table["otherwise"])
        if record.state in UNSURE_STATES and decision.finding:
            decision.warnings.append("state UNKNOWN at audit_at; treated as not yet fixed (D-009)")
        return decision
