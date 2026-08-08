# 0005 — Two parallel notification models

**Status:** Resolved by [0006](0006-gold-star-pivot.md) — the split is closed by
deleting Model A (per-slot voting) entirely. Model B (the digest scanner) is
what remains, simplified into the Gold Star alert. Kept for history; do not
build on Model A.

## Context

golfbot grew a second notification model without retiring the first. Both are
in the tree, both have tests, and they share almost nothing.

### Model A — per-slot voting (P1, the original SPEC design)

One Telegram message per tee-time slot, carrying Yes/No vote buttons. The
message is edited in place as votes land; the admin taps `📖 Booked it` to
close it out.

- Domain type: `models.TeeTimeSlot` with a `votes: dict[str, Vote]`
- State: `state["tee_times"]`, a list of slot dicts
- Mutations: `actions.py` (`mark_booked`, vote handling, undo)
- Render/send: `notifier.send_new_slot`, `update_tally`, `mark_booked`,
  `mark_expired`
- History: `data/bookings.jsonl` via `store.append_booking`
- Reachable via: `golfbot mock`, plus the `handle_callback` and `/unbook`
  handlers in `bot.py`

### Model B — digest (P3, what actually runs)

One message per scan listing every current match, with numbered confirm
buttons. Sent only when the match set changes.

- Domain type: `pipeline.Match`, serialized by `scanner.match_to_dict`
- State: `state["last_scan"]["matches"]`
- Mutations: `bookings.py`, writing `state["bookings"]` keyed by ISO date
- Render/send: `notifier.render_digest`, `send_digest`, `build_digest_keyboard`
- History: none beyond current state
- Reachable via: the scheduler, `/scan`, `/tee`

## The problem

These are not layered — they are duplicated. Two booking stores that never
reconcile (`bookings.jsonl` vs `state["bookings"]`), two notions of a
"confirmed booking" (`models.Booking` vs a frozen Match dict), and two ways a
member expresses intent (a vote on a slot vs the admin confirming a row).

Concretely:

- A booking made through the digest never appears in `bookings.jsonl`.
- `models.Booking`, and the `votes` concept generally, are effectively dead in
  the running system — the group votes verbally, not in the bot.
- `notifier.py` is 799 lines largely because it renders for both models.
- The `❌ No` semantics documented in SPEC ("out for the entire day, triggers
  2-player expansion") were superseded by [ADR 0004](0004-availability-weekly-pattern.md) —
  availability now comes from `/out`, not from votes.

## Not yet decided

The live behavior is Model B. The open question is whether Model A gets
deleted, or whether per-slot voting is intended to come back in revised form.
That's a question about what golfbot is *for*, not a refactor — so it is
deliberately left open here and belongs in the SPEC revision.

**Until it's resolved:** don't build new features on Model A, and don't delete
it either. Ask.
