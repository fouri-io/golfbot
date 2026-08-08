# 0004 — Availability as a weekly pattern plus per-date overrides

**Status:** Superseded by [0006](0006-gold-star-pivot.md) — the availability
layer is deleted in the Gold Star pivot.

## Context

The original design filtered by a global `search.days_of_week` in
`config.yaml` — one weekday list for the whole group. That doesn't survive
contact with three people who have different schedules, and it can't express
"Ed is out next Thursday" without editing config and restarting.

## Decision

Availability is per member, stored in `state.json` under `availability`, as
three fields per person (`availability.AvailabilityRecord`):

- `out_weekdays` — the recurring weekly pattern; the member is out every week
  on these weekdays. New members default to `{Sat, Sun}`.
- `out_dates` — specific dates the member is out despite normally being in.
- `in_dates` — specific dates the member is in despite normally being out.

The model is **available unless explicitly marked out**. Members whose
`telegram_user_id` is `0` are placeholders and are excluded from every
availability calculation until they register via `/whoami`.

The scanner uses this twice per date: to decide whether to scan the date at
all, and to set `min_players` to the number of available registered members —
so a day when someone is out searches for a 2-some instead of failing to find
a foursome.

`search.days_of_week` remains in the config schema but is now **advisory
only** — `pipeline.filter_and_grade` no longer reads it.

## Consequences

Good:

- Schedule changes are a chat message (`/out thu`), not a config edit and
  restart.
- Searching for the party size that can actually play finds slots that a
  fixed 3- or 4-player search would miss entirely.
- Per-date overrides in both directions mean the weekly pattern doesn't have
  to be right, just usually right.

Bad:

- Availability lives in `state.json`, which is gitignored runtime data. It is
  not backed up and does not survive `rm -rf data/`.
- `days_of_week` still validating in config while doing nothing is a trap.
  Someone will edit it and expect a behavior change.
- Whether the admin's availability *gates* a whole date is controlled by
  `group.admin_required`, which defaults to `False` in `config.py`. Written
  documentation has described the gate as on by default; treat the code as
  authoritative and check the config.

## Revisit if

The group grows past a handful of people, at which point "everyone available"
stops being the common case and the min-players heuristic needs to become a
real constraint solve.
