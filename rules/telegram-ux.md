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
- **Tee-time IDs** are deterministic: `{course_key}:{YYYY-MM-DD}:{HHMM}`. The
  same physical slot yields the same id across polls, which is what makes
  dedup trivial. The **spot count is deliberately not part of the id** — a slot
  going 2 → 3 spots is the same slot with more availability. Don't add
  randomness, timestamps, a counter, or the spot count to an id.
- **Admin-only actions** (`/pause`, `/resume`, `/garmin`) check
  `config.group.admin`. Taps from non-roster users are **silently ignored** —
  no error reply. Don't "helpfully" tell a stranger they lack permission.
- **There are no callback buttons.** The only button anywhere is the `🔗 Book it`
  URL link, which produces no callback data, so the bot registers zero
  `CallbackQueryHandler`s. If you add a callback button you must add its handler
  too — nothing will catch it otherwise.

## Noise discipline

The single most valuable property of this bot is that it stays quiet. A scanner
that cries wolf gets muted, and then it's worthless.

- The **Gold Star alert is the only push**. Everything else is on demand
  behind a command. Adding a second push type needs the owner's sign-off.
- A slot re-alerts only when it reappears with **more** open spots than last
  announced. Equal or fewer is silence
  ([SPEC > Re-alert semantics](../docs/SPEC.md)).
- Before adding any new proactive message, answer: what does the group do
  differently on receiving it? If there's no answer, it's noise.

## Snark is data, not code

Alert headlines live in `alerts.headlines` in `config.yaml` so copy can change
without touching code. `{name}` is substituted at send time from the roster.
Keep the pool in config; don't hardcode copy in `notifier.py` beyond the single
fallback used when the pool is empty.

## Testing

Render functions are pure string builders — assert on their output directly.
Only `send_*` / `mark_*` coroutines touch the network, and tests must not.
