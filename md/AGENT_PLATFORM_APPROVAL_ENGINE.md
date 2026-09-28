# Approval engine

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Policy engine](AGENT_PLATFORM_POLICY_ENGINE.md), [Security](AGENT_PLATFORM_SECURITY.md), [Decisions](AGENT_PLATFORM_DECISIONS.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

Inspection date: 2026-09-25. Package: `agent_platform.approval`.

## What this is

**IMPLEMENTED.** An in-process store for policy decisions and human approvals.

```text
Policy evaluation
  ↓
APPROVED / PENDING / REJECTED
  ↓
Approval record
  ↓
next step: continue / wait / stop
```

`ApprovalGate` is the interface: `submit`, `decide`, `gate`, and `require_continue`. `ApprovalEngine` is the implementation. It calls `PolicyEngine.evaluate`. It does not accept a pre-built decision, so a caller cannot submit `status=approved` on their own.

The package does not import `InstagramAgent` or `enqueue_instagram_publication`. A continue step has `executes_effect` false. Recording an approval does not call Meta.

The studio routes still approve through `POST /generation/{image_id}/approve` and `decide_approval`. This engine is not mounted on those routes.

## Records

`ApprovalRecord` stores the tenant, policy id, mode, domain, agent, workflow, status, reason, capability, and the human actor when one has decided. It does not store facts, tokens, or a handler.

| Policy status | Record | `gate` | `require_continue` |
| --- | --- | --- | --- |
| `APPROVED` | Already approved. Used for `AUTO` on a non-publishing domain | `continue` | Returns that step |
| `PENDING` | Waiting for a person | `wait` | `MissingApproval` |
| `REJECTED` | Denied policy, failed condition, or unauthorized effect | `stop` | `PolicyDenied` |

`decide` accepts a record only while it is `PENDING`. A second decision, or a decision on a rejected record, raises `InvalidApprovalState`. The rejected record stays rejected.

## Human decision

`decide(tenant, approval_id, actor_id=..., approved=True)` records `human_approved`. `approved=False` records `human_rejected` and the next step stops.

The actor is a person. These actors are rejected as `PolicyBypass`, and the record stays pending:

- `auto`, `system`, `scheduler`, `agent`, `publish`, `policy`
- `instagram_marketing`
- the same id as the agent on the record

A bypass attempt does not approve the record.

## Publishing

Publishing permission is not granted automatically.

`submit` of a publishing evaluation stores `PENDING` or `REJECTED`. If a decision ever arrived already `APPROVED` for the publishing domain, it is stored as `PENDING` with reason `approval_required`.

`decide(approved=True)` sets `APPROVED` only when `publishing_block_reason` is empty:

- the policy is still a publishing policy and is not automatic and is not a deny
- the agent is `instagram_marketing`
- the workflow is an approved publication workflow: `studio.approve`, `direct.publish`, `scheduler.campaign`, or `instagram.publish`
- the capability is `instagram.publish` and that grant is still registered for the agent and the workflow

When any check fails, the record becomes `REJECTED` and `publishing_granted` stays false.

When the checks pass and the human approved, `gate` returns `continue` with `publishing_granted` true. That flag means the control plane may invoke the existing Instagram publication path. This package does not invoke it. `executes_effect` remains false.

A failed publishing condition never becomes pending, so there is no human override of that gate.

## Tenant isolation

Records are keyed by id and `tenant_id` from the trusted `TenantContext`. `get`, `gate`, `decide`, and `require_continue` require that same tenant. A different tenant receives `ApprovalNotFound`, including when the id exists. The owner's record is unchanged.

## Tests

`tests/test_policy_approval.py` covers:

| Case | Result |
| --- | --- |
| Manual approval | `PENDING`, then `continue` after `reviewer-1` approves |
| Automatic approval | `APPROVED` and `continue` with `publishing_granted` false |
| Denied policy | `stop`, and `decide` cannot overturn it |
| Missing approval | `MissingApproval` until a human decides |
| Unauthorized side effect | `unauthorized_side_effect` until a server grant exists |
| Tenant isolation | The other tenant receives `ApprovalNotFound` |
| Policy bypass | Bypass keys, a lying policy, a forged publishing decision, and self-approval |

## Not in this change

- No new approval route and no change to `scheduler/approval_policy.py`
- No Graph client and no call to `enqueue_instagram_publication`
- No organization tenant. The tenant remains `users.id` when a route eventually calls this engine
