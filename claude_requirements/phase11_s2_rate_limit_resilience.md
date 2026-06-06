# Phase 11: Semantic Scholar Rate-Limit Resilience

## Problem

Clicking **Discover Citations** on a paper with 384 known S2 citations returned
"0 papers discovered, 0 synthesized." The papers exist — the scout silently
swallowed them.

Root cause in [`semantic_scholar.py`](../poneglyph/services/semantic_scholar.py):

```python
if resp.status_code == 429:
    logger.warning("S2 rate limited — backing off 60s")
    await asyncio.sleep(60)
    return None            # gives up after one backoff
```

`_get` returns `None` on HTTP 429. `_fetch_paged` then hits `if not data: break`
and returns an empty list. **A throttled request is indistinguishable from
"no citations."** The scout reports a misleading `0` instead of surfacing the
throttle.

Two compounding issues:
1. No retry — a single 429 permanently zeroes that request.
2. No signal — callers can't tell "empty" from "rate-limited," so the run's
   status box says `0 discovered` with a success checkmark.

## Fix

### 1. Retry-with-backoff in `_get`
- On 429: respect the `Retry-After` header if present, else exponential backoff
  (e.g. 5s, 15s, 45s capped), retry up to `_MAX_RETRIES` (3) times before giving up.
- On transient network errors / 5xx: same bounded retry.
- Only return `None` after retries are exhausted.

### 2. Distinguish "rate-limited" from "empty"
- Raise a typed `S2RateLimitError` when retries are exhausted on a 429, rather
  than silently returning `None`.
- `_fetch_paged`, `get_paper`, etc. propagate it.
- `discover_from_paper` lets it bubble; `run_paper_enrichment` records the run
  status as a distinct rate-limited error message ("Semantic Scholar rate limited
  — try again shortly") instead of `0 discovered`.
- Scout status box (`scout.py`) already renders `error_message` for `status='error'`,
  so the throttle message shows up there with the error border.

## Root cause of the persistent throttling (discovered during verification)
`settings.semantic_scholar_api_key` is **not set**. Unauthenticated clients share
one small global S2 request pool, so 429s are frequent and a burst of requests
(or repeated testing) saturates it for an extended window. Even 230s of backoff +
a 90s cooldown returned 429 on every call once the pool was saturated.

**Recommended action: obtain a free Semantic Scholar API key**
(https://www.semanticscholar.org/product/api#api-key) and set
`SEMANTIC_SCHOLAR_API_KEY` in `.env`. This raises the limit to ~1 req/sec
dedicated, making discovery reliable. The retry/backoff fix still applies as a
safety net, but the API key is the real fix for throughput.

## Out of scope
- Global request budget / token-bucket across concurrent scouts.
- Caching S2 responses.

## Verification
- Re-run discovery for paper 637 (The Price of Correlation Risk, 384 citations)
  and confirm citations/references come back non-zero.
