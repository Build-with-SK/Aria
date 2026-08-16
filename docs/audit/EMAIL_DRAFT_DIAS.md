# Draft email — Dr Fabio S Dias

**Not sent.** Review, edit in your own voice, and send it yourself.

- **To:** f.dias@surrey.ac.uk
- **Subject:** MANM525 student — a research engine that mostly tells me my signals don't work

---

Dear Dr Dias,

I was a student on MANM525 Financial Modelling between February and May 2025.

Since then I have been building a multi-strategy equity research engine on my
own time — 41 independent modules (momentum, mean reversion, cointegration,
regime, credit, carry and so on), each of which must output a probability with
a confidence interval, cite the data behind every claim, and abstain when it
cannot support one.

The part I would value your opinion on is not the engine, but what happened
when I tried to measure it honestly. I built a purged walk-forward harness with
the data layer pinned to each evaluation date, and ran all 41 modules across 40
US large caps over roughly ten years. Three results surprised me:

1. Correcting for overlapping forward windows and for cross-sectional
   correlation cut my effective sample by roughly a third — one module's
   apparent 85% hit rate turned out to rest on about fourteen independent
   observations.
2. After a Benjamini-Hochberg correction for running 41 simultaneous tests, no
   module shows a significant *positive* edge over the base rate, and several
   are significantly negative.
3. A few modules have significantly negative information coefficients — their
   scores rank forward returns the wrong way round — while others have positive
   ICs but negative hit rates because their bearish calls land near chance in a
   sample that mostly rose.

That third result is why I am writing to you specifically. Reading your work on
signed path dependence and on conditional asymmetry in commodity futures, it
seems to me I may have stumbled into a signed-dependence effect with the sign
inverted, and I do not trust my own interpretation of it on one decade of one
market.

Would you have 20-30 minutes in the coming weeks to look at it? I would find
your view on the methodology far more useful than any result it has produced so
far.

I would also welcome your advice on routes into quantitative or trading roles,
if you have time for that question as well.

The code and the write-up are on GitHub and I am happy to send either in
advance.

With thanks,
Soundariyan Karunakaran

---

## Why this email is shaped the way it is

**It leads with a negative result.** He has a PhD in Econometrics and
Statistical Science and runs an investment management firm. He has almost
certainly seen a large number of student projects claiming edge, and close to
none that arrive saying "my own modules do not beat the base rate, here is the
adjusted p-value." The honesty is the differentiator; a backtest equity curve
would be the opposite of one.

**It engages his actual research, not his job title.** *A Non-parametric Test
and Predictive Model for Signed Path Dependence* (Computational Economics,
2020) and *Using conditional asymmetry to predict commodity futures prices*
(2021) are directly adjacent to what your modules do — conditional forward base
rates are conditional asymmetry, and a significantly negative IC is signed path
dependence with the sign flipped. Mentioning the papers without pretending to
have mastered them is the right register.

**The job question is last and small.** He is CEO of Stalwart Holdings and
Director of the Surrey-Bloomberg relationship, so it is a reasonable thing to
ask — but leading with it would make the technical content read as a pretext.

**Length.** Senior academic and a CEO. This is about 320 words; anything longer
gets skimmed.

## Things to check before you send

- Confirm the MANM525 dates and that you want to identify yourself as a former
  student of that module.
- Decide whether to attach the walk-forward findings document
  (`docs/audit/WALKFORWARD_FINDINGS.md`) or wait until he asks.
- If the repository is private, say so, or make it visible before sending.

## What he is likely to ask, and what your answer is

| Likely question | Where the answer is |
|---|---|
| "How did you handle overlapping windows?" | `_util.conditional_hit_rate` — n_eff = n_raw / horizon |
| "Cross-sectional correlation? Forty names is not forty observations." | `walkforward.effective_sample_size`, measured rho 0.21-0.40 |
| "Multiple testing across 41 modules?" | Benjamini-Hochberg, shared with `src/v5/tiers.py` |
| "What is your out-of-sample protocol?" | Purged walk-forward, `marketdata.as_of` pins the data layer per date |
| "How many live resolved calls?" | Zero. Be blunt — the paper loop has just started, 20 resolved directional calls are needed, and no result was backfilled. |
| "Is this fitted or rule-based?" | Mostly rule-based, so out-of-sample across time rather than trained-here-tested-there. The rules were written by someone who lived through the sample, which no replay undoes. |

The last two are the ones where an honest answer is much stronger than a
confident one.
