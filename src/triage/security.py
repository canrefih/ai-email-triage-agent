import re
import unicodedata


POLICY_VERSION = "security-v1"

URL = re.compile(r"https?://\S+", re.IGNORECASE)

CREDENTIAL_REQUEST = re.compile(
	r"\b(?:enter|provide|submit|type|send|share)\b"
	r"[^.!?\n]{0,100}"
	r"\b(?:password|passcode|verification code|one-time code)\b",
	re.IGNORECASE,
)

AGENT_TARGET = re.compile(
	r"\b(?:automated assistants?|ai assistants?|"
	r"language models?|llms?|triage agents?)\b",
	re.IGNORECASE,
)

AGENT_COMMAND = re.compile(
	r"\b(?:must|should|shall)\s+"
	r"(?:classify|categorize|label|route|send|reply)\b",
	re.IGNORECASE,
)


NEGATED_REQUEST = re.compile(
	r"\b(?:never|do\s+not|don't|must\s+not|should\s+not|shouldn't)"
	r"\s+(?:ever\s+)?$",
	re.IGNORECASE,
)


def has_credential_request(text):
	for match in CREDENTIAL_REQUEST.finditer(text):
		prefix = text[max(0, match.start() - 60):match.start()]

		if not NEGATED_REQUEST.search(prefix):
			return True

	return False


def assess_email(email):
	text = unicodedata.normalize(
		"NFKC",
		f"{email.subject}\n{email.body}",
	)

	signals = []

	if URL.search(text) and has_credential_request(text):
		signals.append("credential_request_with_url")

	sentences = re.split(r"[.!?\n]+", text)

	if any(
		AGENT_TARGET.search(sentence)
		and AGENT_COMMAND.search(sentence)
		for sentence in sentences
	):
		signals.append("explicit_agent_instruction")

	return {
		"policy_version": POLICY_VERSION,
		"requires_containment": bool(signals),
		"signals": signals,
	}


def containment_plan(assessment):
	return {
		"reason": (
			"Security precheck requires containment and human review: "
			+ ", ".join(assessment["signals"])
		),
		"actions": [
			{"kind": "move_to_holding"},
			{"kind": "escalate_to_human", "team": "security"},
		],
	}


def apply_security_plan(plan, assessment):
	if not assessment["requires_containment"]:
		return plan

	secured = containment_plan(assessment)

	for action in plan["actions"]:
		if (
			action["kind"] == "escalate_to_human"
			and action.get("team") == "sensitive_requests"
			and action not in secured["actions"]
		):
			secured["actions"].append(action)

	return secured