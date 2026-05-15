# SKILL: Single-article read — practitioner blog / newsletter (finance / quant)

## Purpose
Extract the claim, mechanism, evidence quality, and trading implications from a single blog post,
newsletter, or non-academic article. The output is a compact synthesis note suitable for cross-paper
comparison and topic-level research synthesis.

## When to use this skill
- Source is a Substack post, personal blog, newsletter, or aggregated finance article
- Source lacks a formal abstract, methodology section, or peer review
- Source may contain practitioner insights, anecdotes, or market commentary

## Model routing
- All passes: **claude-haiku-4-5** — extraction tasks, low reasoning demand
- Only escalate to Sonnet if the article contains unusually complex empirical tables

---

## Protocol

### Before starting
Note the article's evidence tier:
- **peer_reviewed** — published in a journal or conference
- **working_paper** — arXiv, SSRN, or similar pre-print
- **practitioner_blog** — blog, newsletter, personal site with named author
- **aggregator_pointer** — aggregator link (Quantocracy, etc.)

Identify the research topic's open questions before reading. The synthesis note should explicitly flag
whether the article addresses, extends, or contradicts any of those questions.

---

### Pass 1 — Thesis (~2 min)
Read the introduction and conclusion (or first and last paragraphs).

**Extract:**
- Main claim in one sentence — what is the author asserting?
- Author stance and credibility signals:
  - Practitioner with named desk or firm experience
  - Commentator / analyst (no direct trading experience cited)
  - Researcher or academic writing informally
  - Unknown / anonymous

**Decision gate:** If the article is clearly off-topic (no overlap with research topic keywords or
problem statements), stop here and recommend "skip."

---

### Pass 2 — Mechanism
Read the body sections that explain *why* the claim is true.

**Extract:**
- Causal story: what mechanism does the author assert connects cause to effect?
- Is the mechanism risk-based, behavioral, structural, or informational?
- Does the mechanism match, contradict, or extend what the academic literature says about this topic?

---

### Pass 3 — Evidence
Read all sections that support the claim.

**Classify the evidence type** (pick the dominant type):
- `anecdote` — personal story, named trade, or single incident
- `market_data_illustration` — chart, table, or data series cited but not rigorously tested
- `cited_paper` — the author cites or summarizes a formal academic or practitioner paper
- `personal_experience` — unnamed experience, "in my trading," "I have observed"
- `assertion_only` — claim stated without supporting evidence
- `mixed` — multiple evidence types present

**Extract concrete numbers** (if any): returns, Sharpe ratios, holding periods, sample sizes —
copy verbatim, mark as the author's own claims (not verified).

**Note cross-references:** any papers, books, other articles, or datasets the author links to or names.
These are high-value leads for further scouting.

---

### Pass 4 — Actionability
Read the conclusion or any explicit recommendation sections.

**Extract:**
- What would a reader do differently after reading? Be specific.
- Is the actionability concrete (specific factor, signal, position sizing adjustment) or vague
  (general awareness, "think about this")?

---

### Synthesis note — structured output

Produce this at the end. The `record_article_skim` tool captures it.

Fields:
- **thesis**: one sentence
- **mechanism**: causal story the author asserts
- **evidence_type**: one of the six types above
- **concrete_numbers**: verbatim statistics if present, empty string if none
- **author_stance**: credibility signals (one sentence)
- **cross_references**: list of any papers, articles, or datasets cited or linked
- **actionability**: what a reader does differently (one or two sentences)
- **recommendation**: `read` (worth reading in full) | `skip` (off-topic or low signal) |
  `save_for_reference` (relevant background, not urgent)

---

## Output notes
- Do not editorialize about the quality of the evidence — report faithfully
- If a field cannot be determined, use an empty string (not "unknown" or "N/A")
- `cross_references` should be URLs or title strings, not paraphrases
- The recommendation reflects topic relevance AND evidence quality together:
  - Practitioner blog with concrete numbers and a clear mechanism = `read`
  - General commentary with no data = `save_for_reference` or `skip`
  - Off-topic entirely = `skip`
