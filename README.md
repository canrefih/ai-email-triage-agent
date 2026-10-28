# AI Email-Triage Agent

I developed this Python prototype to triage inbound emails using a local
LLM, explicit business policies and controlled action execution.

The prototype demonstrates structured classification, human escalation,
audit logging, repeat-run protection and a narrow security gate that can
override an unsafe action plan.

## Current status

- Local LLM integration exercised with Ollama and `llama3.2:3b`.
- Structured model responses validated with Pydantic.
- Deterministic business policies for routing and escalation.
- Mock mailbox, acknowledgments, tickets and notifications.
- SQLite decision records and action ledger.
- Completed actions skipped on subsequent runs.
- 74 passing automated tests.
- Eight original demonstration scenarios.
- Eight additional evaluation scenarios, now retained as regression cases.

The local LLM runs through Ollama on my workstation using `llama3.2:3b`.
No company-provided LLM or future model-service integration is required.

## Scope

Classification can run in either mode:

| Mode | Classification | Mailbox and external actions |
|---|---|---|
| `mock` | Predefined decisions | Mocked |
| `local` | Real local LLM responses | Mocked |

Exchange Online integration is documented using Microsoft Graph.
A live Graph adapter is not implemented, consistent with the exercise's
requirement to implement the core against a mocked mailbox.

The demonstrated local mode connects to Ollama at
`http://localhost:11434/v1`. No cloud LLM is used.
The base URL is configurable; the application does not enforce a network boundary.

## Requirements

- Python 3.12 or later.
- PowerShell for the commands below.
- Internet access for dependency installation and initial model download.
- Ollama with `llama3.2:3b` for the demonstrated local configuration.

Run project commands from the repository root.

## Setup

Create a virtual environment and install the application:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Check dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

The commands use the virtual environment's Python directly.
Activation is optional.

Ollama is installed separately as a Windows application, outside `.venv`.

## Run automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The current suite passed 74 tests covering:

- Decision-schema validation.
- Routing and acknowledgment policies.
- Sensitive-topic escalation and refund thresholds.
- Ambiguity and classification failure.
- Tool failure isolation, independent escalations and repeat-run protection.
- Conflicting safety-rule precedence and explicit retry of failed classifications.
- Security signals, selected benign cases and a negation case.
- Blocking a mock acknowledgment despite a compromised classification.

These tests do not measure general LLM accuracy. Most use deterministic
inputs rather than calling a model.

## Run the mock demonstration

```powershell
.\.venv\Scripts\python.exe -m triage.app `
    --mode mock `
    --db runtime/demo.sqlite
```

With a fresh database, the original eight emails produce thirteen mock
actions. Running the same command again skips completed actions.

Use a new database filename for a fresh classification and execution run.

### Original demonstration scenarios

| ID | Scenario | Expected final handling |
|---|---|---|
| e01 | Invoice request | Finance + acknowledgment |
| e02 | Urgent production outage | Human escalation + ticket + Support |
| e03 | Account activation | Onboarding + acknowledgment |
| e04 | Complaint | Support; no automatic reply |
| e05 | Phishing | Holding + Quarantine + security escalation |
| e06 | Personal data deletion | Human escalation only |
| e07 | Ambiguous request | Needs review |
| e08 | Above-threshold refund | Human escalation only |

All replies, tickets and notifications are simulated locally.

## Run with Ollama

Install Ollama separately, then download the model:

```powershell
ollama pull llama3.2:3b
```

Warm up the model:

```powershell
ollama run llama3.2:3b "Reply with exactly: READY"
```

Verify that the local API is available:

```powershell
Invoke-RestMethod "http://localhost:11434/v1/models"
```

Start a fresh local LLM run:

```powershell
$localRun = Get-Date -Format "yyyyMMdd-HHmmss-fff"

.\.venv\Scripts\python.exe -m triage.app `
    --mode local `
    --model "llama3.2:3b" `
    --base-url "http://localhost:11434/v1" `
    --db "runtime/llama-$localRun.sqlite"
```

Run the additional regression scenarios:

```powershell
$evalRun = Get-Date -Format "yyyyMMdd-HHmmss-fff"

.\.venv\Scripts\python.exe -m triage.app `
    --mode local `
    --data data/evaluation.json `
    --model "llama3.2:3b" `
    --base-url "http://localhost:11434/v1" `
    --db "runtime/evaluation-$evalRun.sqlite"
```

No API key is required for the demonstrated local Ollama configuration.

## Retry failed classifications

Classification failures are saved and routed to human review.
By default, subsequent runs reuse the saved result.

To explicitly retry cached classification failures, use the same
database with:

```powershell
.\.venv\Scripts\python.exe -m triage.app `
    --mode local `
    --model "llama3.2:3b" `
    --db "runtime/EXISTING_DATABASE.sqlite" `
    --retry-classification-errors
```

This option retries only failed classifications. It preserves successful
cached decisions and does not repeat completed actions.

It does not reconsider valid but incorrect model decisions or provide
a human approval workflow. Reclassification replaces the current decision
record; previous action records remain.

### Verify a local run

For each report:

- Confirm `source` is `local_llm`.
- Confirm `decision` is not null.
- Compare the classification with the email.
- Inspect `security`, `original_plan` and the final `plan`.
- Check action results for failures or reconciliation requirements.

The source label alone does not prove successful classification.
Model errors also record `source: local_llm`.

A null decision with Needs review is a fallback, not successful model
integration. Inspect the report's `error` field.

Model confidence is self-reported and is not a calibrated probability.

### Cached decisions and repeat runs

Use a fresh database to obtain new model responses.

Reusing a database reuses saved classifications and skips completed
actions. It verifies repeat-run protection, not model consistency.

The security gate is reassessed before execution, including when a
classification is cached. It cannot undo actions already completed.

## Ollama troubleshooting

| Symptom | Check |
|---|---|
| Connection error or timeout | Ollama is running and localhost:11434 is reachable |
| Timeout during inference | Model warm-up, hardware speed and configured timeout |
| Model not found | Confirm `llama3.2:3b` appears in `ollama list` |
| Request rejected | Ollama version, local API URL and JSON-schema support |
| Validation error | Model output against the Decision schema |
| Old decisions or skipped actions | Use a fresh database filename |

The default request timeout is 20 seconds, with at most one SDK retry
for eligible failures. Reports record exception types rather than
detailed server diagnostics.

## Architecture

```text
Email + limited sender history
             |
             v
        LLM classifier
             |
             v
      Pydantic validation
             |
             v
        Business policy
             |
             v
 Security gate checks the email
 and may override the action plan
             |
             v
       Action executor
             |
             v
 Mock effects + SQLite records
```

The LLM supplies interpretation. Application policies authorize actions.

The security gate runs before action execution; it does not prevent
the model from reading or being influenced by malicious email content.

See [architecture](docs/architecture.md) and the
[Exchange integration design](docs/exchange-integration.md).

## Security gate

The current gate checks for narrow English-language patterns:

- A credential-entry request together with an HTTP/HTTPS URL.
- An explicit command targeting an automated assistant.

Detected signals replace normal business actions with Holding and
security escalation. An existing sensitive-request escalation is
preserved.

The implementation includes limited handling of negated requests such
as “Never share your password.”

This is an additional safeguard, not a general phishing detector.
Alternative wording, other languages and obfuscation may bypass it.
Legitimate quoted or instructional content may also trigger it.

The application does not fetch email links or execute attachments.

## Local LLM findings

After prompt refinement, the original eight development scenarios
produced the expected action plans in the observed run.

On the additional eight scenarios, the initial evaluation met seven
acceptance checks. In v08, the model followed an instruction embedded
in a phishing email and triggered a mock acknowledgment.

After adding the security gate, all eight final action plans met the
scenario expectations in the observed regression run.

The model still misclassified v08 as onboarding. The gate blocked the
acknowledgment and applied containment. The original decision remained
visible in the audit record.

This demonstrates containment of that observed failure, not general
prompt-injection resistance. Since these examples informed changes,
they are now regression cases rather than an independent evaluation set.

See [evaluation details](docs/evaluation.md).

## Traceability

SQLite stores:

- Synthetic email content and mailbox state.
- Classification, confidence, reason and source.
- Original and final action plans.
- Security policy version and detected signals.
- Action status, mock effects and timestamps.

Action states are updated during processing. This is a prototype ledger,
not an immutable audit trail containing every historical attempt.

## Design assumptions

- Confidence threshold: 0.80, illustrative and uncalibrated.
- Refund threshold: CHF 500.00, illustrative.
- Refunds strictly above the threshold require human escalation.
- Unknown refund amounts or a currency mismatch also escalate.
- Data-protection requests, deletion requests and legal threats suppress
  normal business actions and require human escalation.
- Other, ambiguous, low-confidence and failed classifications go to review,
  unless a higher-priority containment or sensitive-handling rule applies.
- Phishing containment takes precedence over ordinary routing.
- Acknowledgments are fixed receipt confirmations, not refund approvals.
- Sensitive-topic policy depends on accurate model extraction.
- Sender history in the mock is limited metadata, ordered by ID;
  chronological history retrieval remains a production requirement.

## Deliverables and execution evidence

| Location | Content |
|---|---|
| `src/triage/` | Application, models, settings, separate prompts and security gate |
| `tests/` | Automated tests |
| `data/emails.json` | Original scenarios and mock classifications |
| `data/evaluation.json` | Additional regression scenarios |
| `docs/architecture.md` | Compact architecture and five-line production note |
| `docs/exchange-integration.md` | Detailed Graph integration design |
| `docs/presentation.md` | Five-minute nontechnical presentation notes |
| `docs/evaluation.md` | Acceptance criteria and evaluation findings |
| `docs/test-cases.md` | Categorized automated test cases and coverage limits |
| `artifacts/` | Saved test results, verified LLM traces and mock recovery evidence |

The three original LLM traces are preserved evidence from earlier real Ollama
executions. They have not been relabeled as new inference runs. Their final
plans and security assessments were verified against the current implementation:

- `first-run.jsonl`: fresh model decisions and mock actions.
- `second-run.jsonl`: cached decisions and skipped completed actions.
- `evaluation-run.jsonl`: additional regression scenarios, including v08 containment.
- `first-run-view.json` and `evaluation-run-view.json`: formatted JSON copies.
- `tests.txt`: current automated test result.
- `demo-check.txt`: verification of current policies against preserved traces.
- `recovery-run-view.json`: current mock failure/recovery evidence, formatted for presentation.

The security gate runs in every mode, including runs with cached decisions.

## Failure handling and action statuses

A failed action or an action needing reconciliation blocks subsequent
non-escalation actions for that email. Independent human/security escalations
remain eligible to execute. A Holding failure therefore does not suppress
the security alert, while a Finance routing failure blocks acknowledgment.

| Status | Meaning |
|---|---|
| `done` | Mock action completed |
| `skipped` | Action completed on an earlier run |
| `failed` | Attempted action raised an error |
| `blocked` | Action was not attempted because a prior action failed or needs reconciliation |
| `needs_reconciliation` | An earlier running state requires outcome investigation |

Blocked results appear in the returned report; they do not create separate
rows in the action ledger. Notification failures are recorded, not guaranteed
to reach a person. A subsequent run retries failed actions but skips completed ones.

## Production boundaries

The implementation is a single-worker prototype.

Before production use, additional work includes:

- Live Graph, ticket and notification adapters.
- Mailbox-scoped authorization and reliable synchronization.
- Durable escalation delivery and operational monitoring.
- Reconciliation of uncertain remote action outcomes.
- Human approval and reconsideration of successful but incorrect decisions.
  Explicit retry of failed classifications is already implemented.
- Concurrency control.
- Broader model and security evaluation.
- Versioned models, prompts and policies.
- Credential protection, encryption and retention controls.

Mock effects and completion records share a local transaction.
Real remote operations cannot share it, so the prototype does not
provide an exactly-once guarantee for external actions.

Real mailbox use should begin with human approval of outbound actions.
Automatic replies require further evaluation and operational controls.
