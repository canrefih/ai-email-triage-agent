from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Settings:
    confidence_threshold: float = 0.80
    refund_threshold: Decimal = Decimal("500.00")
    refund_currency: str = "CHF"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    sender_history_limit: int = 3

    def __post_init__(self) -> None:
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if not self.refund_threshold.is_finite() or self.refund_threshold < 0:
            raise ValueError("invalid refund threshold")
        if len(self.refund_currency) != 3 or not self.refund_currency.isalpha() or not self.refund_currency.isupper():
            raise ValueError("currency must be three uppercase letters")
        if self.llm_timeout_seconds <= 0 or self.llm_max_retries < 0 or self.sender_history_limit < 0:
            raise ValueError("invalid timeout, retries or history limit")
