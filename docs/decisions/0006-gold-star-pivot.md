# 0006 — The Gold Star pivot: scanner-only, retire voting and availability

**Status:** Accepted (2026-08-07)

Resolves [0005](0005-two-notification-models.md) (the open question there is now
answered: Model A is deleted, not revived). Supersedes
[0003](0003-policy-b-best-per-course-date.md) and
[0004](0004-availability-weekly-pattern.md).

## Context

golfbot has run in production for several months. Two things became clear from
actual use:

- **Nobody uses the full availability list.** The group rarely scrolls a digest
  of everything that's open. The signal that matters is narrow: *is one of our
  good courses open at a good time?*
- **The in-bot coordination features are dead.** Voting (`✅ Yes` / `❌ No`),
  per-member availability (`/out`, `/in`, `/avail`), and in-bot booking all went
  unused — the group decides who's in and who books over text, offline. This is
  the dead "Model A" documented in [ADR 0005](0005-two-notification-models.md)
  and the availability layer from [ADR 0004](0004-availability-weekly-pattern.md).

So golfbot's real job shrank to one thing: **be a very good scanner that stays
silent unless there's a genuinely great opportunity.** Everything built to
support group coordination inside the bot is overhead.

## Decision

Refactor golfbot into a **scanner-only** tool built around a single concept:
the **Gold Star alert**.

**Qualification (the Gold Star rule).** A slot alerts iff *all* hold:
- course is in the **all-star set** — Jimmy Clay, Roy Kizer, Riverside, Grey Rock
- tee time is within the single global **premium window** (config, e.g. 07:20–08:00)
- weekday (Mon–Fri)
- at least **one** spot open

No grades, no tiers, no player-count matching, no ideal-vs-acceptable split. The
premium window *is* the quality bar. This retires the two-axis grading of
[ADR 0003](0003-policy-b-best-per-course-date.md).

**Scan all, alert on four.** Every configured course is still scraped so `/full`
can show the complete picture on demand; only the four all-star courses can
trigger an alert.

**Re-alert on more room.** Slot identity is `course:date:time` (player count
leaves the ID). A slot is alerted once; if its open-spot count later *increases*
(a better opportunity), it alerts again. A decrease is silent.

**Snark.** The alert headline is drawn at random from a configurable pool
(`alerts.headlines`), no immediate repeats, with an optional `{name}` token
filled from the roster. The group are self-described degenerate golf addicts;
the copy should read like it.

**Delete Model A entirely** — per-slot voting, vote tally rendering,
`📖 Booked it`, `/unbook`, `models.Booking`, `data/bookings.jsonl`, and the
post-booking horizon suppression. Booking is fully offline. This closes the
split in [ADR 0005](0005-two-notification-models.md).

**Delete the availability layer** — `availability.py`, `/out` / `/in` /
`/avail`, and player-count-driven search expansion. Supersedes
[ADR 0004](0004-availability-weekly-pattern.md).

## Consequences

**Good**
- One notification model, one (zero) booking store — the reconciliation problem
  in ADR 0005 disappears rather than getting fixed.
- `notifier.py` shrinks substantially (it existed at ~799 lines largely to serve
  both models).
- The data model loses `votes`, `bookings`, and `availability` — `state.json`
  becomes a thin dedup cache plus a raw-slot cache for `/full`.

**Cost / risk**
- **State migration.** Existing `state.json` carries `votes`, `bookings`, and
  `availability` keys that the new code ignores. On first run under v2 they're
  dropped. `data/bookings.jsonl` is orphaned (kept on disk for history, no
  longer written).
- Losing per-member availability means the scanner can't suppress a date because
  someone's out — but that suppression was never used, so this is removal of
  dead weight, not a regression.
- Grey Rock and Riverside are GolfNow courses: `players_available` there is a
  lower bound, not an exact count (see `CLAUDE.md` > Known quirks). The alert's
  spot count is exact only for GolfATX courses; for GolfNow it's "at least N".

## Alternatives considered

- **Keep voting in a revised form** — rejected. Months of non-use is the
  answer; a scanner that pings and gets out of the way is what the group
  actually wants.
- **Tiered grading on `/full`** — deferred, not rejected. `/full` ships as a
  flat list; a light sort/tier can come back later if browsing the firehose
  proves annoying. Left out under "less is more."
