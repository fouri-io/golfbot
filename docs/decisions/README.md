# Decision log

Short records of *why* golfbot is built the way it is. These are backfilled
from decisions already visible in the code — they document what was chosen and
what it cost, so a future change doesn't quietly undo a deliberate tradeoff.

| # | Decision | Status |
|---|---|---|
| [0001](0001-flat-files-over-database.md) | Flat JSON files instead of a database | Accepted |
| [0002](0002-curl-cffi-for-cloudflare.md) | `curl_cffi` to reach WebTrac through Cloudflare | Accepted |
| [0003](0003-policy-b-best-per-course-date.md) | Notify one best slot per course+date | Superseded by 0006 |
| [0004](0004-availability-weekly-pattern.md) | Availability = weekly pattern + per-date overrides | Superseded by 0006 |
| [0005](0005-two-notification-models.md) | Two parallel notification models | Resolved by 0006 |
| [0006](0006-gold-star-pivot.md) | Gold Star pivot — scanner-only, retire voting + availability | Accepted |

## Writing a new one

Copy the shape of an existing file: context, decision, consequences (including
the bad ones), status. Number sequentially. Keep it under a page — if it needs
more, it's a spec change and belongs in [SPEC.md](../SPEC.md).

Statuses: **Accepted** (in force), **Under review** (known tension, being
worked), **Superseded by NNNN** (kept for history, don't follow it).
