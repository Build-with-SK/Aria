# ARIA source-security readiness — 2026-10-18

**Verdict: BLOCKED for production release.** Local hardening is implemented and tested; unresolved deployment and source-trust requirements are retained. This report supersedes the grouped assessment of the 49 Bandit findings. The raw scanner gate remains **FAIL**, not waived.

Candidate: `codex/aria-operational-readiness`, isolated from `codex/aria-final-integration` at `edc2796`. Runtime/security implementation: `edfb5e6`. No production merge, push, service restart, broker request, credential change or live enablement.

## All 49 original findings

Each original finding has a stable ID, original file/line, rule, severity, confidence, classification, rationale and disposition in [the review table](security/BANDIT_TRIAGE.md) and [machine-readable inventory](security/BANDIT_TRIAGE.json).

| Classification | Count | Treatment |
| --- | --- | --- |
| Confirmed vulnerability | 7 | Five XML entity-expansion sites fixed; two public-fetch SSRF gaps mitigated, with connection-time DNS rebinding enforcement still open |
| Acceptable documented risk | 23 | Fixed/owner-controlled endpoints, deliberate private inference services, and fixed Jina authority; assumptions recorded individually |
| False positive | 10 | Six SQL sites interpolate only allowlisted identifiers/literal predicates or generated placeholders; four SHA1 sites are stable IDs/cache keys, never authentication |
| Requires further validation | 9 | Two local pickle trust boundaries, five credential-bearing service/provider redirect policies, NSE archive resource handling and V5 provider/egress behavior |
| Total | 49 | No ignored finding or blanket waiver |

The fixed Jina authority is not a demonstrated caller-controlled local SSRF entry point; its transport received the same hardening. A provisional broader classification was corrected during review. Stable ledger IDs are intentionally preserved; replacing their hashes would change historical identities. Truncated IDs must not be treated as adversarial content authenticity.

## Verified changes

- `src/core/xml_security.py`: bounded parser rejects DOCTYPE structurally before entity expansion, including UTF-16 input. All five original external-XML sites use it. Standard-library implementation; no new dependencies. Valid RSS remains supported. Patched Expat is still required: this guard is not a runtime security patch.
- `src/research/transport.py`: initial DNS answers and every redirect are checked for public HTTP(S) destinations; private/mixed answers and HTTPS downgrade are refused; sensitive headers are removed on cross-origin redirects. Central research GET, RSS and Jina fetch use it.
- Residual: DNS can change between preflight resolution and the socket connection. Connection-time egress rules must deny private/link-local/loopback networks for public fetches. Existing trusted local Ollama/SENTINEL traffic needs a separately scoped route. No firewall was changed in this mission. Public proxy resolution and provider behavior also need deployment validation.
- ML summary no longer calls empty, invalidly dated or future-dated rows fresh; resume retains its existing future-stamp rejection.
- Backup refuses a failed SQLite snapshot instead of silently taking raw bytes. Restore rejects Windows absolute/drive-relative/UNC/backslash escape paths and uses tar's data filter. WAL recovery and UNKNOWN-claim preservation are tested on synthetic data. Restore remains an inspection tool: run manifest verification first; archive hashes are integrity checks, not signatures proving archive provenance.

## Scanner evidence — no suppressions

Baseline command: `python -m bandit -r src backend main.py -ll -f json` on `edc2796`: **49 findings, 4 high / 45 medium, exit 1**.

Rerun on `edfb5e6`: **43 findings, 4 high / 39 medium, exit 1; zero scan errors**. B310=29, B608=6, B324=4, B301=2, B314=2. Five original parse sites now share one guarded parser, which produces two remaining Bandit patterns (`fromstring` and `XMLParser`). Three original research URL calls moved into the checked transport, whose policy is verified by regression tests rather than inferred by Bandit. A smaller count is not security certification.

No `nosec`, Bandit rule exclusion, skipped file or severity reduction was added. The scope reproduces the reported 49, not an assertion that every dependency, script, test or runtime surface is secure. Backup code was additionally reviewed and hardened outside that original scan scope.

## Runtime/dependency boundaries

Reused private Windows test venv: CPython 3.14.8 (original manifest/hash preparation evidence is in the integrated reports), Expat 2.8.5 / OpenSSL 3.5.9. Reused private Ubuntu WSL venv: Python 3.14.4; its distro backport status is not certified. Exact runtime probes, current pip/npm audit outputs and complete regression results are in ARIA_FINAL_TEST_RESULTS and the private evidence directory.

The existing deployment's Python 3.11.8 and packages were not changed. Optional Chroma/Torch/OAuth/model stacks remain outside the audited base; trusted pickle files are not safe inputs from arbitrary users. Validate actual filesystem ACLs, writers and artifact provenance before enabling these consumers. No untrusted model/pickle ingestion is authorized.

Anonymous/owner/step-up separation, live ceiling, kill switch, UNKNOWN quarantine, risk/reduce-only gates and no-auto-approval invariants remain unchanged and are covered by the full suites. Local live trading defaults to RESEARCH and cannot reach live without the existing explicit external switch. This mission does not certify the running deployment's process environment or grant a cutover.
