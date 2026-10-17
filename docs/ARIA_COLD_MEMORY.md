# Cold memory listing — Phase 2

PASS for the reproduced listing timeout. Before/after fresh Python 3.11.8
processes, same Windows host and installed optional-heavy venv, temporary empty
Chroma store, outbound network refused, no brain daemon running. Measurements
include tracemalloc and profiling overhead and are not production SLOs.

| Measurement | Before | After |
| --- | ---: | ---: |
| Backend import/setup | 4.0787 s | 4.1966 s |
| First GET /api/brain/memories | 76.2556 s | 4.2950 s |
| Second GET | 1.5195 s | 0.0403 s |
| Python tracked peak allocations | 248.20 MiB | 42.30 MiB |
| Embedding stack imported by listing | yes | no |
| Listing response error | closed HTTP client error | none |

Twenty-second thread stack samples locate the cold request in
sentence_transformers/transformers import scanning, called unconditionally by
LongTermMemory.__init__. Listing documents/statistics never uses embeddings.
The profile's cumulative import list covers the main thread; request worker
stacks and end-to-end request timers establish the request-path diagnosis.
Tracked Python allocations exclude native model allocations/RSS. Empty storage
does not characterize very large collection scans. The running scheduled
training task was left alone; this is one measured before/after pair, not a
controlled load benchmark.

LongTermMemory now creates the embedded collection without a default embedding
function and loads sentence-transformers only when remember/semantic recall/
outcome re-embedding actually needs it. A process-wide lock shares the initial
model across concurrent calls; failed initialization can retry. Listing,
ticker lookup and stats remain usable without the model. No request timeout was
increased. The GET sweep now refuses a memory-listing timeout instead of treating
it as an allowed slow route. First semantic recall still needs the provisioned
model and can be expensive; that is a separate, explicit semantic operation.

Reproduce with a fresh interpreter each time:

```powershell
$env:ARIA_MEMORY_PROFILE='<private-evidence>/memory.json'
venv\Scripts\python.exe -m pytest tests/test_cold_memory_profile.py -q -s
```

The profiling test redirects storage through the suite's isolation fixture,
refuses outbound connections and samples waiting threads. It reports errors
rather than interpreting an empty degraded result as a successful semantic
query. Functional tests verify model-free listing, eight concurrent semantic
requests loading one model, actual retrieval/storage calls and retry after a
failed load. No model/provider credential or production store was used.

Overlap for Claude: long_term_memory.py and the GET sweep's stricter allowance.
No backend route contract or endpoint was removed or renamed.
