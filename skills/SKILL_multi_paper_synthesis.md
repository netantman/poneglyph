# SKILL: Multi-paper synthesis — finance / quant (general protocol)

## Purpose
Synthesize a corpus of academic finance or quant finance papers into a structured literature review, with a focus on trading implications. This skill runs on top of individual single-paper reads — it is not a replacement for them. The output is a multi-paper synthesis note that maps consensus, surfaces genuine disagreements, audits data independence, and weights evidence quality.

## Prerequisite
Complete a single-paper read (SKILL_single_paper_read.md) for each paper in the corpus before running this skill. The multi-paper synthesis takes the individual synthesis notes as its inputs.

## When to use this skill
- User wants a literature review on a signal family, factor, or quant topic
- User has read (or asks Claude to read) multiple papers and wants to know what the body of evidence says
- User wants to understand where the literature agrees, where it conflicts, and how strong the combined evidence is
- User is deciding whether to pursue replication of a signal and wants to know the full evidence base

## Model routing
- If corpus is ≤5 papers and synthesis notes are already complete: **claude-sonnet-4-6**
- If corpus is 6–15 papers or synthesis requires substantial cross-paper reasoning: **claude-sonnet-4-6** (still sufficient if framework is well-specified)
- Escalate to **claude-opus-4-6** only when: (a) papers genuinely contradict each other and the reason for divergence is ambiguous, or (b) the user asks for a judgment call on evidence quality that requires holding many competing claims simultaneously

---

## Protocol

### Step 0 — Corpus assembly (before any reading)
Before reading any individual paper, define and record the corpus parameters:

**Scope definition (get from user or infer from topic):**
- Signal family or topic (e.g., momentum, quality, short-term reversal, earnings surprises)
- Asset class scope (equities, fixed income, commodities, FX, multi-asset)
- Geographic scope (US, international, global)
- Time period of interest
- Minimum venue quality (e.g., top-5 journals only, all peer-reviewed, include working papers)

**Corpus registry — record for each paper:**
| # | Authors | Year | Venue | Sample start | Sample end | Data source | Universe | Key factor |
|---|---------|------|-------|-------------|-----------|-------------|----------|------------|
| 1 | | | | | | | | |

This registry is used in Layer 2 (data overlap audit) and must be completed before synthesis begins.

---

### Layer 1 — Consensus mapping
**Goal:** Identify what the body of literature agrees on, and where it diverges. Divergences are more informative than agreements — trace their causes.

**Run across all synthesis notes:**

**Points of agreement — extract:**
- Signal direction: do all papers find the same sign on the factor?
- Asset class: does the signal work across asset classes consistently?
- Frequency: is there agreement on the optimal rebalancing horizon?
- Mechanism: do papers agree on *why* the signal works (risk / behavioral / structural)?

**Points of divergence — extract and diagnose:**
For each divergence, record:
- What differs (magnitude, persistence, significance, sign)
- Candidate explanations:
  - Different sample periods (pre/post 2000, pre/post financial crisis, etc.)
  - Different universes (large cap vs. all-cap, US vs. international)
  - Different factor definitions (subtle construction differences matter)
  - Different portfolio construction (value-weighted vs. equal-weighted, sorts vs. regressions)
  - Publication date relative to factor discovery (post-publication decay)

**Output for this layer:**
```
CONSENSUS MAP
=============
Topic: [Signal family]
Papers in corpus: [N]

Agreed:
- [Point 1]
- [Point 2]

Divergent — and likely reason:
- [Divergence 1]: [Candidate explanation]
- [Divergence 2]: [Candidate explanation]
```

---

### Layer 2 — Data overlap and independence audit
**Goal:** Assess whether the N papers in the corpus represent N independent data points, or whether they are mining the same underlying returns. Shared data creates correlated evidence, not independent confirmation.

**Using the corpus registry from Step 0:**

**Sample period overlap:**
- Identify the common sample window shared by most papers
- Flag papers that are entirely within another paper's sample (not independent)
- Count papers with out-of-sample evidence (post-publication period, international markets, different asset classes)

**Data source overlap:**
- Papers all using CRSP/Compustat for US equities in overlapping periods are drawing from the same pool
- Note if any papers use truly independent data (proprietary, non-CRSP, non-US)

**Factor zoo check:**
- Is this factor already documented under another name in the literature?
- Has the factor been included in a factor zoo study (e.g., Harvey, Liu, Zhu 2016; Hou, Xue, Zhang 2020)?
- What is the estimated multiple-testing-adjusted t-statistic threshold needed for this factor to be credible?

**Out-of-sample evidence assessment:**
- Rate the corpus on out-of-sample coverage:
  - Strong: multiple papers with post-publication samples or international replication
  - Moderate: some out-of-sample but limited
  - Weak: all papers use overlapping in-sample data

**Output for this layer:**
```
DATA INDEPENDENCE AUDIT
=======================
Common sample window: [years shared by majority of papers]
Truly independent papers: [N out of total]
Out-of-sample coverage: [Strong / Moderate / Weak]
Factor zoo status: [Known / Variant of known / Novel]
Notes: [Any important observations about data overlap]
```

---

### Layer 3 — Evidence weighting
**Goal:** Assign qualitative evidence tiers to the overall body of literature. Do not produce numerical scores — use three qualitative tiers.

**Weighting criteria (consider all, not mechanically):**
- Journal quality: top-5 finance journals carry more weight than working papers
- Out-of-sample validation: does the signal survive in periods and markets not used in discovery?
- International replication: does it work outside the US?
- Post-publication performance: does the signal persist after the paper is published and the factor becomes known?
- Methodological rigor: does the paper use appropriate multiple-testing corrections?
- Factor model spanning: does the alpha survive after controlling for known factors?
- Replication by independent teams: has another group replicated the result independently?

**Evidence tiers:**
- **Strong evidence**: multiple independent papers, out-of-sample validation, international replication, post-publication persistence, published in top venues
- **Moderate evidence**: some independent confirmation, limited out-of-sample, predominantly US, some post-publication evidence
- **Weak evidence**: single paper or heavily overlapping samples, no out-of-sample, no international evidence, or evidence of post-publication decay

**Output for this layer:**
```
EVIDENCE WEIGHT
===============
Overall tier: [Strong / Moderate / Weak]
Rationale: [2-3 sentences explaining the tier assignment]
Strongest paper(s): [Which papers carry most evidential weight, and why]
Weakest link: [What would most change your view of this evidence]
```

---

### Multi-paper synthesis note — structured output

Produce this after completing all three layers.

```
MULTI-PAPER SYNTHESIS NOTE
===========================
Topic:          [Signal family or research question]
Prepared:       [Today's date]
Corpus:         [N papers] — see corpus registry below

CONSENSUS VIEW
--------------
What the literature agrees on:
[Bullet points — direction, asset class, frequency, mechanism]

What remains contested:
[Bullet points — magnitude disputes, mechanism disputes, conditional effects]

DATA INDEPENDENCE
-----------------
[Summary from Layer 2: common window, independent papers, OOS coverage, factor zoo status]

EVIDENCE QUALITY
----------------
Overall tier:   [Strong / Moderate / Weak]
[2-3 sentence rationale]

TRADING VIEW (signal spec across corpus)
-----------------------------------------
Best-supported specification:
- Factor definition: [most commonly used, or most robust]
- Direction:         [long-short / long-only / conditional]
- Frequency:         [consensus rebalancing period]
- Universe:          [where evidence is strongest]
- Expected SR range: [from / to — across papers, not a point estimate]
- Post-cost viability: [what the literature says collectively]

OPEN DISPUTES WORTH MONITORING
-------------------------------
[What future research could resolve — specific questions, not vague calls for more work]

ANNOTATION (your field)
-----------------------
Priority for replication:  [High / Medium / Low]
Starting paper:            [Which paper to replicate first, and why]
Data required:             [Minimum data needed to begin]
Your open questions:       [Anything the literature doesn't answer that matters to you]

CORPUS REGISTRY
---------------
[Paste completed table from Step 0]
```

---

## Output format notes
- The synthesis note is always produced in full — partial syntheses should mark incomplete sections
- Do not collapse genuine disagreements into a single "mixed evidence" statement — identify the source of the disagreement
- The trading view section aggregates across the corpus; always note the range of results, not just the best-case study
- Distinguish clearly between what the literature says (evidence) and the annotation (your judgment)
- If the corpus has fewer than 3 papers, note that the multi-paper synthesis is limited and the consensus view should be treated with caution
