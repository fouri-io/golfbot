# Architecture

How golfbot is actually wired, as-built. [SPEC.md](SPEC.md) is the source of
truth for *intended* UX and data model; this file describes the *current*
module graph so you can find your way around the code.

> Where the two disagree, that's a bug in one of them — say so rather than
> guessing which is right.

This describes **v2 (Gold Star)**. See
[ADR 0006](decisions/0006-gold-star-pivot.md) for what was removed and why.

## One process, two entrypoints

`golfbot run` starts a single asyncio process holding two concurrent jobs:

| Job | Driver | Lives in |
|---|---|---|
| Telegram listener | `python-telegram-bot` long-polling | `bot.py` |
| Scheduled scan | APScheduler, interval + jitter | `scanner.scan_and_notify` |

Both share `data/state.json`. Writes go through `store.save_state`, which is
atomic (temp file + `os.replace`) under an `asyncio.Lock`; reads need no lock
because a reader observes either the whole old file or the whole new one.

`golfbot scrape` runs the same scan core with no Telegram and no persistence —
a preview of what *would* alert. `golfbot mock` injects one synthetic Gold Star
to check the alert renders, without waiting for a real cancellation.

## Module layers

Dependencies point downward. Nothing below imports anything above it.

```
  interface     __main__.py ──── bot.py ──── mock_source.py
                     │              │              │
  presentation       └──────────────┴──── notifier.py
                                              │
  orchestration                          scanner.py
                                              │
              ┌───────────────┬───────────────┴────────────┐
  domain  pipeline.py    providers/base ─┬─ golfatx     actions.py
          (Gold Star rule)               └─ golfnow    (the ledger)
              │
  leaf   config.py   models.py   horizon.py   store.py   weather.py
```

**Leaf modules** have no internal dependencies (except on `config`).
`config.py` is imported by nearly everything; `store.py` is the only module
that touches the filesystem; `weather.py` and `providers/*` are the only ones
that touch the network.

**`pipeline.py` and `actions.py` are pure.** Same input, same output, no I/O.
That's deliberate — they hold the two rules that matter (what qualifies, and
what's worth announcing), and `scrape` and the scheduler share them so they
can't drift.

Note that `bot.py` uses **function-local imports** in several handlers
(`from golfbot import scanner as _scanner`). That's working around import
cycles, not a style choice.

## The two rules

Everything golfbot does reduces to these.

**1. What qualifies** — `pipeline.qualifies`, a flat conjunction
([SPEC > The Gold Star rule](SPEC.md)):

```
all-star course  AND  premium window  AND  weekday  AND  spots >= 1
```

No grades, no tiers, no player-count matching. Window bounds are inclusive on
both ends.

**2. What's worth announcing** — `actions.should_alert`, driven by
`last_alerted_spots` on the ledger entry ([SPEC > Re-alert semantics](SPEC.md)):

| Ledger state | Result |
|---|---|
| Never alerted | alert |
| More spots than last alerted | re-alert — a better opportunity |
| Equal or fewer spots | silent |
| Tee date has passed | pruned silently |

Slot identity is `course_key:YYYY-MM-DD:HHMM` — **the spot count is not part of
the id**, so a slot going 2 → 3 spots is the same record with more availability,
not a new one.

## Scan data flow

```
config.yaml + .env
        ↓
   Config (pydantic, validated)
        ↓
   __main__ builds {provider_name: Provider}
        ↓
   scanner.scan_and_notify
        │
        ├─ active_window check ──────────────→ skip if outside (unless force)
        ├─ horizon.current_window → dates[]
        ├─ actions.prune_past_slots
        │
        ├─ run_full_scan(min_players=1) → provider.fetch_slots
        │       └─ RawSlot[]  ← cached to state.raw_slots so /full is free
        │
        ├─ pipeline.gold_star_slots  ← rule 1
        │       └─ Match[]
        │
        ├─ weather.fetch_forecast (if cache stale)
        │
        └─ for each Match:
             actions.upsert_slot     → fold into state.tee_times
             actions.should_alert    ← rule 2
               └─ notifier.send_new_slot  (one message per slot)
                    └─ actions.record_alert
```

Every configured course is scanned so `/full` shows the complete picture; only
all-star courses can clear rule 1 and alert ("scan all, alert on four").

A failed send leaves the slot un-stamped, so the next scan retries it rather
than swallowing the alert.

## Notifications

There is exactly **one push**: the Gold Star alert, one message per qualifying
slot. Everything else is on demand.

| Surface | Trigger | Renderer |
|---|---|---|
| Gold Star alert | scan finds a due slot | `notifier.render_open` |
| `/full` | asked | `notifier.render_full_listing` |
| `/status` | asked | `notifier.render_status` |

The alert headline is drawn from `alerts.headlines` in config by
`notifier.pick_headline` — uniform random, never repeating the immediately
previous line, with `{name}` replaced by a random roster member. The no-repeat
check is on the template rather than the rendered text, so the same line can't
recur with a different name.

**The bot registers zero `CallbackQueryHandler`s.** The only button anywhere is
a URL button (`🔗 Book it`), which produces no callback data. If you add a
callback button you must also add a handler — there is no fallback.

## Providers

`providers/base.Provider` is a `Protocol` — a provider is any object with
`name` and `async fetch_slots(courses, target_date, min_players) -> list[RawSlot]`.
Providers filter `courses` down to the ones they own before issuing requests.

| | GolfATX (WebTrac) | GolfNow |
|---|---|---|
| Transport | `curl_cffi` (Firefox TLS fingerprint) | `httpx` |
| Why | Cloudflare blocks plain httpx — see [ADR 0002](decisions/0002-curl-cffi-for-cloudflare.md) | Public JSON endpoint |
| Requests | 1 per **date** (returns all muni courses) | 1 per **facility × date** |
| `players_available` | Exact — WebTrac exposes open slots | **Lower bound only** — ">= what we asked for" |

That asymmetry leaks into the alert: a spot count is exact for Jimmy Clay and
Roy Kizer, but for Riverside and Grey Rock it means "at least N."

A GolfATX course missing from `WEBTRAC_NAME_BY_CODE` returns zero slots with
only a log warning — the most common "why is this course empty?" cause.

## State

`data/state.json`, one JSON object (see [SPEC > Data model](SPEC.md)):

| Key | Written by | Purpose |
|---|---|---|
| `paused`, `pause_started_at` | `bot.cmd_pause/cmd_resume` | Mute alerts |
| `last_poll_at` | `scanner` | `/status` stamp; also the `/full` "as of" time |
| `next_run_at` | `scanner` | `/status` next-scan stamp |
| `last_alert_at` | `scanner` | `/status` stamp |
| `tee_times` | `actions` | **The ledger** — dedup + re-alert bookkeeping |
| `raw_slots` | `scanner` | Last full scan, cached so `/full` costs no API calls |
| `weather` | `weather.save_cache` | Forecast, refreshed per `cache_hours` |

`data/bookings.jsonl` is **no longer written**. Booking happens offline. An
existing file is left on disk as history.

## Testing seams

- `pipeline`, `actions`, `horizon`, `models` — pure, test directly.
- Providers — tested against recorded fixtures in `tests/fixtures/`, never live.
- `notifier` — render functions are pure string builders; only `send_new_slot`
  touches Telegram.
- `scanner` — `scan_and_notify` is covered end-to-end with a fake `Bot`, fake
  providers, and `weather` disabled on the test config so nothing reaches
  Open-Meteo.
- `store` — tested against `tmp_path`.

Scanner tests derive their dates from the *live* horizon rather than hardcoding
them, so they don't rot as the calendar moves.

See [rules/testing.md](../rules/testing.md).
