# Event-time evidence contract

Status: deterministic local extraction implemented. No announced time is
promoted to a confirmed deployment completion time.

Grain: one row per `(appid, event_id)`.

The table distinguishes:

- news publication time;
- announced maintenance start;
- announced live-event start;
- approximate future transition period;
- independently confirmed effective/deployment completion time.

`announced_at_utc` may be parsed from explicit UTC or PT wording. To remain
dependency-light on Windows, 2007-and-later PT dates use the codified US rule:
DST begins at 02:00 on March's second Sunday and ends at 02:00 on November's
first Sunday. The basis is stored on every parsed row. The source phrase is
represented by a SHA-256 hash rather than copying the excerpt to the derived
table.

Every current row has `promotion_allowed=false` and `effective_at=null` because
an announcement does not prove when deployment completed. A later verification
workflow must retain evidence URL, observation time and method before promotion.
