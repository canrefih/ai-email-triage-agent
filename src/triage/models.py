from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field

class Intent(StrEnum):
    BILLING = "billing"
    TECHNICAL_OUTAGE = "technical_outage"
    ONBOARDING = "onboarding"
    COMPLAINT = "complaint"
    SPAM_PHISHING = "spam_phishing"
    OTHER = "other"

class Priority(StrEnum):
    URGENT = "urgent"
    NORMAL = "normal"
    LOW = "low"

class SensitiveTopic(StrEnum):
    DATA_PROTECTION = "data_protection"
    DATA_DELETION = "data_deletion"
    LEGAL_THREAT = "legal_threat"
    REFUND = "refund"

class Email(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    subject: str
    body: str

class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    priority: Priority
    confidence: Annotated[float, Field(strict=True, ge=0, le=1)]
    ambiguous: Annotated[bool, Field(strict=True)]
    sensitive_topics: list[SensitiveTopic]
    refund_amount: str | None = Field(pattern=r"^\d+(\.\d{1,2})?$")
    refund_currency: str | None = Field(pattern=r"^[A-Z]{3}$")
    reason: str = Field(min_length=1, max_length=600)
