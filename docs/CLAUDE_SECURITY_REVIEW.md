# Claude security review — ARIA final candidate

Reviewer: Claude Code. Date: 2026-10-18. Candidate `codex/aria-final-integration` @ `edc2796`;
Codex remediation `edfb5e6` (`codex/aria-operational-readiness`) reviewed in §4.
No credential value, `.env` content or vault content was read or reproduced. Probes use
synthetic data only.

## 1. Bandit — independent rerun

`uvx --from bandit==1.8.6 bandit -r src backend main.py -ll -f json`:

| Tree | Exit | Findings | B310 url open | B608 SQL | B314 XML | B324 SHA1 | B301 pickle |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `edc2796` (candidate) | 1 | **49** (45 M / 4 H) | 32 | 6 | 5 | 4 | 2 |
| `edfb5e6` (Codex remediation) | 1 | **43** | 29 | 6 | 2 | 4 | 2 |

The candidate count matches Codex's reported 49 exactly. The scanner still exits 1 after
`edfb5e6`. That is expected and correct: the remaining sites are assessed below, and none
is suppressed.

### Assessment by class (candidate `edc2796`)

| Class | Sites | Verdict | Reasoning |
| --- | --- | --- | --- |
| B310 research fetches | `research/http.py:157`, `sources/rss.py:86`, `sources/web.py:79` | **REAL (candidate) → remediated in `edfb5e6`** | URLs come from feeds, search results and model output. `normalize_public_url` checks only the URL string: a public hostname that resolves to `127.0.0.1`, or a public URL that 302-redirects to loopback or link-local, passed, because `urlopen` follows redirects. Targets: the local Ollama API (unauthenticated), other loopback services, and metadata endpoints on a hosted box. |
| B310 fixed or operator hosts | 26 sites: Ollama base (`OLLAMA_BASE`), arXiv, Reddit OAuth (fixed host), NewsAPI, LSE, FX, universe vendors, ntfy, Pushover, launch-check base URL, Anthropic provider, SENTINEL | **False positive / accepted**, except S-04 | The host is fixed in code or set by the operator. Ticker strings reach some paths and queries; they are URL-encoded where it matters. |
| B314 XML | `research/sources/{rss,news,reddit}.py`, `political/fetchers/senate_efts.py`, `brain/quant_lab.py` | **REAL (candidate) → remediated in `edfb5e6`** | Untrusted feed XML was parsed with `ElementTree.fromstring`. XXE is not possible with stdlib ElementTree, but entity amplification and expat parser CVEs depend on the runtime's expat. The deployed runtime is CPython 3.11.8 with **Expat 2.5.0** (per ARIA_SECURITY_READINESS), which predates the 2.6.3 to 2.7.x security fixes. |
| B608 SQL | `brain/data_generator.py:89`, `data/universe.py:553,774,845,1373`, `database/database.py:501` | **False positive** | Each one interpolates a constant table or column name from a fixed tuple, or a `?` placeholder list. Every value is bound. |
| B324 SHA1 | `core/ingest.py:63`, `core/ledger.py:232`, `data/lse_data.py:53`, `intelligence/research/pit_data.py:485` | **False positive** | Event identity and cache-file keys only, never authentication or integrity against an adversary. Add `usedforsecurity=False` so the intent is in the code. |
| B301 pickle | `brain/quant_lab.py:379`, `evolution/universe.py:92` | **Accepted risk** | `pd.read_pickle` of cache files ARIA itself writes under `data/`. Safe only while `data/` is writable by the owner alone. Prefer parquet. Never load a pickle from a download, a backup restore from elsewhere, or a shared folder. |

## 2. Other areas checked

| Area | Result |
| --- | --- |
| Authentication / authorisation | A global `_role_guard` middleware denies by default. Free users reach only the research allowlist (`src/auth/policy.py`). Execution routes require **proven** ownership (token or signed session): loopback-inferred ownership is refused there (`tests/test_security_execution_auth.py`). Approve and release need step-up. My route scan: 231 routes, 73 mutating. The five without a route-level `Depends` (`/api/auth/login`, `/logout`, `/api/chat/local`, `/api/specialist/analysis`, `/api/quant/greeks`) are still behind the role middleware and are deliberately public or service routes. |
| CSRF | `_mutation_guard` refuses a foreign `Origin` on writes. Same-origin is compared with `Host`, which is sound except under DNS rebinding (S-03). |
| Subprocess | `/api/run` uses an argv list with `shell=False`. Arguments come from a boolean, not user text. |
| Path traversal | Covered by `tests/test_path_traversal.py`. Backup restore is hardened in `edfb5e6` (drive letters, backslashes, `filter="data"`). |
| Secrets in logs | Grepped every logger call for token, secret, api_key or password: none logs a credential. Halt and readiness responses withhold host paths. |
| Deserialisation | JSON everywhere on the order path. The queue loader validates schema and identity, and raises `QueueStateError` rather than returning an empty book. |

## 3. Findings

### S-01 — HIGH (deployment gate) — The runtime venv is not the audited environment: 74 known vulnerabilities in 14 packages

- **Component**: `venv\` in the runtime folder (CPython 3.11.8), the environment that serves ARIA today.
- **Evidence**: `uvx pip-audit==2.9.0 --path venv/Lib/site-packages`: 175 packages, **74 vulnerabilities in 14 packages**. These include the HTTP stack (**starlette 1.0.0** → 1.3.1, **python-multipart 0.0.28** → 0.0.31, **urllib3 2.7.0** → 2.8.0, anyio, aiohttp, multidict), pillow 12.2.0, gitpython 3.1.50, setuptools 65.5.0, pip 24.0, soupsieve, oauthlib, torch 2.12.1, and **chromadb 1.5.9 (no fixed version)**. Codex's "zero findings" result is for a fresh private 3.14.8 environment that is not deployed (ARIA_SECURITY_READINESS says so).
- **Impact**: The backend that would run a paper-acceptance session today exposes a vulnerable web framework and multipart parser on its listening port, and old Expat for feed XML.
- **Fix**: At cutover, recreate the runtime venv from `requirements.txt` + `constraints-security.txt` on the patched interpreter (Codex's procedure), then re-run pip-audit against **that** path. Decide what to do about chromadb: run it in-process only, never as an HTTP server, or replace it.
- **Owner**: Owner (cutover approval), Codex (procedure + audit). **Status**: OPEN (part of the BLOCKED cutover gate).

### S-02 — MEDIUM (candidate) / remediated in `edfb5e6` — Research fetches could reach private addresses through DNS or redirects

See §1. `edfb5e6` adds `src/research/transport.py`: DNS answers must be global before
connecting, every redirect is re-validated, HTTPS→HTTP downgrades are refused, and
credentials are stripped across origins. The DNS-rebinding window between the check and the
connect remains and is documented; it needs an egress rule on the host.
**Owner**: Codex. **Status**: FIXED in `edfb5e6` (accepted, see §4); not in the candidate.

### S-03 — LOW — Inferred loopback ownership ignores the Host header (DNS rebinding)

- **Component**: `src/auth/policy.py:398-445` (`resolve_role_with_basis`), `backend/main.py:736-762` (`_origin_allowed`).
- **Evidence**: Probe `test_f07_loopback_fallback_refuses_a_foreign_host` (F-07). With no owner credential, a loopback socket presenting `Host: attacker.example:8000` resolves to `{'role': 'owner'}`.
- **Impact**: Limited. It applies only when no owner credential is configured, and `/ready` reports exactly that state as NOT READY. Execution routes still demand proof. A rebinding page could read owner-only GETs (vault, portfolio) and make non-execution writes on a developer laptop.
- **Fix**: Accept the loopback fallback only when `Host` is a loopback name (`localhost`, `127.0.0.1`, `[::1]`). Add a Host allowlist (Starlette `TrustedHostMiddleware`) that includes the configured tunnel host.
- **Owner**: Codex. **Status**: OPEN.

### S-04 — LOW — The SENTINEL consult token goes to an unvalidated URL and survives redirects

- **Component**: `src/consult/sentinel_client.py:163` (`SENTINEL_URL` from env, default `http://127.0.0.1:8300`), `:295`.
- **Evidence**: No scheme or host check. `urlopen` follows redirects and copies the custom `X-Sentinel-Consult-Token` header to the new host. A non-loopback `http://` URL (Mac mini peering over the LAN) sends the token in cleartext.
- **Fix**: Require `https` for non-loopback hosts. Use an opener with redirects disabled, or reuse `research/transport.PublicRedirectHandler`'s header stripping.
- **Owner**: Codex. **Status**: OPEN.

### S-05 — LOW — Pickle caches trust `data/`

See B301 above. **Owner**: Codex (move to parquet when touched). **Status**: ACCEPTED RISK; documented prerequisite: `data/` is owner-only.

## 4. Review of Codex `edfb5e6` (security part)

| Change | Verdict | Notes |
| --- | --- | --- |
| `src/core/xml_security.feed_xml` | **ACCEPT** | Refuses the DOCTYPE structurally through `TreeBuilder.doctype`, before the internal subset is processed. Size is checked on the encoded bytes. Codex tests cover utf-8/16/32 entities and oversize input. Bandit still flags its own `ET.fromstring` (B314). That is the hardened site: document it, don't suppress it. `quant_lab` still does an unbounded `resp.read()` before calling it; bound the read to `MAX_XML_BYTES + 1`. |
| `src/research/transport.public_urlopen` | **ACCEPT with note** | A redirect to a non-public URL raises `ValueError` (from `normalize_public_url`), not `URLError`, and `research/http.get` catches only `URLError/OSError/TimeoutError`, so the error escapes as a different type. It fails closed, but wrap it in `URLError` so every source reports it the same way. IPv6 answers with a scope id (`fe80::1%eth0`) also raise `ValueError` from `ip_address`; same remedy. |
| `scripts/backup.py` restore | **ACCEPT** | Drive, backslash and `is_relative_to` checks, plus `tarfile` `filter="data"` (requires ≥ 3.11.4; the runtime is 3.11.8). |
| `scripts/backup.py` make_backup | **REQUEST CHANGE (LOW)** | Any `.db/.sqlite` file whose snapshot fails, for example locked longer than 10 s or not actually SQLite, now aborts the **whole** server backup, including the execution records, and leaves `server-backup-*.tar.partial` behind (prune only globs `*.tar.gz`). Prefer: mark that file failed in the manifest, finish the archive, exit non-zero, and delete the `.partial` on failure. The desk's own nightly zip of `data/desk` + `data/execution` is unaffected. |
| `src/models/ml_store.dataset_age` | **REJECT as is (MEDIUM regression, F-06)** | See CLAUDE_ML_PIPELINE_REVIEW F-06: the new early return yields `age_hours=None` with `basis="row_generated_at"`, and `backend/main.py:2852` (`if age_h < 36`) raises `TypeError` for any future-dated row. |

Full suite on `edfb5e6` (Windows, runtime venv 3.11.8): **3,542 passed, 13 skipped, exit 0**
(+19 tests over the candidate). The regression is not caught because no test drives
`_source_age` with a future-dated ML row.
