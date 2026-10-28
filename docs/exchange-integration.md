# Exchange Online Integration Design

This is the exercise-required integration design, not a live adapter.
The LLM remains local Ollama; no company LLM integration is planned.

## Exchange Online integration - planned

A Graph-backed mailbox adapter will replace mock mailbox operations.
For unattended execution, use a Microsoft Entra application with
certificate-based client credentials.

Scope Exchange Application RBAC roles `Application Mail.ReadWrite`
and `Application Mail.Send` to the shared mailbox. Avoid overlapping
unrestricted grants and verify access boundaries before deployment.

| Tool | Planned integration |
|---|---|
| `get_email` | `GET /users/{mailbox}/messages/{id}` |
| `search_sender_history` | Retrieve a bounded set of earlier messages from the same sender; use timestamps and bounded pagination. |
| `route` | `POST /users/{mailbox}/messages/{id}/move`, using a configured folder ID. |
| `send_ack` | `POST /users/{mailbox}/messages/{id}/reply`, using a fixed template and approved recipient handling. |
| `move_to_holding` | Add Quarantine while preserving existing categories, then move to Holding; track the two operations separately. |
| `create_ticket` | Separate internal ticket-system adapter. |
| `escalate_to_human` | Separate durable notification adapter for approved support/security destinations. |

Validate required folder IDs at startup. Begin with bounded polling;
production ingestion should persist delta synchronization state.
Unread status must not be the processing ledger.

Use `Prefer: IdType="ImmutableId"` on applicable requests and identify
work by mailbox plus immutable message ID. This preserves tracking
across folder moves within the same mailbox.


## Error isolation and remote effects

Security and human notifications must not depend on successful mailbox
movement. Track category updates, moves and notifications separately.
Respect Graph Retry-After and use bounded retries for eligible failures.
Uncertain remote write outcomes require reconciliation, not blind replay.
The local mock transaction does not extend to Graph or a ticket service.
