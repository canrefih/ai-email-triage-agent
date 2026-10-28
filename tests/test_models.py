import pytest
from pydantic import ValidationError

from triage.models import Decision, Intent, Priority


@pytest.fixture
def valid_decision():
    return {
        "intent": "billing",
        "priority": "normal",
        "confidence": 0.92,
        "ambiguous": False,
        "sensitive_topics": [],
        "refund_amount": None,
        "refund_currency": None,
        "reason": "The customer requests a copy of an invoice.",
    }


def test_valid_decision_is_accepted(valid_decision):
    decision = Decision.model_validate(valid_decision)

    assert decision.intent == Intent.BILLING
    assert decision.priority == Priority.NORMAL
    assert decision.confidence == 0.92


def test_valid_json_is_accepted(valid_decision):
    import json

    decision = Decision.model_validate_json(json.dumps(valid_decision))

    assert decision.intent == Intent.BILLING


@pytest.mark.parametrize("confidence", [-0.01, 1.01, "0.92", True, None])
def test_invalid_confidence_is_rejected(valid_decision, confidence):
    valid_decision["confidence"] = confidence

    with pytest.raises(ValidationError):
        Decision.model_validate(valid_decision)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("intent", "sales"),
        ("priority", "critical"),
        ("ambiguous", "false"),
        ("sensitive_topics", ["unknown_topic"]),
        ("refund_amount", "-100.00"),
        ("refund_amount", "100.123"),
        ("refund_currency", "chf"),
        ("reason", ""),
    ],
)
def test_invalid_fields_are_rejected(valid_decision, field, value):
    valid_decision[field] = value

    with pytest.raises(ValidationError):
        Decision.model_validate(valid_decision)


@pytest.mark.parametrize(
    "field",
    [
        "intent",
        "priority",
        "confidence",
        "ambiguous",
        "sensitive_topics",
        "refund_amount",
        "refund_currency",
        "reason",
    ],
)
def test_missing_required_fields_are_rejected(valid_decision, field):
    del valid_decision[field]

    with pytest.raises(ValidationError):
        Decision.model_validate(valid_decision)


def test_unexpected_action_field_is_rejected(valid_decision):
    valid_decision["action"] = "send_ack"

    with pytest.raises(ValidationError):
        Decision.model_validate(valid_decision)


def test_sensitive_refund_is_representable(valid_decision):
    valid_decision.update(
        sensitive_topics=["refund"],
        refund_amount="750.00",
        refund_currency="CHF",
    )

    decision = Decision.model_validate(valid_decision)

    assert decision.refund_amount == "750.00"
    assert decision.refund_currency == "CHF"