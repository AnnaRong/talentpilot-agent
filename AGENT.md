# TalentPilot Runtime Contract v0.3

## Architecture

`runtime.AGENTS` is the authoritative registry of goals, tools, memory and permissions.
Five specialists execute deterministic business code; `analyst` optionally uses a bounded
model/tool loop. This is a hybrid recruiting application, not a swarm of autonomous LLMs.

The orchestrator selects an explicit pipeline, claims a durable job with a SQLite transaction,
checks tool-call budget/deadline, emits events, saves agent-specific checkpoints and terminates.
Only pure, local read computations may be retried after a worker lease expires.
External hiring actions must never be placed into an automatic retry loop.

## Shared State

Immutable datasets contain verified positions, stable candidate identities, applications and
interviews. Agent memories are keyed by agent + dataset + options. Time-sensitive lock,
load and talent-pool checks are not cached. New payloads are encrypted at rest.
The current implementation is single-host, single-worker; it is not a distributed event bus.

## Evidence Rules

- Join application/candidate/job stable IDs. Names and filenames are not identity.
- Current stage inventory is not historical transition evidence.
- Only completed explicit pass/reject outcomes enter pass-rate denominators.
- Personal evaluation and aggregate interview outcome are distinct.
- An Offer inventory row does not establish that an Offer was sent.
- Unknown timestamps/timezones, missing actors and absent feedback stay unknown.
- Model numeric claims require exact tool citations; narrative is never automatically verified.

## Approval Rules

New actions are immutable envelopes bound to dataset ID, action parameters, payload hash,
requesting user and expiry. Only a scoped lead/admin may decide. Decisions are one-way.
Execution requires an approved, fresh, untampered, unconsumed envelope.
Unconfigured external connectors always block; approval alone does not authorize a guessed API.
The old approval endpoints and descriptive-only workflow are retired.

## Privacy and Recording

The local vault requires purpose, policy version, consent evidence/time, region and expiry.
This is not a GDPR/CCPA compliance certification or a complete overseas application portal.
Deleting from the local vault does not delete upstream ATS, exports, recordings or backups.

Default audit produces persistent events, not a video. Native whole-desktop recording is off.
Never represent a started process, an event HTML file or a nonempty movie as verified recording
of the intended recruiting tab. Do not capture credentials, tokens or unrelated windows.

## External Integration

No live BOSS/iTalent write/Infoflow/MCP adapter is enabled in this release.
The adapter must establish account/tenant, authorized tool/API contract, stable IDs, current
state, scope, approval, rate limits and explicit post-action read-back before enabling writes.
CAPTCHA, QR login, SMS and 2FA always require user handoff.
