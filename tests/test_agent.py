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


def test_holding_failure_does_not_block_security_alert(
    system, monkeypatch
):
    store, agent, _ = system

    def fail_holding(email_id):
        raise RuntimeError("Simulated holding failure")

    monkeypatch.setattr(
        agent.tools,
        "move_to_holding",
        fail_holding,
    )

    report = agent.process("e05")

    assert [
        (item["action"]["kind"], item["status"])
        for item in report["results"]
    ] == [
        ("move_to_holding", "failed"),
        ("escalate_to_human", "done"),
    ]

    assert effects(store, "e05") == ["escalate_to_human"]

    folder = store.db.execute(
        "SELECT folder FROM emails WHERE id = 'e05'"
    ).fetchone()["folder"]

    assert folder == "Inbox"


def test_security_alert_failure_is_recorded(system, monkeypatch):
    store, agent, _ = system

    def fail_escalation(email_id, team):
        raise RuntimeError("Simulated notification failure")

    monkeypatch.setattr(
        agent.tools,
        "escalate_to_human",
        fail_escalation,
    )

    report = agent.process("e05")

    assert [
        (item["action"]["kind"], item["status"])
        for item in report["results"]
    ] == [
        ("move_to_holding", "done"),
        ("escalate_to_human", "failed"),
    ]

    row = store.db.execute(
        """
        SELECT status, error
        FROM actions
        WHERE email_id = ? AND kind = ?
        """,
        ("e05", "escalate_to_human"),
    ).fetchone()

    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"
    assert effects(store, "e05") == ["move_to_holding"]


def test_billing_failure_reports_ack_as_blocked(
    system, monkeypatch
):
    store, agent, _ = system

    def fail_route(email_id, folder):
        raise RuntimeError("Simulated routing failure")

    monkeypatch.setattr(agent.tools, "route", fail_route)

    report = agent.process("e01")

    assert [
        (item["action"]["kind"], item["status"])
        for item in report["results"]
    ] == [
        ("route", "failed"),
        ("send_ack", "blocked"),
    ]

    assert effects(store, "e01") == []


def test_recovery_retries_holding_without_repeating_alert(
    system, monkeypatch
):
    store, agent, _ = system
    original_holding = agent.tools.move_to_holding

    def fail_holding(email_id):
        raise RuntimeError("Simulated holding failure")

    monkeypatch.setattr(
        agent.tools,
        "move_to_holding",
        fail_holding,
    )

    first = agent.process("e05")
    assert first["results"][0]["status"] == "failed"
    assert first["results"][1]["status"] == "done"

    monkeypatch.setattr(
        agent.tools,
        "move_to_holding",
        original_holding,
    )

    second = agent.process("e05")

    assert [
        (item["action"]["kind"], item["status"])
        for item in second["results"]
    ] == [
        ("move_to_holding", "done"),
        ("escalate_to_human", "skipped"),
    ]

    kinds = effects(store, "e05")
    assert kinds.count("move_to_holding") == 1
    assert kinds.count("escalate_to_human") == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 0.1},
        {"ambiguous": True},
        {"confidence": 0.1, "ambiguous": True},
    ],
)
def test_phishing_containment_precedes_uncertainty(system, changes):
    _, _, decisions = system
    data = {**decisions["e05"], **changes}

    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [
        {"kind": "move_to_holding"},
        {"kind": "escalate_to_human", "team": "security"},
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 0.1},
        {"ambiguous": True},
        {"confidence": 0.1, "ambiguous": True},
    ],
)
def test_sensitive_escalation_precedes_uncertainty(system, changes):
    _, _, decisions = system
    data = {**decisions["e06"], **changes}

    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [
        {
            "kind": "escalate_to_human",
            "team": "sensitive_requests",
        }
    ]


def test_phishing_with_sensitive_topic_keeps_both_escalations(system):
    _, _, decisions = system
    data = {
        **decisions["e05"],
        "sensitive_topics": ["data_deletion"],
        "confidence": 0.1,
        "ambiguous": True,
    }

    plan = make_plan(Decision.model_validate(data), Settings())

    assert plan["actions"] == [
        {"kind": "move_to_holding"},
        {"kind": "escalate_to_human", "team": "security"},
        {
            "kind": "escalate_to_human",
            "team": "sensitive_requests",
        },
    ]


def test_cached_classification_error_is_not_retried_by_default(
    system, monkeypatch
):
    _, agent, _ = system
    working_classify = agent.classifier.classify
    calls = []

    def timeout(email, history):
        raise TimeoutError("Temporary model failure")

    monkeypatch.setattr(agent.classifier, "classify", timeout)
    agent.process("e01")

    def recovered(email, history):
        calls.append(email.id)
        return working_classify(email, history)

    monkeypatch.setattr(agent.classifier, "classify", recovered)

    report = agent.process("e01")

    assert calls == []
    assert report["decision"] is None
    assert report["error"] == "TimeoutError"
    assert report["results"][0]["status"] == "skipped"


def test_explicit_retry_recovers_failed_classification(
    system, monkeypatch
):
    store, agent, _ = system
    working_classify = agent.classifier.classify
    calls = []

    def timeout(email, history):
        raise TimeoutError("Temporary model failure")

    monkeypatch.setattr(agent.classifier, "classify", timeout)
    first = agent.process("e01")

    assert first["decision"] is None

    def recovered(email, history):
        calls.append(email.id)
        return working_classify(email, history)

    monkeypatch.setattr(agent.classifier, "classify", recovered)

    report = agent.process(
        "e01",
        retry_classification_errors=True,
    )

    assert calls == ["e01"]
    assert report["decision"]["intent"] == "billing"
    assert "error" not in report
    assert report["plan"]["actions"] == [
        {"kind": "route", "folder": "Finance"},
        {"kind": "send_ack", "template": "billing"},
    ]
    assert all(
        item["status"] == "done"
        for item in report["results"]
    )

    folder = store.db.execute(
        "SELECT folder FROM emails WHERE id = 'e01'"
    ).fetchone()["folder"]

    assert folder == "Finance"

    repeated = agent.process(
        "e01",
        retry_classification_errors=True,
    )

    assert calls == ["e01"]
    assert all(
        item["status"] == "skipped"
        for item in repeated["results"]
    )


def test_retry_option_preserves_successful_cached_decision(
    system, monkeypatch
):
    _, agent, _ = system
    first = agent.process("e01")

    def unexpected_call(email, history):
        pytest.fail("Successful cached decision must not be reclassified")

    monkeypatch.setattr(
        agent.classifier,
        "classify",
        unexpected_call,
    )

    second = agent.process(
        "e01",
        retry_classification_errors=True,
    )

    assert second["decision"] == first["decision"]
    assert all(
        item["status"] == "skipped"
        for item in second["results"]
    )