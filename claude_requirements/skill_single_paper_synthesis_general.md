# SKILL: Single-paper read — finance / quant (general protocol)

## Purpose
Extract signal, evidence, and trading implications from a single academic finance or quant finance paper using a structured four-pass protocol. The output is a structural skim or a deep synthesis note.


## Model routing
- Pass 1 and Pass 2: **claude-haiku-4-5** — extraction tasks, low reasoning demand
- Pass 3 and Pass 4 + synthesis note: **claude-sonnet-4-6** — empirical interpretation and structured output generation
- Only escalate to Opus if the synthesis requires resolving genuine ambiguity across conflicting evidence within the paper itself

---

## Protocol

### Before starting
Confirm you have access to the paper text, from the pdf of the paper.

Identify the problem statement in the topic. This does not change what you extract — you extract everything faithfully — but it informs the annotation field of the synthesis note.

---

### Pass 1 — Orientation
**Goal:** Establish relevance and frame the paper before reading deeply.

Read in this order:
1. **Abstract** in full
2. **Introduction** — do not read linearly. Scan specifically for:
   - Contribution sentences, typically signaled by: *"we show that..."*, *"we find that..."*, *"our main finding is..."*, *"we contribute to..."*, *"we are the first to..."*
   - Positioning against prior literature — what the paper claims to add or correct
   - The roadmap, usually in the last paragraph of the introduction
   - Skip the motivation story and narrative setup
3. **Conclusion** in full

**Extract:**
- Main claim in 2-3 sentences
- Data source and sample period
- Strategy type (cross-sectional, time-series, event study, etc.)
- Headline statistic if stated in abstract or conclusion

---

### Pass 2 — Structural skim
**Goal:** Understand the mechanism and the data before reading results.

Read:
1. **Theory or hypothesis section** — identify whether the signal is motivated by risk compensation, behavioral bias, market microstructure, or an institutional constraint. This determines how durable the signal is likely to be.
2. **Data and methodology section** — note: universe (market cap filters, exchange filters), rebalancing frequency, holding period, portfolio construction method (sorts, regressions, long-short), and whether the paper is in-sample or includes out-of-sample tests.
3. **Identify key tables and figures before reading them** — locate the main return table, the factor exposure or regression table, and any robustness table. Do not read them yet.

**Extract:**
- Signal mechanism (risk-based / behavioral / structural) and strategy formation
- Data source (CRSP, Compustat, Bloomberg, proprietary, etc.)
- Sample: start year, end year, frequency, asset class, geographic scope
- Universe: approximate number of securities, filters applied
- Portfolio construction: sort method, weighting, rebalancing
- Identified key tables: [list by table number or label]
- Key performance metrics or evaluation metricss, such as Sharpe ratio, information ratio, t-stat, mean return, correlation, accuracy/precision, AUC-ROC, etc.

**Produce:** 
- The structural skim as outlined above
- a yes or no recommendation of whether to perform deep

---

### Pass 3 — Empirical deep dive
**Goal:** Present the empirical evidence faithfully and completely. Do not filter or editorialize — the user will judge the results.

Read and extract from the key tables identified in Pass 2.

**Main result table:**
- Report all alpha, return statistics and evaluation metrics as stated above (annualized where noted)
- Record these metrics if reported
    - max drawdown
    - turnover
    - pre- and post-transaction cost performance: separate if both provided
    - win rate/hit rate
    - avearge return from hits/misses
    - return concentration
- Long and short leg breakdown if provided

**Robustness checks:**
- List all robustness tests the authors run: subperiods, alternative factor definitions, alternative samples, international tests
- Note which results hold and which weaken — report both
- Note the number of specifications tested if the paper reports this

**Authors' own caveats and limitations:**
- Extract directly from the paper — any limitations the authors acknowledge about capacity, liquidity, data availability, or generalizability
- Note any open questions the authors flag for future research

---

### Pass 4 — Extraction
**Goal:** Convert the evidence into an actionable signal specification and flag what needs further work.

**Signal specification (if extractable):**
- Singal definition and strategy formation: exact variable construction as described by the authors
- Signal direction: long / short / long-only
- All metrics in 'Main Result table' in Pass 3, whenever they are available.
- Universe
- Estimated capacity (if discussed)


**Implementation notes:**
- Data requirements to replicate
- Known data vendors that carry the required inputs
- Any implementation challenges noted in the paper

**Open questions for follow-up:**
- What would you need to verify before trading this signal?
- What the paper leaves unanswered that matters for implementation

---

### Synthesis note — structured output

Produce this at the end of the four passes. Format as a structured block, not prose.

```
SYNTHESIS NOTE
==============
Paper:        [Full citation: Authors (Year). Title. Journal/Working Paper.]
Read date:    [Today's date]
Relevance:    [High / Medium / Low — relative to user's stated topic]

CLAIM
-----
[2-3 sentences: what the paper argues and what it finds]

CONTRIBUTION VS. PRIOR WORK
-----------------------------
[What this paper adds or corrects relative to existing literature, as stated by authors]

SIGNAL SPECIFICATION
---------------------
Singal:       [Exact definition]
Direction:    [Long / short / long-short]
Frequency:    [Daily / weekly / monthly / quarterly]
Universe:     [Description]
Sample:       [Start year – End year, data source]

KEY STATISTICS
--------------
[Report verbatim from paper — do not round or restate. When a metric is not found, say *not found*]
- Annualized Returns
- Sharpe Ratio or Information Ratio
- Max Drawdown
- Turnover
- Win Rate/Hit Rate
- Average Returns for Hits/Misses
- Accuracy/Precision/Recall for Prediction
- Return Concentration

ROBUSTNESS
----------
[Summary of what holds and what weakens across robustness tests]

LIMITATIONS AND CAVEATS
------------------------
[Authors' own stated limitations + implementation constraints]

```

---

## Output format notes
- Key statistics must be copied verbatim from the paper — do not paraphrase or recompute
- Do not editorialize about whether results are good or bad — present faithfully
- If a field cannot be filled because the paper does not report it, write: *not found*
