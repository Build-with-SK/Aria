"""
src/training/
=============
Steps 5 and 6 of docs/ARIA_NEXT_SESSION.md — making the model HERS, and
letting it evolve without letting it get worse.

`dataset.py`   the three corpora, split chronologically and never shuffled
`evaluate.py`  one metric, scored out-of-sample against what is already running
`adapters.py`  versioned adapters, and the gate a new one has to pass
`qlora.py`     the training run itself: config, preflight, cluster job

Written before the data exists, deliberately. Every module here refuses to
produce a number today — loudly, with the count it wanted and the count it
has — and starts working on its own the day the paper loop has accumulated the
sample. `src/v5/weightfit.py` is the same shape and the same reasoning; this
package copies it on purpose.

The one rule that is not negotiable anywhere in here: **outcomes are never
synthesised.** A model fine-tuned on invented results is worse than no
fine-tune, because it is confidently wrong in the one domain that matters.
"""
