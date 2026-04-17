# SKILL: Limit Order Book — Prediction & Trading Implementation

## Topic context

**Problem statements:**
1. How to use limit order book (LOB) data to predict short-horizon price movements
2. Implementation details about how the signal/model can translate to actual PnL in trading

**Keywords:** limit order book, LOB, order flow, market microstructure, price prediction, order book imbalance, queue position, high-frequency trading, bid-ask spread, tick data, level 2 data, depth of book, trade execution, market making, latency, order flow toxicity

**Priority keywords:** order book imbalance, LOB prediction, microstructure alpha, queue priority, price impact model

---

## Section A — Structural Skim (Haiku, Phase 2)

### Purpose
Extract from each paper the prediction target, the LOB features used, the modelling approach, and the key performance numbers — fast enough to decide read / skip / deep_dive across dozens of scouted papers.

### Paper anatomy for LOB research
LOB papers typically follow one of two templates. Identify which one early because it determines where the useful content lives:

**Template 1 — Empirical / ML prediction papers** (most common):
- Abstract states the prediction target (mid-price direction, return sign, spread crossing) and headline accuracy or Sharpe.
- Introduction frames the contribution against OFI, DeepLOB, or the Cont/Stoikov/Talreja line. Look for "we improve upon..." or "unlike prior work, we..."
- Data section is critical: which exchange, which assets, what snapshot frequency (event-by-event vs fixed interval), how many levels of depth, and the sample period. Papers using 5-level snapshots vs full 10-level event-by-event data are solving different problems.
- Feature engineering or model architecture section is the core — this is where the paper either uses raw LOB states, hand-crafted features (imbalance ratios, volume-weighted prices, order flow imbalance), or learned representations (CNNs on LOB images, LSTMs on sequences, transformers on event streams).
- Results section: look for the main classification or regression table first, then any ablation or feature importance analysis.
- Backtest or PnL section (often missing): if present, this is the most valuable part. Note whether they simulate trading with realistic latency, fees, and queue priority assumptions.

**Template 2 — Theoretical / market microstructure papers:**
- Abstract states the model setup (continuous-time, discrete, agent-based) and the main result (equilibrium characterisation, optimal strategy, asymptotic behaviour).
- Model section dominates the paper. Look for the state space definition, the information structure (symmetric vs asymmetric), and what agents optimise.
- Propositions/theorems are the main output — extract the key result in plain language.
- Numerical illustrations or calibration section: note what data they calibrate to and whether parameters are realistic.
- These papers rarely have backtest results but may have simulation-based PnL comparisons.

### Pass 1 — Orientation
Read in this order:
1. **Abstract** in full.
2. **Introduction** — scan for:
   - The prediction target or decision problem (e.g., "predict mid-price movement at horizon τ", "optimal execution given LOB state")
   - The LOB representation used (raw snapshots, features, event stream)
   - Contribution sentences: "we show...", "we find...", "our main contribution..."
   - Positioning: which prior LOB papers are they building on or challenging? (DeepLOB, OFI, Cont et al.?)
3. **Conclusion** in full.

**Extract:**
- `main_claim`: one sentence — what the paper argues and finds
- `data_source`: exchange(s), asset(s), and data vendor/source
- `strategy_type`: classify as one of: LOB prediction (classification), LOB prediction (regression), market making, optimal execution, price impact modelling, order flow analysis, theoretical/equilibrium, survey/review
- `headline_statistic`: the single most important number from abstract or conclusion (accuracy, F1, Sharpe, alpha, etc.)

**Decision gate:** Does this paper address LOB-based prediction or the translation of LOB signals into trading PnL? If it is purely about optimal execution without prediction, or about market design/regulation with no signal content, set `skim_recommendation` to `skip` with a one-line reason, and stop.

### Pass 2 — Structural skim
Read:
1. **Data section** — extract:
   - Exchange and assets (e.g., LOBSTER/NASDAQ ITCH for AAPL, MSFT; Binance BTC-USDT; LSE FTSE100 constituents)
   - LOB depth: how many levels (1, 5, 10, full book)
   - Snapshot frequency: event-by-event, 10ms, 100ms, 1s, or other
   - Sample period: start date, end date, total trading days
   - Train/validation/test split method (rolling, anchored, random — random is a red flag for time-series)
   - Any filtering applied (e.g., "exclude first/last 30 min", "remove auction periods")

2. **Feature engineering / model architecture** — extract:
   - LOB features: list all features or feature groups (e.g., order imbalance at levels 1-5, volume-weighted mid-price, trade flow indicators, raw price-volume vectors)
   - Model type: linear, logistic, random forest, CNN, LSTM, transformer, GNN, reinforcement learning, analytic (closed-form), other
   - If deep learning: architecture specifics (number of layers, input shape, attention mechanism)
   - Prediction target: exact definition (e.g., "sign of mid-price change over next 10 events", "5-second return exceeding 1 tick")
   - Prediction horizons tested (list all: e.g., 1, 2, 3, 5, 10, 20, 50, 100 events ahead)

3. **Key tables and figures** — identify (by number/label) but do not read yet:
   - Main prediction accuracy or return table
   - Feature importance or ablation table
   - PnL or backtest table (flag if absent)
   - Any comparison table against benchmark models

**Extract:**
- `signal_mechanism`: what LOB information drives the prediction (e.g., "order flow imbalance at best quotes", "deep-book volume asymmetry", "trade arrival rate changes")
- `data_details`: exchange, assets, depth, frequency, sample period — compact string
- `sample`: start year – end year, number of trading days or events
- `universe`: which instruments, any filters
- `portfolio_construction`: how the signal is turned into positions (if discussed) — e.g., "long/short based on predicted direction", "market making with skewed quotes", "not discussed (prediction only)"
- `key_tables`: list of table/figure numbers identified as important
- `key_metrics`: compact summary of main performance numbers from key tables (do not deep-read — extract what is visible from table headers and first rows)

### Recommendation logic
Assign `skim_recommendation` based on:
- **deep_dive**: paper presents a novel LOB feature, architecture, or trading strategy with reported out-of-sample results on real exchange data; or presents a practical PnL simulation with realistic assumptions (latency, fees, queue position); or introduces a dataset or benchmark that could be directly useful
- **read**: paper has relevant LOB prediction content but either lacks out-of-sample testing, uses synthetic data only, or covers well-trodden ground (e.g., another DeepLOB variant) without clear improvement; or is a useful survey/review
- **skip**: paper is tangentially related (mentions LOB but focuses on regulation, market design, or pure theory without testable predictions); or uses data/methods too far from the problem statements (e.g., daily-frequency analysis of order book shape)

---

## Section B — Deep Synthesis (Sonnet/Opus, Phase 4)

### Purpose
Produce a thorough analysis of the paper's LOB methodology, evidence quality, and implementation feasibility — the user has decided this paper is worth deep investment after reviewing the structural skim.

### Instructions

You have access to the full paper text (PDF-extracted). Read the entire paper, then produce the synthesis below. If only the abstract is available, complete what you can and flag each section that requires full text with "[needs PDF]".

### Structure of the deep synthesis

#### 1. Core contribution (2–3 sentences)
What this paper adds to the LOB prediction or trading literature that did not exist before. Be specific — "improves accuracy" is not a contribution; "shows that volume imbalance at levels 3–5 carries incremental predictive power beyond best-quote imbalance, with a 3pp accuracy gain on NASDAQ ITCH data" is.

#### 2. LOB representation and features
- Exact LOB representation: what data goes into the model (price levels, volumes, timestamps, trade indicators). Draw a diagram in words if helpful — e.g., "Input is a 10×4 matrix: 10 levels × (bid price, bid size, ask price, ask size), updated per event."
- Feature engineering pipeline: any transformations applied before modelling (normalisation, differencing, log-transform, rolling statistics). Note whether features are stationary.
- Feature novelty: are these features new, or are they standard OFI / imbalance features with a new model on top?

#### 3. Model architecture and training
- Full architecture description. For deep learning: layer types, dimensions, activation functions, loss function, optimiser, learning rate schedule, regularisation.
- Training regime: batch size, epochs, early stopping criteria, data augmentation (if any).
- Label construction: exact definition of the prediction target, including any smoothing or bucketing (e.g., "three-class: up if return > +0.5 tick, down if < −0.5 tick, stationary otherwise").
- Class balance: how are classes distributed? Any oversampling, undersampling, or class-weighted loss?
- Train/test methodology: rolling window, expanding window, or static split? Lookahead contamination risk?

#### 4. Empirical results — faithful extraction
Report all main results exactly as stated. For each prediction horizon or strategy variant:
- Accuracy, precision, recall, F1 (for classification tasks)
- RMSE, MAE, R² (for regression tasks)
- Sharpe ratio, cumulative return, max drawdown, average PnL per trade (for trading strategies)
- Statistical significance: t-stats, p-values, confidence intervals — whatever is reported
- Benchmark comparison: what baselines are used? Report their numbers too.

#### 5. Robustness and out-of-sample
- List every robustness check: different assets, different time periods, different horizons, different architectures, feature ablations, alternative label definitions.
- Which results hold out-of-sample and which degrade? Quantify the degradation.
- Cross-asset generalisation: does a model trained on one stock work on another?
- Temporal stability: do results degrade in more recent data?

#### 6. Implementation reality check
This section is critical for Problem Statement 2 (signal → PnL translation). Assess:
- **Latency assumptions**: does the paper assume instantaneous execution? What is the realistic latency for the exchange and data feed used? Would the signal survive 1ms, 10ms, 100ms, 1s of latency?
- **Transaction costs**: are costs modelled? If so: spread cost, exchange fees, market impact. Are the assumed costs realistic for the asset and size?
- **Queue position**: for market-making or limit-order strategies, does the paper model queue priority? Adverse selection risk?
- **Capacity**: how much capital could this strategy support before market impact erodes returns? Is this discussed?
- **Data requirements for live trading**: what data feed, what infrastructure, what compute? Could this run on co-located hardware, or does it need specialised GPU inference?
- **Regime sensitivity**: does the strategy work across different volatility regimes, different spread environments, different market conditions?

If the paper does not discuss implementation at all, state this explicitly and assess from the methodology whether the signal could plausibly survive realistic trading frictions.

#### 7. Connections and gaps
- How does this paper relate to the LOB prediction canon? Position it relative to: Cont, Stoikov & Talreja (2010); Cont, Kukanov & Stoikov (2014, OFI); Zhang et al. (2019, DeepLOB); Sirignano & Cont (2019); Kolm, Turiel & Westray (2023, deep learning survey).
- What does this paper leave unanswered that matters for LOB-based trading?
- What follow-up work would be needed to make this signal tradeable?

#### 8. Verdict
- **Replication priority**: High / Medium / Low — and why
- **Implementation feasibility**: score 1–5 (1 = pure theory, 5 = could implement next week with available data)
- **Key risk**: the single biggest reason this might not work in practice
- **One-line takeaway**: the single most useful thing from this paper for the topic's problem statements

---

## Section C — Seed Papers

The following papers form a strong starting point for citation graph traversal on the LOB prediction and trading topic:

1. **Cont, Stoikov & Talreja (2010)**. "Continuous-time stochastic model for the dynamics of a limit order book." — Foundational LOB dynamics model; heavily cited across the field.

2. **Cont, Kukanov & Stoikov (2014)**. "The price impact of order book events." — Introduces Order Flow Imbalance (OFI) as a linear predictor of price changes; the canonical LOB feature paper.

3. **Zhang, Zohren & Roberts (2019)**. "DeepLOB: Deep convolutional neural network for limit order books." — The benchmark deep learning model for LOB prediction; most subsequent ML-on-LOB papers compare against it.

4. **Sirignano & Cont (2019)**. "Universal features of price formation in financial markets: perspectives from deep learning." — Shows that LOB dynamics share universal features across stocks; important for cross-asset generalisation.

5. **Kolm, Turiel & Westray (2023)**. "Deep order flow imbalance: extracting alpha at multiple horizons from the limit order book using deep learning." — Extends OFI with deep learning; bridges the classical OFI line and the DeepLOB line.

6. **Cartea, Jaimungal & Penalva (2015)**. "Algorithmic and High-Frequency Trading." — Textbook covering market making and optimal execution with LOB models; theoretical grounding for implementation.

7. **Avellaneda & Stoikov (2008)**. "High-frequency trading in a limit order book." — Foundational market-making model; essential for any strategy that involves providing liquidity.

8. **Gould, Porter, Williams, McDonald, Fenn & Howison (2013)**. "Limit order books." — Comprehensive survey of LOB modelling; useful for mapping the landscape.

9. **Lucchese, Pakkanen & Veraart (2024)**. "The short-term predictability of returns in order book markets: a deep learning perspective." — Recent work examining what prediction horizons are feasible and where signal decays; directly relevant to Problem Statement 1.

10. **Arroyo, Gatarek, Balderas & Wan (2024)**. "Deep attentive survival analysis in limit order books: estimating fill probabilities with convolutional-transformers." — Addresses fill probability estimation, directly relevant to Problem Statement 2 (translating signals to execution).
