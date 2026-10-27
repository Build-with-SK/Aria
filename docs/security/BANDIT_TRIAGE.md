# Bandit finding-by-finding review

Baseline `edc2796`, 2026-10-18. Original locations are stable review anchors, not current line numbers. Scan scope: `src backend main.py`, medium/high. No suppressions added.

| ID | Original location / rule | Classification | Disposition and evidence |
| --- | --- | --- | --- |
| BND-01 | `backend/main.py:2807` B310 (MEDIUM) | Acceptable documented risk | Retained: Ollama base comes from operator environment; fixed /api/tags path, intentional private model service. |
| BND-02 | `backend/main.py:2815` B310 (MEDIUM) | Acceptable documented risk | Retained: Same trusted Ollama base; model inventory read, no caller URL. |
| BND-03 | `src/brain/aggregate.py:245` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal localhost Ollama fallback; private connection is intentional. |
| BND-04 | `src/brain/cognitive/sight.py:137` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator Ollama base; fixed model inventory path. |
| BND-05 | `src/brain/cognitive/sight.py:150` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator Ollama base; model name goes in JSON body, never URL authority. |
| BND-06 | `src/brain/data_generator.py:89` B608 (MEDIUM) | False positive | Retained: Table selected only from literals signals/signal_history; limit bound with placeholder. |
| BND-07 | `src/brain/quant_lab.py:296` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal arXiv search URL. Public upstream trust and unbounded download remain availability limits; parser now bounded. |
| BND-08 | `src/brain/quant_lab.py:300` B314 (MEDIUM) | Confirmed vulnerability | Fixed: Untrusted XML allowed DTD/internal entity expansion; all five sites now use bounded feed_xml with parser-level DTD rejection (UTF-8/16 tests). |
| BND-09 | `src/brain/quant_lab.py:350` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator Ollama base; paper text is JSON request content, not authority. |
| BND-10 | `src/brain/quant_lab.py:379` B301 (MEDIUM) | Requires further validation | Open: Owner-controlled prices cache; pickle executes code if cache is replaced. Filesystem ACL/provenance must be validated on deployment. |
| BND-11 | `src/brain/self_state.py:97` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator Ollama base and literal /api/tags path; intentional private model read. |
| BND-12 | `src/consult/sentinel_client.py:295` B310 (MEDIUM) | Requires further validation | Open: Operator SENTINEL service URL and service token; redirect/token handling and trusted service deployment require validation. |
| BND-13 | `src/consult/sentinel_client.py:352` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator SENTINEL base plus fixed /health path; no arbitrary caller host. |
| BND-14 | `src/core/ingest.py:63` B324 (HIGH) | False positive | Retained: SHA1 creates stable event IDs, not credential signatures; changing IDs would duplicate imported history. Collision resistance for hostile bulk imports remains a limitation. |
| BND-15 | `src/core/launch_check.py:99` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator CLI target for read-only health GET, bounded 200KB read; local targets intentional. |
| BND-16 | `src/core/ledger.py:232` B324 (HIGH) | False positive | Retained: SHA1 stable ledger identities, not authentication; preserve existing prediction IDs. No claim of adversarial collision resistance. |
| BND-17 | `src/data/enrichment.py:55` B310 (MEDIUM) | Requires further validation | Open: Fixed HTTPS NewsAPI host; query and key URL-encoded. API-key redirect/log handling needs provider/egress validation. |
| BND-18 | `src/data/fx_monitor.py:38` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal Yahoo host and fixed currency pair; no caller-controlled authority. |
| BND-19 | `src/data/lse_data.py:53` B324 (HIGH) | False positive | Retained: SHA1 cache filename from endpoint/parameters; not authentication or password hashing. |
| BND-20 | `src/data/lse_data.py:73` B310 (MEDIUM) | Requires further validation | Open: Fixed LSE host, x-api-key header; cross-origin credential forwarding on redirects requires validation. |
| BND-21 | `src/data/technical_feeds.py:86` B310 (MEDIUM) | Acceptable documented risk | Retained: Fixed BSE history endpoint, query encoded, response capped; transport override is code-only fixture hook. |
| BND-22 | `src/data/universe.py:355` B310 (MEDIUM) | Acceptable documented risk | Retained: Constant NSE equities URL; public source availability/size are bounded by timeout only. |
| BND-23 | `src/data/universe.py:396` B310 (MEDIUM) | Acceptable documented risk | Retained: Constant BSE URL template interpolates date only. |
| BND-24 | `src/data/universe.py:462` B310 (MEDIUM) | Acceptable documented risk | Retained: Nested helper used only for NASDAQ Trader constant listing URLs. |
| BND-25 | `src/data/universe.py:553` B608 (MEDIUM) | False positive | Retained: Only generated question-mark placeholders interpolated; exchange values bound. |
| BND-26 | `src/data/universe.py:578` B310 (MEDIUM) | Requires further validation | Open: Literal NSE archive host; date formatting only, no untrusted URL authority. Zip/download resource limits need validation. |
| BND-27 | `src/data/universe.py:774` B608 (MEDIUM) | False positive | Retained: Only generated question-mark placeholders interpolated; rescued symbols bound. |
| BND-28 | `src/data/universe.py:845` B608 (MEDIUM) | False positive | Retained: WHERE built from literal predicates exchange=? and asset_class=?; all caller values and limit bound. |
| BND-29 | `src/data/universe.py:1373` B608 (MEDIUM) | False positive | Retained: Identifier loop restricted to industry/sector literals; values, excluded symbol and limit bound. |
| BND-30 | `src/database/database.py:501` B608 (MEDIUM) | False positive | Retained: Table identifier from six source-code literals; no external identifier. |
| BND-31 | `src/desk/auto_executor.py:448` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal HTTPS ntfy.sh authority; owner topic is path-quoted. Notification vendor receives the intended message. |
| BND-32 | `src/desk/auto_executor.py:457` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal HTTPS Pushover endpoint; credentials in POST body; optional notification service is an operator trust decision. |
| BND-33 | `src/desk/debate.py:46` B310 (MEDIUM) | Acceptable documented risk | Retained: Operator Ollama base, fixed /api/tags; intentional private service access. |
| BND-34 | `src/desk/notify.py:55` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal HTTPS ntfy.sh host; owner topic quoted in path, no arbitrary authority. |
| BND-35 | `src/desk/notify.py:64` B310 (MEDIUM) | Acceptable documented risk | Retained: Literal HTTPS Pushover host; operator config supplies intended token/user in POST body. |
| BND-36 | `src/evolution/universe.py:92` B301 (MEDIUM) | Requires further validation | Open: Cache path chosen from PANELS constants. Pickle accepts arbitrary code if replaced; verify trusted writer and ACL before optional lab enablement. |
| BND-37 | `src/inference/ollama_discovery.py:84` B310 (MEDIUM) | Acceptable documented risk | Retained: Base URL selected by operator/code, fixed /api/tags path; private inference network is intentional. |
| BND-38 | `src/inference/providers/anthropic.py:43` B310 (MEDIUM) | Requires further validation | Open: Literal HTTPS vendor API with x-api-key; redirect credential policy requires validation before cloud enablement. |
| BND-39 | `src/inference/providers/ollama.py:96` B310 (MEDIUM) | Acceptable documented risk | Retained: Provider base is operator configuration; body serializes messages as JSON, fixed /api/chat path. |
| BND-40 | `src/intelligence/research/pit_data.py:485` B324 (HIGH) | False positive | Retained: SHA1 news identity from URL/title, never used for authorization. Do not treat this truncated digest as content authenticity. |
| BND-41 | `src/political/fetchers/senate_efts.py:389` B314 (MEDIUM) | Confirmed vulnerability | Fixed: Untrusted XML allowed DTD/internal entity expansion; all five sites now use bounded feed_xml with parser-level DTD rejection (UTF-8/16 tests). |
| BND-42 | `src/research/http.py:157` B310 (MEDIUM) | Confirmed vulnerability | Mitigated; further validation required: Initial syntax-only URL check did not validate DNS or redirects. public_urlopen checks every DNS answer/hop, strips cross-origin credentials and forbids HTTPS downgrade. DNS rebinding at socket connection still needs network egress enforcement. |
| BND-43 | `src/research/sources/news.py:51` B314 (MEDIUM) | Confirmed vulnerability | Fixed: Untrusted XML allowed DTD/internal entity expansion; all five sites now use bounded feed_xml with parser-level DTD rejection (UTF-8/16 tests). |
| BND-44 | `src/research/sources/reddit.py:73` B310 (MEDIUM) | Requires further validation | Open: Literal HTTPS OAuth endpoint with Basic authorization; reject/validate redirects before provisioning optional credentials. |
| BND-45 | `src/research/sources/reddit.py:111` B314 (MEDIUM) | Confirmed vulnerability | Fixed: Untrusted XML allowed DTD/internal entity expansion; all five sites now use bounded feed_xml with parser-level DTD rejection (UTF-8/16 tests). |
| BND-46 | `src/research/sources/rss.py:86` B310 (MEDIUM) | Confirmed vulnerability | Mitigated; further validation required: Initial syntax-only URL check did not validate DNS or redirects. public_urlopen checks every DNS answer/hop, strips cross-origin credentials and forbids HTTPS downgrade. DNS rebinding at socket connection still needs network egress enforcement. |
| BND-47 | `src/research/sources/rss.py:95` B314 (MEDIUM) | Confirmed vulnerability | Fixed: Untrusted XML allowed DTD/internal entity expansion; all five sites now use bounded feed_xml with parser-level DTD rejection (UTF-8/16 tests). |
| BND-48 | `src/research/sources/web.py:79` B310 (MEDIUM) | Acceptable documented risk | Hardened: Jina authority is a fixed HTTPS literal; the supplied URL is a normalized path suffix, not the request authority. New transport also checks DNS/redirects for this fetch. Jina upstream trust and DNS rebinding egress validation remain deployment limits. |
| BND-49 | `src/v5/vendors.py:79` B310 (MEDIUM) | Requires further validation | Open: Helper accepts URLs built by module vendor functions; caller symbol is path/query data. Validate final egress/provider behavior before deployment. |

## Rerun

After `edfb5e6`: **43 findings**, exit 1; 4 high / 39 medium. Rules: B310 29, B608 6, B324 4, B301 2, B314 2. No scan errors. Five parsing sites now call the shared bounded XML parser; Bandit emits two B314 patterns at its `fromstring`/`XMLParser` call, whose DTD rejection is tested. Three research URL calls now use a transport with explicit DNS/redirect policy; the scanner does not infer that policy. Count reduction alone is not the security acceptance criterion.

Manual assessment: five XML vulnerabilities fixed; two public-fetch SSRF vulnerabilities mitigated, with DNS rebinding still requiring connection-time egress enforcement. Nine further-validation findings remain open. The fixed Jina authority is not a demonstrated caller-controlled local SSRF entry point; its transport was hardened too.
