# Update decision mart contract

Status: local prototype implemented from measured review data and rule-based
event classifications. It is not a causal model or a production alert policy.

Grain: one row per `(appid, event_id)`.

## Separate score families

1. **Expected monitoring prior** combines rule-based scope (50%), technical
   risk (30%) and monetization risk (20%). It describes where to watch closely,
   not whether an update performed badly.
2. **Observed reaction** currently contains only worsening negative-review
   share, scaled so +20 percentage points reaches 100. Player and issue signals
   remain null until event-aligned observations exist.
3. **Data confidence** rewards verified event time, human-verified class,
   complete windows, adequate samples and narrower intervals. Ten points are
   reserved for aligned player/issue evidence and are currently unavailable.
4. **Decision priority** combines prior (35%) and observed reaction (65%), but
   is always accompanied by confidence, quality flags and an action string.

Incomplete windows, low samples and unverified event time force conservative
recommendations. Every row sets `causal_claim_allowed=false`. Policy weights
are versioned as `update_decision_v1` and require owner/analyst validation before
operational use.

Announced maintenance/event timestamps may be joined for operational context,
but they do not populate `effective_at` or increase confidence until deployment
completion is independently observed.
