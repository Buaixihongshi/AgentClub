"""Validated, provider-neutral submissions for the six-phase discussion protocol."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Claim(ResultModel):
    key: str = Field(min_length=1, max_length=80)
    decision_key: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=4000)
    recommendation: str = Field(min_length=1, max_length=2000)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=40)
    evidence_status: Literal["unverified", "verified", "inference", "preference"] = "unverified"


class Opinion(ResultModel):
    kind: Literal["opinion"] = "opinion"
    position_summary: str = Field(min_length=1, max_length=6000)
    claims: list[Claim] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    actions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({c.key for c in self.claims}) != len(self.claims):
            raise ValueError("Claim keys must be unique")
        if any(c.evidence_status == "verified" and not c.source_ids for c in self.claims):
            raise ValueError("Verified claims require source_ids")
        return self


class IssueContribution(ResultModel):
    issue_id: str | None = None
    claim_ids: list[str] = Field(min_length=1, max_length=20)
    category: Literal["fact", "approach", "assumption", "needs_information", "needs_human"]
    problem: str = Field(min_length=1, max_length=3000)
    impact: str = Field(min_length=1, max_length=2000)
    proposal: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=40)
    status: Literal["open", "resolved", "unresolved", "needs_information", "needs_human"] = "open"


class Contribution(ResultModel):
    kind: Literal["contribution"] = "contribution"
    no_addition: bool = False
    issues: list[IssueContribution] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_increment(self):
        if self.no_addition == bool(self.issues):
            raise ValueError("Submit issues or no_addition=true, not both or neither")
        return self


SECTION_IDS = ("recommendation", "consensus", "disagreements", "pending", "actions", "sources")


class DraftSection(ResultModel):
    id: Literal["recommendation", "consensus", "disagreements", "pending", "actions", "sources"]
    content: str = Field(min_length=1, max_length=12000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class DecisionOption(ResultModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=2000)


class ValueDecision(ResultModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=2000)
    issue_ids: list[str] = Field(default_factory=list, max_length=20)
    options: list[DecisionOption] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def unique_options(self):
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError("Decision option IDs must be unique")
        if any(o.id == 'defer' for o in self.options):
            raise ValueError('defer is reserved for explicitly postponing a decision')
        return self


class Disposition(ResultModel):
    finding_id: str
    action: Literal["accepted", "partially_accepted", "not_accepted"]
    reason: str = Field(min_length=1, max_length=3000)


class IssueDecision(ResultModel):
    """The synthesizer's structured outcome for a non-value issue."""
    issue_id: str = Field(min_length=1, max_length=80)
    status: Literal["resolved", "unresolved", "deferred"]
    decision: str = Field(min_length=1, max_length=3000)


class SynthesisDraft(ResultModel):
    kind: Literal["draft"] = "draft"
    sections: list[DraftSection] = Field(min_length=6, max_length=6)
    decisions: list[ValueDecision] = Field(default_factory=list, max_length=10)
    issue_decisions: list[IssueDecision] = Field(default_factory=list, max_length=50)
    dispositions: list[Disposition] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def complete_sections(self):
        if {s.id for s in self.sections} != set(SECTION_IDS):
            raise ValueError("Draft requires all six unique sections")
        if len({d.id for d in self.decisions}) != len(self.decisions):
            raise ValueError("Decision IDs must be unique")
        if len({d.issue_id for d in self.issue_decisions}) != len(self.issue_decisions):
            raise ValueError("Issue decision IDs must be unique")
        return self


class Finding(ResultModel):
    section_id: Literal["recommendation", "consensus", "disagreements", "pending", "actions", "sources"]
    category: Literal["misrepresentation", "omission", "unsupported_claim", "constraint_violation", "source_error"]
    problem: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(min_length=1, max_length=40)
    desired_change: str = Field(min_length=1, max_length=3000)
    blocking: bool = False


class Review(ResultModel):
    kind: Literal["review"] = "review"
    draft_id: str
    no_findings: bool = False
    findings: list[Finding] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_review(self):
        if self.no_findings == bool(self.findings):
            raise ValueError("Submit findings or no_findings=true, not both or neither")
        return self


RESULT_TYPES = {
    "independent": Opinion, "exchange": Contribution,
    "synthesis": SynthesisDraft, "review": Review, "finalizing": SynthesisDraft,
}
