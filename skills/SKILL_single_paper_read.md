# SKILL: Single-paper read — finance / quant (general protocol)

## Purpose
Extract signal, evidence, and trading implications from a single academic finance or quant finance paper using a structured four-pass protocol. The output is a synthesis note — a compact, structured record suitable for building a literature archive or informing replication decisions.

## When to use this skill
- User provides a paper (PDF, link, or pasted text) and asks for a read, summary, synthesis, or trading implications
- User asks what a paper finds, whether its results are robust, or how to interpret its tables
- User wants to build a note from a paper for archiving or further analysis

## Model routing
- Pass 1 and Pass 2: **claude-haiku-4-5** — extraction tasks, low reasoning demand
- Pass 3 and Pass 4 + synthesis note: **claude-sonnet-4-6** — empirical interpretation and structured output generation
- Only escalate to Opus if the synthesis requires resolving genuine ambiguity across conflicting evidence within the paper itself

---

## Protocol

### Before starting
Confirm you have access to the paper text or a sufficient excerpt. If only an abstract is available, note this limitation in the synthesis note — Pass 3 cannot be completed without the results section.

Identify the user's problem or topic focus if stated. This does not change what you extract — you extract everything faithfully — but it informs the annotation field of the synthesis note.

---

### Pass 1 — Orientation (~5 min)
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
- Main claim in one sentence
- Asset class and geographic scope
- Data source and sample period
- Strategy type (cross-sectional, time-series, event study, etc.)
- Headline statistic if stated in abstract or conclusion

**Decision gate:** If the paper is not relevant to the user's problem, stop here and note why. Do not proceed to Pass 2.

---

### Pass 2 — Structural skim (~10–15 min)
**Goal:** Understand the mechanism and the data before reading results.

Read:
1. **Theory or hypothesis section** — identify whether the signal is motivated by risk compensation, behavioral bias, market microstructure, or an institutional constraint. This determines how durable the signal is likely to be.
2. **Data and methodology section** — note: universe (market cap filters, exchange filters), rebalancing frequency, holding period, portfolio construction method (sorts, regressions, long-short), and whether the paper is in-sample or includes out-of-sample tests.
3. **Identify key tables and figures before reading them** — locate the main return table, the factor exposure or regression table, and any robustness table. Do not read them yet.

**Extract:**
- Signal mechanism (risk-based / behavioral / structural)
- Data source (CRSP, Compustat, Bloomberg, proprietary, etc.)
- Sample: start year, end year, frequency
- Universe: approximate number of securities, filters applied
- Portfolio construction: sort method, weighting, rebalancing
- Identified key tables: [list by table number or label]

---

### Pass 3 — Empirical deep dive (~20–30 min)
**Goal:** Present the empirical evidence faithfully and completely. Do not filter or editorialize — the user will judge the results.

Read and extract from the key tables identified in Pass 2.

**Main result table:**
- Report all alpha and return statistics as stated (annualized where noted)
- Sharpe ratio (annualized)
- t-statistic on alpha or mean return
- Maximum drawdown if reported
- Turnover if reported
- Pre-cost and post-cost figures separately if both provided
- Long and short leg breakdown if provided

**Robustness checks:**
- List all robustness tests the authors run: subperiods, alternative factor definitions, alternative samples, international tests
- Note which results hold and which weaken — report both
- Note the number of specifications tested if the paper reports this

**Authors' own caveats and limitations:**
- Extract directly from the paper — any limitations the authors acknowledge about capacity, liquidity, data availability, or generalizability
- Note any open questions the authors flag for future research

---

### Pass 4 — Extraction (~10 min)
**Goal:** Convert the evidence into an actionable signal specification and flag what needs further work.

**Signal specification (if extractable):**
- Factor definition: exact variable construction as described by the authors
- Signal direction: long / short / long-only
- Rebalancing frequency
- Universe
- Estimated capacity (if discussed)
- Turnover estimate
- Cost sensitivity: does the alpha survive after realistic transaction costs?

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
[One sentence: what the paper argues and what it finds]

CONTRIBUTION VS. PRIOR WORK
-----------------------------
[What this paper adds or corrects relative to existing literature, as stated by authors]

SIGNAL SPECIFICATION
---------------------
Factor:       [Exact definition]
Direction:    [Long / short / long-short]
Frequency:    [Daily / weekly / monthly / quarterly]
Universe:     [Description]
Sample:       [Start year – End year, data source]

KEY STATISTICS
--------------
[Report verbatim from paper — do not round or restate]
- Annualized return:
- Sharpe ratio:
- Alpha (model):       [specify model, e.g. CAPM, FF3, FF5]
- t-statistic:
- Max drawdown:
- Turnover:
- Post-cost alpha:

ROBUSTNESS
----------
[Summary of what holds and what weakens across robustness tests]

LIMITATIONS AND CAVEATS
------------------------
[Authors' own stated limitations + implementation constraints]

ANNOTATION (your field)
-----------------------
Replicate?    [Y / N / Maybe — and why]
Open questions: [What you want to verify or investigate further]
Related papers: [Any cited work worth following up]
```

---

## Output format notes
- Always produce the synthesis note, even for a partial read (mark incomplete sections clearly)
- Key statistics must be copied verbatim from the paper — do not paraphrase or recompute
- Do not editorialize about whether results are good or bad — present faithfully
- If a field cannot be filled because the paper does not report it, write: *not reported*
- The annotation section is the only place for the user's own judgment — keep it clearly separated from the paper's content
