# Automated Test Cases and Coverage

## How to read these tables

Each parameter value represents a separate pytest case.
The Count column therefore adds up to the reported 60 tests.

PASS refers to the supplied automated test execution.
Most tests use deterministic data and mocks rather than a live LLM.

For negative tests, rejecting the input or raising the expected
validation error is the successful outcome.

## A. Decision schema - `test_models.py`

### A1. Accepted inputs

| Test / case | Input or scenario | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_valid_decision_is_accepted` | Valid billing decision as a Python dictionary | Accepted; intent, priority and confidence preserved | 1 | PASS |
| `test_valid_json_is_accepted` | Valid decision encoded as JSON | Parsed and accepted as billing | 1 | PASS |
| `test_sensitive_refund_is_representable` | Refund of CHF 750.00 with refund topic | Accepted; amount and currency preserved | 1 | PASS |

### A2. Invalid confidence

| Test | Parameter values | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_invalid_confidence_is_rejected` | `-0.01`, `1.01`, `"0.92"`, `True`, `None` | Each value raises `ValidationError` | 5 | PASS |

### A3. Invalid field values

All rows below belong to `test_invalid_fields_are_rejected`.

| Field | Invalid value | Expected assertion | Count | Result |
|---|---|---|---:|---|
| intent | `sales` | Reject undefined category | 1 | PASS |
| priority | `critical` | Reject undefined priority | 1 | PASS |
| ambiguous | `"false"` | Reject string instead of boolean | 1 | PASS |
| sensitive_topics | `["unknown_topic"]` | Reject undefined topic | 1 | PASS |
| refund_amount | `"-100.00"` | Reject negative amount format | 1 | PASS |
| refund_amount | `"100.123"` | Reject more than two decimal places | 1 | PASS |
| refund_currency | `"chf"` | Reject lowercase currency format | 1 | PASS |
| reason | `""` | Reject empty reason | 1 | PASS |

### A4. Required and additional fields

| Test | Parameter values or scenario | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_missing_required_fields_are_rejected` | Remove each of: intent, priority, confidence, ambiguous, sensitive_topics, refund_amount, refund_currency, reason | Every omission raises `ValidationError` | 8 | PASS |
| `test_unexpected_action_field_is_rejected` | Add `action="send_ack"` | Reject field outside the decision contract | 1 | PASS |
| **Schema subtotal** | | | **25** | **PASS** |

Nullable refund fields must still be present. A null value means
unknown or not applicable; an omitted field violates the contract.

Rejecting an extra action field protects the schema. Actual action
authorization is enforced separately by application policy.

## B. Agent behavior - `test_agent.py`

### B1. End-to-end routing

All rows below belong to `test_end_to_end_routes`.

The tests check the final mailbox folder, expected effect types,
and successful action statuses.

| Email | Scenario | Expected folder and effect types | Count | Result |
|---|---|---|---:|---|
| e01 | Billing | Finance; route + acknowledgment | 1 | PASS |
| e02 | Urgent outage | Support; route + ticket + human escalation | 1 | PASS |
| e03 | Activation | Onboarding; route + acknowledgment | 1 | PASS |
| e04 | Complaint | Support; route only | 1 | PASS |
| e05 | Phishing | Holding; containment + human escalation | 1 | PASS |
| e06 | Data deletion | Inbox unchanged; human escalation only | 1 | PASS |
| e07 | Ambiguous request | Needs review; route only | 1 | PASS |
| e08 | Above-threshold refund | Inbox unchanged; human escalation only | 1 | PASS |

### B2. Safety, uncertainty and reliability

| Test | Scenario / parameter values | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_phishing_has_quarantine_and_no_reply` | Phishing email | Quarantine category present; no acknowledgment effect | 1 | PASS |
| `test_repeat_run_does_not_duplicate_effects` | Process all eight emails twice | Second run skips completed actions; effect count unchanged | 1 | PASS |
| `test_classification_failure_goes_to_review` | Classifier raises simulated `TimeoutError` | Null decision, recorded error, Needs review, route only | 1 | PASS |
| `test_uncertainty_blocks_business_actions` | Confidence 0.2; ambiguous=true; intent=other | Each case produces Needs review only | 3 | PASS |
| `test_sensitive_topics_block_normal_actions` | data_protection; data_deletion; legal_threat | Each case produces sensitive-request escalation only | 3 | PASS |

### B3. Refund boundaries

All rows below belong to `test_refund_threshold`.

| Amount | Currency | Expected assertion | Count | Result |
|---|---|---|---:|---|
| 499.99 | CHF | No threshold-based escalation | 1 | PASS |
| 500.00 | CHF | No threshold-based escalation | 1 | PASS |
| 500.01 | CHF | Escalation; no acknowledgment | 1 | PASS |
| Unknown | CHF | Escalation; no acknowledgment | 1 | PASS |
| 100.00 | EUR | Currency mismatch causes escalation; no acknowledgment | 1 | PASS |

The configured threshold is illustrative: CHF 500.00.
Amounts strictly above it trigger the threshold rule.

### B4. Outage and tool failure

| Test | Scenario | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_normal_outage_creates_ticket_without_escalation` | Non-urgent technical outage | Ticket then Support routing; no escalation | 1 | PASS |
| `test_tool_failure_stops_later_actions` | Routing raises a simulated failure | Failed result recorded; acknowledgment not executed | 1 | PASS |
| **Agent subtotal** | | | **24** | **PASS** |

## C. Security gate - `test_security.py`

### C1. Positive detection

All rows below belong to `test_detects_explicit_security_signals`.

| Scenario | Expected signal | Count | Result |
|---|---|---:|---|
| Enter mailbox password at an HTTPS URL | `credential_request_with_url` | 1 | PASS |
| Automated assistant instructed to classify and confirm | `explicit_agent_instruction` | 1 | PASS |

### C2. Selected benign inputs

All rows below belong to
`test_ordinary_requests_do_not_trigger_precheck`.

| Scenario | Expected assertion | Count | Result |
|---|---|---:|---|
| Activate an account today | No containment signal | 1 | PASS |
| Production outage requiring immediate help | No containment signal | 1 | PASS |
| Request an invoice copy | No containment signal | 1 | PASS |
| Ask how to reset a password | No containment signal | 1 | PASS |
| “Never share your password” followed by a help URL | No containment signal | 1 | PASS |

### C3. Enforcement and regression cases

| Test | Scenario | Expected assertion | Count | Result |
|---|---|---|---:|---|
| `test_security_override_blocks_acknowledgment` | Unsafe onboarding plan plus detected assistant instruction | Replace plan with Holding + security escalation | 1 | PASS |
| `test_security_override_preserves_sensitive_escalation` | Detected credential request plus existing sensitive escalation | Preserve sensitive escalation in the final plan | 1 | PASS |
| `test_compromised_llm_decision_cannot_send_ack` | Mock classifier incorrectly returns confident onboarding for an attack | Keep original classification visible; execute containment and escalation only; repeat run skips effects | 1 | PASS |
| `test_warning_does_not_hide_an_actual_credential_request` | Benign warning followed by a real password-entry request | Detect the actual credential request | 1 | PASS |
| **Security subtotal** | | | **11** | **PASS** |

## D. Overall automated result

| Category | Cases | Passed | Failed |
|---|---:|---:|---:|
| Decision schema | 25 | 25 | 0 |
| Agent behavior | 24 | 24 | 0 |
| Security gate | 11 | 11 | 0 |
| **Total** | **60** | **60** | **0** |

Run the suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## E. Live local LLM observations

These are separate from the 60 automated tests.

| Exercise | Observed result | Interpretation |
|---|---|---|
| Original eight development emails after prompt refinement | Eight expected final plans | Development-scenario success in the observed run |
| Initial additional evaluation | Seven of eight expected outcomes | Exposed unsafe handling of an injected phishing email |
| Regression after adding the security gate | Eight expected final plans | Gate contained the observed attack; model classification still failed on v08 |

These datasets informed implementation changes and are now regression
cases. They do not provide an independent estimate of production accuracy.

## F. Coverage boundaries

The suite covers every defined intent through workflow scenarios and
checks both urgent and normal outage handling.

It does not exhaust all possible combinations of intent, priority,
sensitive topic, confidence and failure state.

Areas without dedicated automated coverage include:

- Priority-specific behavior for `low`.
- Real LLM transport, schema compatibility and classification quality.
- Real Graph, ticket and notification operations.
- Chronological sender-history retrieval.
- Process-restart and concurrent-worker behavior.
- Remote action reconciliation.
- Broad multilingual or obfuscated attacks.
- All combinations of cached decisions and policy changes.

## Final assessment

The prototype passed 60 automated cases covering the defined decision
contract, representative business workflows, sensitive escalation,
uncertainty, selected failures, repeat-run protection and targeted
security controls.

Local LLM execution additionally demonstrated the end-to-end classification
path and exposed a security failure that was retained as a regression case.

This evidence supports the tested prototype behaviors. It does not claim
exhaustive scenario coverage, production readiness, or general resistance
to phishing and prompt injection.