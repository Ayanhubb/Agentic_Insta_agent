# Policy engine

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Approval engine](AGENT_PLATFORM_APPROVAL_ENGINE.md), [Security](AGENT_PLATFORM_SECURITY.md), [Decisions](AGENT_PLATFORM_DECISIONS.md), [Workflow engine](AGENT_PLATFORM_WORKFLOW_ENGINE.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

Inspection date: 2026-09-25. Package: `agent_platform.policy`.

## What this is

**IMPLEMENTED.** An in-process policy engine. A registered policy returns an opinion. The engine turns that opinion into `APPROVED`, `PENDING`, or `REJECTED`.

```text
Agent
  ↓
Workflow
  ↓
Policy evaluation
  ↓
APPROVED / PENDING / REJECTED
  ↓
next step
```

It is not wired into `create_app`, HTTP routes, or the scheduler. It does not import `InstagramAgent`, `enqueue_instagram_publication`, or `scheduler.approval_policy.decide_approval`. The studio scheduler still uses `decide_approval` in `scheduler/approval_policy.py`. That function is unchanged.

`next_action` reports whether the next step may continue, must wait, or must stop. `executes_effect` is false. This package does not run the effect.

## Modes

| Mode | Engine result when the policy allows the request |
| --- | --- |
| `AUTO` | `APPROVED` for a non-publishing domain whose capability rule, if any, already holds |
| `MANUAL` | `PENDING` until the [approval engine](AGENT_PLATFORM_APPROVAL_ENGINE.md) records a human decision |
| `CONDITIONAL` | `APPROVED` when every structured condition holds, for a non-publishing domain. A failed condition is `REJECTED` |

`HUMAN_APPROVAL` is manual. A definition that marks it automatic is rejected at registration.

A deny outcome is `REJECTED` with reason `policy_denied` in every mode.

## Domains

| Domain | Effect the request must declare | Capability |
| --- | --- | --- |
| `tool_access` | `none` | No |
| `data_access` | `none` | No |
| `external_action` | `external` | Explicit grant required |
| `publishing` | `publishing` | Explicit `instagram.publish` grant required. Never automatic |
| `financial_action` | `financial` | Explicit grant required |
| `content_approval` | `none` | No |
| `asset_requirement` | `none` | A missing required fact is `asset_requirement_missing` |
| `human_approval` | `none` | Manual |

The request's effect must match the domain. A tool-access policy id cannot be evaluated as publishing. That mismatch raises `PolicyMismatch` and does not approve.

Conditions compare sanitized facts. Operators are `eq`, `neq`, `exists`, `not_exists`, and `in`. The field is a dotted path. `__` is rejected. There is no expression string.

## Interfaces

| Type | Role |
| --- | --- |
| `Policy` | `definition` plus `evaluate(subject) -> PolicyVerdict` |
| `PolicyEvaluator` | `evaluate(request, tenant) -> PolicyDecision` and `next_action(decision) -> NextStep` |
| `DeclarativePolicy` | The data-backed `Policy`. Server code registers it |
| `PolicyRegistry` | Stores policies and capability grants. A request cannot add either |
| `PolicyEngine` | The evaluator. It applies the rules below after the policy returns |

`PolicyVerdict` is `allow`, `deny`, or `needs_human`. It is not a status. The engine chooses `APPROVED`, `PENDING`, or `REJECTED`. A verdict of another type raises `InvalidPolicyResult` and is not treated as approval.

## Publishing

The generic platform does not grant publishing permission automatically.

- `PolicyMode.AUTO` together with `PolicyDomain.PUBLISHING` cannot be constructed. Registration fails with `Publishing cannot be automatic.`
- A policy document that includes `publish` or any other bypass key is `PolicyBypass`.
- When the Instagram checks below pass, `evaluate` returns `PENDING` with reason `approval_required`. It does not return `APPROVED`.
- `next_action` on a publishing decision waits or stops. It does not continue, and `publishing_granted` stays false.
- A caller-built `PolicyDecision` with status `APPROVED` and domain `publishing` still waits. The engine does not honor that object as a grant.

Publishing can become eligible for a later human decision only when all of these hold:

| Check | Required value |
| --- | --- |
| Agent | `instagram_marketing` |
| Workflow | `studio.approve`, `direct.publish`, `scheduler.campaign`, or `instagram.publish` |
| Capability | `instagram.publish`, granted in server code to that agent and that workflow |

Any other agent is `unauthorized_publisher`. `studio.generate`, `studio.regenerate`, and `trend.cycle` are `unauthorized_workflow`. A missing grant, a missing capability, or a publishing tool name is `unauthorized_side_effect`.

The only publishing capability token is `instagram.publish`. These names cannot be registered as capabilities:

- `validate_image`, `prepare_image`, `upload_image`, `create_instagram_media`, `publish_instagram_media`, `verify_publication`
- `media_publish`, `instagram_publish`, `publish`, `post_to_instagram`, `create_media`, `publish_media`
- `enqueue_instagram_publication`, `instagram_graph_client`
- `eval`, `exec`, `compile`, and `os` / `subprocess` entry points

`instagram.publish` is a permission token. It is not a tool call. The six Instagram tools stay on `agent/registry.py`.

A failed condition on a publishing policy is `REJECTED`. The approval engine will not turn that record into an approval.

## External side effects

`external_action` and `financial_action` require a capability name on the policy and a matching grant:

```text
registry.grant(agent_id=..., capability=..., workflows=...)
```

The grant is server code. The request cannot carry the grant. Without it, the decision is `REJECTED` / `unauthorized_side_effect` and the next step stops. With it, `AUTO` may approve that non-publishing effect. `publishing_granted` stays false, and `executes_effect` stays false.

An external policy cannot use the capability `instagram.publish`.

## Tenancy and bypass

The tenant argument is a `TenantContext` from `authenticated`, `scheduler`, or `system`. A tenant id inside facts is `PolicyBypass`. The engine does not adopt it.

Facts and policy documents are rejected when they contain:

- secret-like or code-like keys (`token`, `api_key`, `eval`, `handler`, and the same family)
- bypass keys (`publish`, `approved`, `decision`, `skip_policy`, `auto_publish`, `tenant_id`, and the same family)

The policy implementation sees sanitized facts only. The engine re-checks declared conditions, so a registered policy that returns `allow` while a condition fails is still `REJECTED`.

Manual mode stays `PENDING` when a policy returns `allow`. The mode on the registered definition is the mode that counts.

## Tests

`tests/test_policy_approval.py` covers manual approval, automatic approval, conditional facts, a denied policy, a missing approval, an unauthorized side effect, tenant isolation, bypass attempts, and the publishing rules above. A source check confirms this package does not import the Instagram publisher.

## Not in this change

- No change to `decide_approval`, `PublicationService`, or `InstagramAgent`
- No route that accepts a policy document from a client
- No Graph call and no `publish=true` field on `PolicyDecision`
