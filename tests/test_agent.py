from pathlib import Path

import pytest

from triage.app import (
    MockClassifier,
    MockTools,
    Store,
    TriageAgent,
    load_data,
    make_plan,
)
from triage.config import Settings
from triage.models import Decision


DATA = Path(__file__).resolve().parents[1] / "data" / "emails.json"


@pytest.fixture
def system(tmp_path):
    emails, decisions = load_data(DATA)
    store = Store(tmp_path / "test.sqlite")
    tools = MockTools(store, emails)
    agent = TriageAgent(
        store,
        tools,
        MockClassifier(decisions),
        Settings(),
    )

    yield store, agent, decisions

    store.close()


def effects(store, email_id):
    return [
        row["kind"]
        for row in store.db.execute(
            "SELECT kind FROM effects WHERE email_id = ?",
            (email_id,),
        )
    ]


@pytest.mark.parametrize(
    ("email_id", "folder", "expected_effects"),
    [
        ("e01", "Finance", {"route", "send_ack"}),
        (
            "e02",
            "Support",
            {"route", "create_ticket", "escalate_to_human"},
        ),
        ("e03", "Onboarding", {"route", "send_ack"}),
        ("e04", "Support", {"route"}),
        (
            "e05",
            "Holding",
            {"move_to_holding", "escalate_to_human"},
        ),
        ("e06", "Inbox", {"escalate_to_human"}),
        ("e07", "Needs review", {"route"}),
        ("e08", "Inbox", {"escalate_to_human"}),
    ],
)
def test_end_to_end_routes(
    system, email_id, folder, expected_effects
):
    store, agent, _ = system
    report = agent.process(email_id)

    actual = store.db.execute(
        "SELECT folder FROM emails WHERE id = ?",
        (email_id,),
    ).fetchone()["folder"]

    assert actual == folder
    assert set(effects(store, email_id)) == expected_effects
    assert all(item["status"] == "done" for item in report["results"])


def test_phishing_has_quarantine_and_no_reply(system):
    store, agent, _ = system
    agent.process("e05")

    category = store.db.execute(
        "SELECT category FROM emails WHERE id = 'e05'"
    ).fetchone()["category"]

    assert category == "Quarantine"
    assert "send_ack" not in effects(store, "e05")


def test_repeat_run_does_not_duplicate_effects(system):
    store, agent, _ = system

    for email_id in [f"e{i:02}" for i in range(1, 9)]:
        agent.process(email_id)

    before = store.db.execute(
        "SELECT COUNT(*) FROM effects"
    ).fetchone()[0]

    for email_id in [f"e{i:02}" for i in range(1, 9)]:
        report = agent.process(email_id)
        assert all(
            item["status"] == "skipped"
            for item in report["results"]
        )

    after = store.db.execute(
        "SELECT COUNT(*) FROM effects"
    ).fetchone()[0]

    assert after == before


def test_classification_failure_goes_to_review(system):
    store, agent, _ = system

    class BrokenClassifier:
        source = "test_failure"

        def classify(self, email, history):
            raise TimeoutError("Simulated timeout")

    agent.classifier = BrokenClassifier()
    report = agent.process("e01")

    assert report["decision"] is None
    assert report["error"] == "TimeoutError"
    assert effects(store, "e01") == ["route"]

    folder = store.db.execute(
        "SELECT folder FROM emails WHERE id = 'e01'"
    ).fetchone()["folder"]

    assert folder == "Needs review"


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 0.2},
        {"ambiguous": True},
        {"intent": "other"},
    ],
)
def test_uncertainty_blocks_business_actions(system, changes):
    _, _, decisions = system
    data = {**decisions["e01"], **changes}
    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [
        {"kind": "route", "folder": "Needs review"}
    ]


@pytest.mark.parametrize(
    "topic",
    ["data_protection", "data_deletion", "legal_threat"],
)
def test_sensitive_topics_block_normal_actions(system, topic):
    _, _, decisions = system
    data = {
        **decisions["e01"],
        "sensitive_topics": [topic],
    }
    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [{
        "kind": "escalate_to_human",
        "team": "sensitive_requests",
    }]


@pytest.mark.parametrize(
    ("amount", "currency", "escalates"),
    [
        ("499.99", "CHF", False),
        ("500.00", "CHF", False),
        ("500.01", "CHF", True),
        (None, "CHF", True),
        ("100.00", "EUR", True),
    ],
)
def test_refund_threshold(system, amount, currency, escalates):
    _, _, decisions = system
    data = {
        **decisions["e08"],
        "refund_amount": amount,
        "refund_currency": currency,
    }
    plan = make_plan(Decision.model_validate(data), Settings())
    kinds = {action["kind"] for action in plan["actions"]}

    assert ("escalate_to_human" in kinds) == escalates

    if escalates:
        assert "send_ack" not in kinds


def test_normal_outage_creates_ticket_without_escalation(system):
    _, _, decisions = system
    data = {**decisions["e02"], "priority": "normal"}
    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [
        {"kind": "create_ticket"},
        {"kind": "route", "folder": "Support"},
    ]


def test_tool_failure_stops_later_actions(system, monkeypatch):
    store, agent, _ = system

    def fail_route(email_id, folder):
        raise RuntimeError("Simulated mailbox failure")

    monkeypatch.setattr(agent.tools, "route", fail_route)
    report = agent.process("e01")

    assert report["results"][0]["status"] == "failed"
    assert "send_ack" not in effects(store, "e01")