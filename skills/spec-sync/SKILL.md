---
name: spec-sync
description: Keep docs/SPEC.md in sync when changing golfbot's Telegram surface or data model. Use BEFORE implementing any change to message text, inline buttons, slash commands, or state shape — the spec is updated first, then the code follows.
---

# Spec sync

`docs/SPEC.md` is the source of truth for golfbot's user-visible behavior. Code
follows it, not the other way around.

This exists because the spec already drifted once: `CLAUDE.md` described the
project as unimplemented stubs long after it had been running in production for
months. A spec nobody trusts is worse than no spec, because people act on it.

## When this applies

Any change to:

- Telegram **message text** — including a digest row, a status line, an emoji
- Inline **buttons** — labels, layout, or callback semantics
- **Slash commands** — new, removed, or changed arguments
- The **data model** — `state.json` shape, `bookings.jsonl` lines, or the
  domain dataclasses in `models.py`
- **Config schema** — anything in `config.yaml` that `config.py` validates

Not required for: internal refactors, provider parsing changes, test changes,
or anything a group member could not observe.

## Order of operations

**1. Read the relevant SPEC section first.** Find the section that governs what
you're about to change (`Notification mocks`, `Telegram commands`,
`Data model`, `Config schema`).

**2. Check whether the spec already matches reality.** It may not — the spec has
known stale areas. If what's written no longer describes the running system,
say so before editing. Don't quietly overwrite the drift; the gap is
information about an undocumented decision.

**3. Edit SPEC.md.** Update the mock/table/schema to show the *new* intended
behavior. Mocks in SPEC are literal — write the message exactly as it will
render, including emoji and spacing.

**4. Then implement.** The code should now be a transcription of what the spec
says.

**5. Check for a decision.** If the change involved a real tradeoff — something
a future reader would ask "why is it like this?" about — add an ADR under
`docs/decisions/`. Spec says *what*; ADRs say *why*.

## Things that are decisions, not gaps

If the spec seems to be "missing" one of these, it isn't. They were removed
deliberately in the v2 Gold Star pivot
([ADR 0006](../../docs/decisions/0006-gold-star-pivot.md)) after months of
production use showed the group never used them. Re-adding any of them is an
intent change that needs the owner's sign-off, not a spec gap to fill:

- **In-bot voting** — `✅ Yes` / `❌ No`, vote tallies, roster display
- **Per-member availability** — `/out`, `/in`, `/avail`, player-count-driven
  search expansion
- **Booking tracking** — `📖 Booked it`, `/unbook`, `bookings.jsonl` writes
- **Grading** — A/B/C grades, course tiers, ideal-vs-acceptable windows
- **The digest** — one message listing every match. v2 pushes one alert per
  qualifying slot instead, and `/full` covers the on-demand listing.

The one remaining known inconsistency: SPEC's literal alert mock shows
`🎯 Book it now, dumbass`, while the starter pool in the same document lists
that headline with a trailing `⛳`. The code renders pool entries verbatim, so
the emoji appears. Fix is one character in `config.yaml` if the mock should
win — don't add emoji-stripping to the renderer.

## Reminder

The bot posts to a live group chat with three real people in it. A message
change ships the moment the process restarts — there's no staging environment
and nothing can be un-sent.
