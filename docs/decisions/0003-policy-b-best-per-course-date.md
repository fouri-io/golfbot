# 0003 — Notify one best slot per course + date ("Policy B")

**Status:** Superseded by [0006](0006-gold-star-pivot.md) — grading and
per-course-date selection are retired in the Gold Star pivot.

## Context

A single scan across 9 courses × a 7-day horizon can surface dozens of
qualifying slots. Roy Kizer alone might have eight acceptable times on a
Tuesday. Sending all of them — or re-sending as the set churns — turns the
group chat into noise, and noise is what kills a tool three friends actually
use.

## Decision

After filtering and grading, collapse to **one match per (course, date)** pair.
Tiebreak on higher grade first, then earlier tee time.

Implemented as `pipeline.apply_policy_b`. It runs for the digest path and for
`golfbot scrape`, so the preview and the real notification always agree.

Separately, a digest only fires when the match **set** changes. `_signature`
in `scanner.py` compares course, date, time, player count, and the per-date
roster against the previous scan; identical means no message.

## Consequences

Good:

- The digest stays one readable screen: one row per course-date.
- Stable availability produces zero messages. The bot is silent unless
  something actually changed.
- Roster is part of the signature, so a member running `/out` re-fires the
  digest with corrected numbers — availability changes are treated as real
  changes, not suppressed as duplicates.

Bad:

- You cannot see the second-best time at a course without `/full`. If the
  8:00 gets taken, the 8:10 that was always there reads as "new."
- Tiebreaking on *earlier* time is a hardcoded group preference. It is not
  configurable and does not follow from `time_windows`.
- Grade compresses tier and time-of-day into one letter, so the tiebreak can
  prefer a tier-1 course at a worse hour over a tier-2 course at the ideal
  hour. That is intended, but it is a judgment call baked into an enum.

## Revisit if

The group starts wanting to compare times *within* a course from the digest,
rather than dropping into `/full` for it.
