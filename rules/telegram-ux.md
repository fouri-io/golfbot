# Telegram surface rules

The bot talks to three real people in a live group chat. Changes here are
visible immediately and can't be un-sent.

## Spec first — non-negotiable

Any change to **message text, inline buttons, commands, or the data model**
updates [`docs/SPEC.md`](../docs/SPEC.md) *first*, then the code. The spec is
the source of truth for UX; code follows it.

Use the `spec-sync` skill rather than doing this from memory.

If implementing reveals the spec was wrong, stop and fix the spec — don't let
the code silently win. A spec nobody trusts is the failure mode this rule
exists to prevent.

## Conventions to preserve

- **Times** are stored as ISO 8601 with an `America/Chicago` offset and always
  displayed in CT. Never render a naive datetime to a user.
- **Tee-time IDs** are deterministic:
  `{course_key}:{YYYY-MM-DD}:{HHMM}:{players}`. The same physical slot yields
  the same id across polls, which is what makes dedup trivial. Don't add
  randomness, timestamps, or a counter to an id.
- **Admin-only actions** (`/pause`, `/resume`, `/unbook`, booking confirmation)
  check `config.group.admin`. Taps from non-roster users are **silently
  ignored** — no error reply. Don't "helpfully" tell a stranger they lack
  permission.
- **Callback data** is parsed defensively. An old message's buttons can be
  tapped days later, after the underlying state is gone; handle the miss
  rather than raising.

## Noise discipline

The single most valuable property of this bot is that it stays quiet.

- Digests fire only when the match set actually changes
  ([ADR 0003](../docs/decisions/0003-policy-b-best-per-course-date.md)).
- Prefer **editing** an existing message to sending a new one.
- Before adding any new proactive message, answer: what does the group do
  differently on receiving it? If there's no answer, it's noise.

## Two notification models exist

`notifier.py` serves both the per-slot voting model and the digest model. They
are **not** layered, and only the digest is live. Read
[ADR 0005](../docs/decisions/0005-two-notification-models.md) before touching
`notifier.py` so you extend the right one — the split is unresolved, so ask
rather than picking.

## Testing

Render functions are pure string builders — assert on their output directly.
Only `send_*` / `mark_*` coroutines touch the network, and tests must not.
