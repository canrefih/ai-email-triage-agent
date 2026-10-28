import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from triage.config import Settings
from triage.models import Decision, Email
from triage.prompts import FEW_SHOT_MESSAGES, SYSTEM_PROMPT
from triage.security import assess_email, apply_security_plan


def now():
    return datetime.now(timezone.utc).isoformat()


def action_key(email_id, action):
    payload = json.dumps(
        [email_id, action],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                folder TEXT NOT NULL DEFAULT 'Inbox',
                category TEXT
            );

            CREATE TABLE IF NOT EXISTS decisions (
                email_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                action_key TEXT PRIMARY KEY,
                email_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS effects (
                action_key TEXT PRIMARY KEY,
                email_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()


class MockTools:
    """Local tool effects and completion records share one transaction."""

    def __init__(self, store, emails):
        self.store = store

        for email in emails:
            self.store.db.execute(
                "INSERT OR IGNORE INTO emails(id, payload) VALUES (?, ?)",
                (email.id, email.model_dump_json()),
            )

        self.store.db.commit()

    def get_email(self, email_id):
        row = self.store.db.execute(
            "SELECT payload FROM emails WHERE id = ?",
            (email_id,),
        ).fetchone()

        if row is None:
            raise KeyError(email_id)

        return Email.model_validate_json(row["payload"])

    def search_sender_history(self, email, limit):
        rows = self.store.db.execute(
            "SELECT payload FROM emails WHERE id != ? ORDER BY id",
            (email.id,),
        ).fetchall()

        history = []

        for row in rows:
            previous = Email.model_validate_json(row["payload"])

            if previous.sender.casefold() == email.sender.casefold():
                history.append({
                    "id": previous.id,
                    "subject": previous.subject[:200],
                })

        return history[:limit]

    def route(self, email_id, folder):
        self.store.db.execute(
            "UPDATE emails SET folder = ? WHERE id = ?",
            (folder, email_id),
        )

    def move_to_holding(self, email_id):
        self.route(email_id, "Holding")
        self.store.db.execute(
            "UPDATE emails SET category = 'Quarantine' WHERE id = ?",
            (email_id,),
        )

    def create_ticket(self, email_id):
        return f"MOCK-TICKET-{email_id}"

    def send_ack(self, email_id, template):
        templates = {
            "billing": "We received your billing request.",
            "onboarding": "We received your activation request.",
        }
        return templates[template]

    def escalate_to_human(self, email_id, team):
        return {"email_id": email_id, "team": team}

    def execute(self, email_id, action):
        kind = action["kind"]

        if kind == "route":
            self.route(email_id, action["folder"])
            return {"folder": action["folder"]}

        if kind == "move_to_holding":
            self.move_to_holding(email_id)
            return {"folder": "Holding", "category": "Quarantine"}

        if kind == "create_ticket":
            return {"ticket_id": self.create_ticket(email_id)}

        if kind == "send_ack":
            return {
                "body": self.send_ack(email_id, action["template"]),
            }

        if kind == "escalate_to_human":
            return self.escalate_to_human(email_id, action["team"])

        raise ValueError(f"Unknown action: {kind}")


class Classifier(Protocol):
    source: str

    def classify(self, email, history) -> Decision: ...


class MockClassifier:
    source = "mock"

    def __init__(self, fixtures):
        self.fixtures = fixtures

    def classify(self, email, history):
        return Decision.model_validate(self.fixtures[email.id])


class LocalClassifier:
    source = "local_llm"

    def __init__(self, base_url, model, settings):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=os.getenv("LOCAL_LLM_API_KEY", "local"),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def classify(self, email, history):
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *FEW_SHOT_MESSAGES,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "email": email.model_dump(),
                            "sender_history": history,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "triage_decision",
                    "strict": True,
                    "schema": Decision.model_json_schema(),
                },
            },
        )

        content = response.choices[0].message.content

        if not content:
            raise ValueError("Empty model response")

        return Decision.model_validate_json(content)


def review_plan(reason):
    return {
        "reason": reason,
        "actions": [{"kind": "route", "folder": "Needs review"}],
    }


def make_plan(decision, settings):
    topics = {topic.value for topic in decision.sensitive_topics}
    sensitive = bool(topics - {"refund"})
    refund_uncertain = False

    refund_present = (
        "refund" in topics
        or decision.refund_amount is not None
        or decision.refund_currency is not None
    )

    if refund_present:
        if (
            decision.refund_amount is None
            or decision.refund_currency != settings.refund_currency
        ):
            refund_uncertain = True
        elif Decimal(decision.refund_amount) > settings.refund_threshold:
            sensitive = True

    intent = decision.intent.value
    reason = decision.reason

    if intent == "spam_phishing":
        actions = [
            {"kind": "move_to_holding"},
            {"kind": "escalate_to_human", "team": "security"},
        ]

        if sensitive or refund_uncertain:
            actions.append({
                "kind": "escalate_to_human",
                "team": "sensitive_requests",
            })

        return {"reason": reason, "actions": actions}

    if sensitive or refund_uncertain:
        return {
            "reason": f"Mandatory human handling: {reason}",
            "actions": [{
                "kind": "escalate_to_human",
                "team": "sensitive_requests",
            }],
        }

    if (
        decision.ambiguous
        or decision.confidence < settings.confidence_threshold
    ):
        return review_plan(f"Ambiguous or low confidence: {reason}")

    if intent == "billing":
        actions = [
            {"kind": "route", "folder": "Finance"},
            {"kind": "send_ack", "template": "billing"},
        ]
    elif intent == "technical_outage":
        actions = [
            {"kind": "create_ticket"},
        ]

        if decision.priority.value == "urgent":
            actions.insert(0, {
                "kind": "escalate_to_human",
                "team": "support_on_call",
            })

        actions.append({"kind": "route", "folder": "Support"})
    elif intent == "onboarding":
        actions = [
            {"kind": "route", "folder": "Onboarding"},
            {"kind": "send_ack", "template": "onboarding"},
        ]
    elif intent == "complaint":
        actions = [{"kind": "route", "folder": "Support"}]
    else:
        return review_plan(f"No configured business route: {reason}")

    return {"reason": reason, "actions": actions}


class Executor:
    def __init__(self, store, tools):
        self.store = store
        self.tools = tools

    def execute(self, email_id, action):
        key = action_key(email_id, action)
        row = self.store.db.execute(
            "SELECT status FROM actions WHERE action_key = ?",
            (key,),
        ).fetchone()

        if row and row["status"] == "done":
            return {"action": action, "status": "skipped"}

        if row and row["status"] == "running":
            return {
                "action": action,
                "status": "needs_reconciliation",
            }

        self.store.db.execute(
            """
            INSERT INTO actions VALUES (?, ?, ?, ?, 'running', NULL, ?)
            ON CONFLICT(action_key) DO UPDATE SET
                status = 'running', error = NULL,
                updated_at = excluded.updated_at
            """,
            (key, email_id, action["kind"], json.dumps(action), now()),
        )
        self.store.db.commit()

        try:
            with self.store.db:
                result = self.tools.execute(email_id, action)

                self.store.db.execute(
                    "INSERT OR IGNORE INTO effects VALUES (?, ?, ?, ?, ?)",
                    (
                        key,
                        email_id,
                        action["kind"],
                        json.dumps(result),
                        now(),
                    ),
                )
                self.store.db.execute(
                    """
                    UPDATE actions SET status = 'done', updated_at = ?
                    WHERE action_key = ?
                    """,
                    (now(), key),
                )

            return {"action": action, "status": "done"}

        except Exception as exc:
            with self.store.db:
                self.store.db.execute(
                    """
                    UPDATE actions SET status = 'failed', error = ?,
                    updated_at = ? WHERE action_key = ?
                    """,
                    (type(exc).__name__, now(), key),
                )

            return {
                "action": action,
                "status": "failed",
                "error": type(exc).__name__,
            }


class TriageAgent:
    def __init__(
        self,
        store: Store,
        tools: MockTools,
        classifier: Classifier,
        settings: Settings,
    ):
        self.store = store
        self.tools = tools
        self.classifier = classifier
        self.settings = settings
        self.executor = Executor(store, tools)

    def process(self, email_id):
        email = self.tools.get_email(email_id)
        saved = self.store.db.execute(
            "SELECT payload, source FROM decisions WHERE email_id = ?",
            (email_id,),
        ).fetchone()

        if saved:
            record = json.loads(saved["payload"])
            source = saved["source"]
        else:
            source = self.classifier.source

            try:
                history = self.tools.search_sender_history(
                    email,
                    self.settings.sender_history_limit,
                )
                decision = self.classifier.classify(email, history)
                record = {
                    "decision": decision.model_dump(mode="json"),
                    "plan": make_plan(decision, self.settings),
                }

            except Exception as exc:
                record = {
                    "decision": None,
                    "error": type(exc).__name__,
                    "plan": review_plan(
                        f"Classification unavailable: {type(exc).__name__}"
                    ),
                }

            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO decisions VALUES (?, ?, ?, ?)",
                    (email_id, json.dumps(record), source, now()),
                )

        security = assess_email(email)

        if "original_plan" not in record:
            record["original_plan"] = record["plan"]

        record["security"] = security
        record["plan"] = apply_security_plan(
            record["original_plan"],
            security,
        )

        with self.store.db:
            self.store.db.execute(
                "UPDATE decisions SET payload = ? WHERE email_id = ?",
                (json.dumps(record), email_id),
            )

        results = []

        for action in record["plan"]["actions"]:
            result = self.executor.execute(email_id, action)
            results.append(result)

            if result["status"] in {"failed", "needs_reconciliation"}:
                break

        return {
            "email_id": email_id,
            "source": source,
            **record,
            "results": results,
        }


def load_data(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    emails = [Email.model_validate(item) for item in data["emails"]]

    if len({email.id for email in emails}) != len(emails):
        raise ValueError("Duplicate email IDs")

    return emails, data["mock_decisions"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "local"], default="mock")
    parser.add_argument("--data", default="data/emails.json")
    parser.add_argument("--db", default="runtime/mock.sqlite")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model")
    args = parser.parse_args()

    if args.mode == "local" and not args.model:
        parser.error("--model is required in local mode")

    settings = Settings()
    emails, fixtures = load_data(args.data)
    store = Store(args.db)

    try:
        tools = MockTools(store, emails)
        classifier = (
            MockClassifier(fixtures)
            if args.mode == "mock"
            else LocalClassifier(args.base_url, args.model, settings)
        )
        agent = TriageAgent(store, tools, classifier, settings)

        for email in emails:
            try:
                report = agent.process(email.id)
            except Exception as exc:
                report = {
                    "email_id": email.id,
                    "processing_error": type(exc).__name__,
                }

            print(json.dumps(report, ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()