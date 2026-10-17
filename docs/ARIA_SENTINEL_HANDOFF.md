# ARIA → SENTINEL handoff

ARIA remains a separate trading specialist. SENTINEL integration can proceed against the constrained interfaces below while ARIA's deployment/external gates remain blocked. This document grants no credentials or execution authority.

## Stable integration boundary

`src/auth/policy.py:SERVICE_ROUTES` is the exact method/path allowlist, protected by a distinct `ARIA_SERVICE_TOKEN` sent in the Authorization header. Do not give SENTINEL an owner token/cookie, broker key, filesystem write access or a generic HTTP/shell execution tool.

| Method/path | Use and limits |
| --- | --- |
| GET /health | Liveness only; not readiness, account connectivity or trading approval. |
| GET /api/summary | Current signal summary; preserve freshness/unavailable information. |
| GET /api/brain/status | Brain/model status. Does not authorize brain controls. |
| POST /api/chat/local | Stateless local consultation; no vault context or recorded trade intent. |
| POST /api/specialist/analysis | Structured specialist consultation; preserve source ages, missing evidence and uncertainty. |

These are existing interfaces; no new route/permission was added. `/ready` is an operator readiness check, outside the service-token allowlist. `/api/execute/queue` is also deliberately unavailable to SERVICE; a403 means refused, not an empty book. All execution, broker, trading, desk, vault, portfolio, long-term memory and owner-management operations remain denied. Service token equal to either owner or reverse-direction consultant token is refused, including hashed owner credential equality.

Use bounded, read-only DTO snapshots. Treat model/advisor text as untrusted data; never parse it into approvals, mode changes or broker calls. Preserve observed timestamps and row ages; distinguish stale, missing, withheld, transport failure and zero. A client's retry of analysis is not authority to retry an order. No UI control or tool may call approve_and_execute autonomously.

## ARIA consulting SENTINEL

Existing `src/consult/sentinel_client.py` uses urllib HTTP; the reverse credential is `SENTINEL_SERVICE_TOKEN` (legacy `SENTINEL_CONSULT_TOKEN`). `SENTINEL_URL` defaults to local port8300. Existing endpoints: POST `/api/consult` and `/api/consult/outcome`; transport and malformed-response handling remain bounded/advisory. Automatic trading-path consultation stays opt-in through `ARIA_SENTINEL_CONSULT`.

Availability is separate from stance: AVAILABLE, UNAVAILABLE, TIMEOUT, MALFORMED, UNAUTHORISED, NO_TOKEN, ERROR, NOT_CONSULTED. Silence is never agreement or permission. A stated AGREE is advice only. Preserve ARIA-owned status fields against advisor-supplied overrides; redact secrets before outbound payloads. No external SENTINEL runtime was started or newly authenticated in this mission.

## Verification and next work

Existing service-role, specialist, sentinel-protocol/bridge and transcript tests run in the integrated full suite. Contract/malformed replies and no-intent/no-vault behavior are covered with local fixtures. Local browser/proxy acceptance does not establish the real peer's current deployment status.

Build SENTINEL tools as the five named operations above, with response schemas, deadlines, cancellation and explicit error states. Add contract tests proving execution/vault routes stay forbidden. Never expand a prefix allowlist or delegate the human approval step. Any broader access, hosted connection, credentials or order tests need a separate owner decision.
