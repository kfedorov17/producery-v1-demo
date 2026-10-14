"""Output contracts shared by prompts (their O section), nodes (`response_format`) and evaluators.

Every structured model call and every human decision is validated against one of these models, so the eval
checks score the same shape the prompt promised and the node consumed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EmailClass = Literal[
    "carrier_thread",
    "network_thread",
    "prospect",
    "client_service",
    "billing_commission",
    "compliance",
    "vendor",
    "personal_ignore",
]


class Classification(BaseModel):
    """The classifier's verdict for one inbound email: one of the eight classes, a confidence and a reason."""

    classification: EmailClass
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)


class FieldValue(BaseModel):
    """One extracted field: the value, the source span it was read from and the extractor's confidence."""

    value: Any = None
    source_span: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Extraction(BaseModel):
    """The extractor's result: the document type, a decline reason for non-documents, and the fields by name."""

    doc_type: Literal["dec_page", "contract", "not_document"]
    decline_reason: str | None = None
    fields: dict[str, FieldValue] = Field(default_factory=dict)


class DraftResponse(BaseModel):
    """The drafter's structured answer: client reply, owner brief, citations, or a refusal with its reason."""

    subject: str = ""
    body: str = ""
    owner_brief: str = ""
    citations: list[str] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None


class JudgeVerdict(BaseModel):
    """The LLM judge's anchored score (0 is the violation floor, 3 the best) and its reason."""

    score: Literal[0, 1, 2, 3]
    reason: str


class Decision(BaseModel):
    """A human decision on a paused proposal: approve, edit (with the edited payload) or reject (with a reason)."""

    action: Literal["approve", "edit", "reject"]
    by: str = "reviewer"
    reason: str | None = None
    payload: dict[str, Any] | None = None  # edited proposal payload when action == "edit"
