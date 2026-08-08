# Architecture

How golfbot is actually wired, as-built. [SPEC.md](SPEC.md) is the source of
truth for *intended* UX and data model; this file describes the *current*
module graph so you can find your way around the code.

> Where the two disagree, that's a bug in one of them — say so rather than
> guessing which is right.

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
it's a preview of what *would* notify.

## Module layers

Dependencies point downward. Nothing below imports anything above it.

```
  interface     __main__.py ──── bot.py ──── mock_source.py
                     │              │              │
  presentation       └──────────────┴──── notifier.py
                                              │
  orchestration                          scanner.py
                                              │
        ┌─────────────┬──────────┬────────────┼──────────────┐
  domain│          pipeline   grading    availability    providers/
        │             │          │                       base ─┬─ golfatx
        │             └──────────┘                             └─ golfnow
        │
  leaf   config.py   models.py   horizon.py   store.py   actions.py
         bookings.py  weather.py
```

**Leaf modules** have no internal dependencies (except on `config`) and no
side effects worth mocking. `config.py` is imported by nearly everything;
`store.py` is the only module that touches the filesystem; `weather.py` and
`providers/*` are the only ones that touch the network.

**`pipeline.py` is pure.** Same input, same output, no I/O. That's deliberate —
it's the piece most worth unit-testing, and `scrape` and the scheduler share it
so they can't drift.

Note that `bot.py` and `scanner.py` use **function-local imports** in many
places (`from golfbot import scanner as _scanner` inside a handler). That's
working around import cycles, not a style choice.

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
        ├─ availability.load_availability(state)
        │
        ├─ run_full_scan → provider.fetch_slots(courses, date, min_players=2)
        │       └─ RawSlot[]  ← cached into state.last_scan.raw_slots so /full
        │                        costs no API calls
        ├─ run_scan(prefetched=raw)
        │       ├─ availability.date_should_be_scanned  (drop admin-out dates)
        │       ├─ players_to_search_for                (drop under-sized slots)
        │       ├─ pipeline.filter_and_grade            (course known? window? grade?)
        │       ├─ pipeline.apply_policy_b              (best one per course+date)
        │       └─ annotate members_in / members_out
        │       → Match[]
        │
        ├─ weather.fetch_forecast (if cache stale)
        ├─ _signature(new) == _signature(prev)? → no digest, just touch run_at
        └─ notifier.send_digest → Telegram
```

`_signature` includes the per-date roster, so a member running `/out`
re-fires the digest with an updated roster even when the slots are unchanged.

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

That `players_available` asymmetry leaks into the domain: for GolfNow the value
is the number we *queried with*, not the number actually open. Any logic
comparing it to a roster size must treat it as a floor.

## State

`data/state.json`, one JSON object:

| Key | Written by | Purpose |
|---|---|---|
| `paused`, `pause_started_at` | `bot.cmd_pause/cmd_resume` | Mute digests |
| `last_poll_at`, `last_digest_at` | `scanner` | `/status` stamps |
| `last_scan.matches` | `scanner` | Feeds `/tee` re-render |
| `last_scan.raw_slots` | `scanner` | Cache so `/full` is free |
| `last_scan.telegram_message_id` | `scanner` | Lets callbacks edit the digest in place |
| `availability` | `availability.save_availability` | Per-member weekly pattern + date overrides |
| `bookings` | `bookings.py` | One active booking per date |
| weather cache | `weather.save_cache` | Forecast, refreshed per `cache_hours` |

`data/bookings.jsonl` is append-only history, written by `store.append_booking`.

**Heads-up:** there are currently two parallel booking stores — `state["bookings"]`
(digest path, live) and `bookings.jsonl` (per-slot voting path, only reachable via
`golfbot mock`). See [ADR 0005](decisions/0005-two-notification-models.md); this
is unresolved.

## Testing seams

- `pipeline`, `grading`, `horizon`, `actions`, `models` — pure, test directly.
- Providers — tested against recorded fixtures in `tests/fixtures/`, never live.
- `notifier` — render functions are pure string builders and tested as such;
  only the `send_*` / `mark_*` coroutines touch Telegram.
- `store` — tested against `tmp_path`.

See [rules/testing.md](../rules/testing.md).
