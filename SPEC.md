# Austin Golf Tee Time Watcher — Spec

A lightweight, self-hosted bot that monitors Austin municipal golf tee times
and notifies a small group via Telegram when desirable times become available.
The group votes inside Telegram; the admin books manually outside the bot.

## Design philosophy

- **Notify, don't book.** Avoids ToS friction and keeps the system simple.
- **Tiny audience.** 3 fixed members, admin-led. No multi-tenant concerns.
- **Vibe app, not enterprise.** America/Chicago hard-coded, single config file,
  flat-file state, no auth beyond a Telegram bot token.
- **Polite scraping.** Once-per-hour polling, real-browser User-Agent, jitter.
  Stay well under any reasonable rate threshold.

---

## V1 scope

GolfATX courses only. The Austin city-of-muni booking system runs on **WebTrac**
(`txaustinweb.myvscloud.com`), which serves a public, server-rendered search
results page. No login required; CSRF token is fetched from the search form
on each run.

**Out of scope for V1:** Grey Rock, Harvey Penick, any non-WebTrac courses.

### V1 courses

| Course | Tier |
|---|---|
| Roy Kizer | 1 |
| Jimmy Clay | 1 |
| Lions | 2 |
| Hancock | 2 |
| Riverside | 2 |
| Morris Williams | 2 |

(Tiering is a placeholder — confirm before implementation.)

---

## Tech stack

- **Python 3.12+**
- **`httpx`** + **`beautifulsoup4`** for scraping (no Playwright unless we hit blocking)
- **`APScheduler`** for polling cadence (portable to Docker/Pi later)
- **`python-telegram-bot`** in long-polling mode (supports inline-button callbacks)
- **Flat-file persistence** — `state.json` (live) + `bookings.jsonl` (history). No DB.
- **`pydantic`** for config schema validation
- **`PyYAML`** for config loading

---

## Architecture

```
APScheduler ──► scraper (httpx + bs4) ──► parse + filter + grade
                                                  │
                                                  ▼
                                          state.json (live state, votes, open slots)
                                          bookings.jsonl (append-only history)
                                                  │
                                                  ▼
                                          Telegram bot (sends notifications,
                                                        handles button taps
                                                        and slash commands)
```

Two long-running asyncio tasks share the state file:

1. **Poller** — APScheduler triggers scraper jobs on the configured cadence.
2. **Telegram listener** — `python-telegram-bot` long-polling loop handles
   commands and callback queries (button taps).

Both run in a single Python process. No external services beyond Telegram.
Writes to `state.json` go through an asyncio lock + atomic temp-file rename
to prevent corruption.

---

## Search behavior

- **Horizon:** rolling 7 days starting at `today + start_offset_days` (default
  offset = 1, so "tomorrow through 7 days out").
- **After a booking is confirmed** for date `D`: notifications suppress through
  `D`, horizon advances to `D+1 → D+7` until `D` has passed, then snaps back to
  normal.
- **Days of week:** weekdays (Mon–Fri) by default; weekends optional via config.
- **Holes:** 18.
- **Players:** default 3 (firm room for the group). If exactly one member is
  marked out for a date, search expands to also match 2-player slots for that
  date. If 2+ members are out, no notifications for that date.

---

## Grading

Two-axis grading: course tier × time window.

| Course | Time = ideal | Time = acceptable | Outside both |
|---|---|---|---|
| Tier 1 | **A** | **B** | — |
| Tier 2 | **B** | **C** | — |

- **Ideal:** 07:30–08:00 (default; configurable)
- **Acceptable:** 07:00–09:00 (default; configurable)

`notify_min_grade: B` by default — A and B notify, C is suppressed.

---

## Notification policy

When a scheduled poll surfaces multiple matching slots, we filter to avoid
chat noise.

**Policy B (chosen):** per poll cycle, at most **one notification per
(course, date) pair** — the highest-graded slot for that course on that
date. Lower-graded sibling slots are dropped for that poll; if the picked
slot is skipped/booked/expired, the next poll re-evaluates from scratch.

Worst case (6 courses × 2 weekend days × hourly polling) ≈ 12
notifications/hour, but in practice municipal cancellation rates make it
more like 1–3.

A confirmed booking still wipes out all further notifications for that date
via the horizon override. A `❌ No` vote triggers 2-player expansion for
that date on subsequent polls.

This policy is implemented in P3 when the scheduler lands — the mock
injector in P1 fires one slot per invocation regardless.

---

## Group voting

### Members

3 fixed members. Roster lives in `config.yaml`. Admin (Colby) is the only one
who can book/pause/resume/unbook.

### Vote semantics

Every notification carries two vote buttons: `[✅ Yes]`, `[❌ No]`.

- A **`✅ Yes`** vote means "I'm in for this slot."
- A **`❌ No`** vote means "I'm out **for the entire day**" — not just this slot.
  This marks the member unavailable for that date globally and triggers
  2-player expansion for that date.
- Tapping a different button replaces your previous vote.
- Only roster members can vote; taps from non-members are silently ignored.

### Vote tally

The notification message is edited in place as votes come in, showing
who voted what. The admin uses the tally to decide whether to book.
There is no automatic quorum or auto-suggest — the bot just displays.

---

## Booking flow

1. Poller finds a qualifying slot → creates a tee-time row in SQLite, sends
   notification with Yes/No/Maybe + admin-only Booked/Skip/Pause buttons.
2. Group members vote. Tally updates live.
3. Admin clicks the booking link in the notification, books on the WebTrac site
   manually.
4. Admin taps `[📖 Booked it]` in Telegram.
5. Bot edits the message to "BOOKED" state, records booking in SQLite, pauses
   notifications through the booked date, advances horizon.
6. Admin can tap `[↩️ Undo]` to roll back the booking state in the bot (the
   actual booking would need to be cancelled on the WebTrac site separately).

---

## Notification mocks

### Initial state (new slot detected)

```
🏌️ Tee Time Found — Grade A

Roy Kizer · Sat May 23
8:00 AM · 4 players · 18 holes

🔗 Open booking page

👥 Availability:
⏳ Waiting: Colby, Steve, Ed

[ ✅ Yes ] [ ❌ No ]
[ 📖 Booked it ]  [ 🚫 Skip ]  [ 🔕 Pause ]
```

### Votes coming in (edited in place)

```
🏌️ Tee Time Found — Grade A

Roy Kizer · Sat May 23
8:00 AM · 4 players · 18 holes

🔗 Open booking page

👥 Availability:
✅ Yes (2): Colby, Steve
❌ No (1):  Ed
⏳ Waiting: —

[ ✅ Yes ] [ ❌ No ]
[ 📖 Booked it ]  [ 🚫 Skip ]  [ 🔕 Pause ]
```

### After admin taps `[📖 Booked it]`

```
🏌️ BOOKED ✅

Roy Kizer · Sat May 23
8:00 AM · 4 players · 18 holes

👥 Final roster:
✅ Yes: Colby, Steve
❌ No:  Ed

Booked by Colby at 2:14 PM
Notifications paused through May 23.

[ ↩️ Undo ]
```

### After horizon passes (auto-archived)

```
⌛ Expired — Tee Time was Sat May 23, 8:00 AM
```

### `/status` response

```
📡 Watching: Roy Kizer, Jimmy Clay, Lions, Hancock, Riverside, Morris Williams
🗓  Horizon: May 17 → May 23 (7 days)
🎯 Days: Sat, Sun
⏰ Ideal: 7:30–8:00 AM · Acceptable: 7:00–9:00 AM
🔁 Last poll: 14 min ago · Next: in 46 min
🎟  Booked: — (none)
🚫 Out today: —
🔔 Notifications: ON
```

---

## Telegram commands

| Command | Who | Effect |
|---|---|---|
| `/status` | anyone | Show current watch state |
| `/pause` | admin | Mute all notifications until `/resume` |
| `/resume` | admin | Re-enable notifications |
| `/unbook` | admin | Roll back the most recent booking lock |
| `/courses` | anyone | List watched courses |
| `/garmin` | admin | Run the sibling **garmin-golf** update/deploy script and relay its one-line summary back to the group |
| `/whoami` | anyone (DM) | Reply with the user's Telegram numeric ID (used once per member to populate `config.yaml`) |

Per-notification buttons handle Yes/No/Maybe/Booked/Skip/Pause.
No need for `/yes`/`/no`/`/book` text commands.

### `/garmin` — external update hook

A convenience hook unrelated to tee-time watching: it shells out to the
[`garmin-golf`](../garmin-golf) project's `update.sh` (sync new rounds → AI
coach → rebuild + deploy the golf dashboard) and echoes that script's final
stdout line (a Telegram-friendly summary) back to the group.

- **Admin-only**, since it triggers a deploy/push.
- Runs off the bot's event loop (`asyncio.create_subprocess_exec`) with a
  10-minute timeout; a placeholder message is edited in place with the result.
- Script location: `GARMIN_UPDATE_SCRIPT` env var if set (absolute path),
  else the default sibling path `../garmin-golf/update.sh` resolved relative to
  this repo (so it's independent of the host user's home dir).
- Assumes a home/residential host — `update.sh` notes Garmin rate-limits or
  blocks datacenter IPs and needs a valid token cache + SSH key for the push.

---

## Onboarding members

One-time, manual:

1. Steve DMs the bot: `/whoami`
2. Bot replies: `Your Telegram ID is 87654321.`
3. Steve sends that to Colby.
4. Colby pastes it into `config.yaml` under `group.members`.
5. Restart bot.

---

## Config schema

`config.yaml` lives at the repo root. Secrets (bot token, chat ID) are pulled
from environment variables referenced by name.

```yaml
timezone: America/Chicago

search:
  horizon_days: 7
  start_offset_days: 1
  days_of_week: [saturday, sunday]
  holes: 18
  default_players: 3
  expanded_players: 2   # used when exactly one member is marked out for a date

time_windows:
  ideal:      { start: "07:30", end: "08:00" }
  acceptable: { start: "07:00", end: "09:00" }

courses:
  - { key: roy_kizer,       display: "Roy Kizer",       tier: 1, webtrac_code: TBD }
  - { key: jimmy_clay,      display: "Jimmy Clay",      tier: 1, webtrac_code: TBD }
  - { key: lions,           display: "Lions",           tier: 2, webtrac_code: TBD }
  - { key: hancock,         display: "Hancock",         tier: 2, webtrac_code: TBD }
  - { key: riverside,       display: "Riverside",       tier: 2, webtrac_code: TBD }
  - { key: morris_williams, display: "Morris Williams", tier: 2, webtrac_code: TBD }

grading:
  notify_min_grade: B   # one of: A, B, C

polling:
  default_interval_minutes: 60
  jitter_minutes: 5
  hammer_windows: []    # populated once release times are known

group:
  admin: colby
  members:
    - { name: Colby, telegram_user_id: 0 }   # fill via /whoami
    - { name: Steve, telegram_user_id: 0 }
    - { name: Ed,    telegram_user_id: 0 }

telegram:
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env:   TELEGRAM_CHAT_ID
```

---

## Data model (flat files)

Three files, all in `./data/` (gitignored):

### `state.json` — live state, rewritten atomically on each change

```json
{
  "paused": false,
  "pause_started_at": null,
  "last_poll_at": "2026-05-15T18:00:00-05:00",
  "horizon_override_until": null,
  "tee_times": [
    {
      "id": "roy_kizer:2026-05-23:0800:4",
      "course_key": "roy_kizer",
      "tee_date": "2026-05-23",
      "tee_time": "08:00",
      "players_open": 4,
      "holes": 18,
      "grade": "A",
      "booking_url": "https://txaustinweb.myvscloud.com/...",
      "first_seen_at": "2026-05-15T17:00:00-05:00",
      "last_seen_at": "2026-05-15T18:00:00-05:00",
      "status": "open",
      "message_id": 4421,
      "votes": {
        "Colby": { "vote": "yes", "voted_at": "2026-05-15T17:02:00-05:00" },
        "Steve": { "vote": "yes", "voted_at": "2026-05-15T17:05:00-05:00" }
      }
    }
  ]
}
```

The `id` is a deterministic composite (`course:date:time:players`) so the
same physical slot is the same record across polls — that's how dedup works.

When a tee-time's status becomes `booked`, `skipped`, or `expired`, it stays
in `state.json` until its date passes, then gets pruned (or moved to history
if it was booked).

### `bookings.jsonl` — append-only history

One JSON object per line, written when `[📖 Booked it]` fires.

```json
{"booked_at": "2026-05-15T17:14:00-05:00", "booked_by": "Colby", "course_key": "roy_kizer", "tee_date": "2026-05-23", "tee_time": "08:00", "players": 4, "roster": {"yes": ["Colby", "Steve"], "no": ["Ed"]}, "undone_at": null}
```

When `/unbook` or `[↩️ Undo]` fires, we append a corresponding entry with
`"undone_at"` set on the *new* line (we don't mutate prior lines — easier
to audit).

### `golfbot.log` — rotating text log

Standard `logging.handlers.RotatingFileHandler`. Captures poll attempts,
errors, scraper anomalies. This is where poll history lives instead of a
structured table — grep is enough.

### Derived state

- **"Member out for date D"** is computed from `state.json`: any tee-time
  with `tee_date == D` that has a `no` vote from that member.
- **Active booking** is the most recent line in `bookings.jsonl` whose
  tee_date is in the future and `undone_at` is null.

### Concurrency

Single-process. All writes go through an `asyncio.Lock` and write to a temp
file + `os.replace` for atomicity.

---

## Phasing

Build the **interaction surface first** with mock data, then plug in real
scraping, then move from CLI-triggered to scheduled.

### P1 — Telegram bot + mock data injector

Goal: full UX validated end-to-end with zero scraping.

- `python-telegram-bot` long-polling bot, group-chat wired up.
- `/whoami` command for member onboarding.
- CLI command for injecting fake matches, e.g.:
  ```
  golfbot mock --course roy_kizer --date 2026-05-23 --time 08:00 \
               --players 4 --grade A
  ```
  pushes one synthetic tee-time through the full pipeline.
- Notification rendering with `[✅ Yes] [❌ No] [📖 Booked it] [🚫 Skip] [🔕 Pause]`.
- Vote tally edits the message in place.
- `[📖 Booked it]` (admin) advances state → message switches to BOOKED form,
  horizon override stored, notifications suppressed through booked date.
- `[↩️ Undo]` reverts a booking.
- Commands: `/status`, `/pause`, `/resume`, `/unbook`, `/courses`.
- Auto-archive notifications whose tee date has passed.
- Flat-file persistence (`state.json`, `bookings.jsonl`).

Exit criteria: you and Steve and Ed can vote on a fake tee time, admin can
book it, `/status` reflects the state shift, and `/unbook` rolls it back.
No scraper exists yet.

### P2 — WebTrac scraper

Goal: replace the mock data source with real GolfATX data.

- Manual recon of the WebTrac search page (answers the "Known unknowns"
  section below).
- `httpx` + `bs4` scraper module returning a list of `TeeTimeSlot` objects
  given (course, date, players).
- Grading and filter pipeline applied to scraper output.
- Dedup against `state.json` so a slot isn't re-notified across polls.
- Same CLI entry point shifted from `mock` to `scrape` for one-shot manual
  runs:
  ```
  golfbot scrape --date 2026-05-23
  ```

Exit criteria: running the scrape command produces real notifications for
real slots, dedup works across repeated runs.

### P3 — Scheduled polling

Goal: the bot runs unattended.

- `APScheduler` job firing at the configured cadence (default hourly with jitter).
- 7-day rolling horizon driven by `today + start_offset_days`.
- Horizon override honored after a booking.
- 2-player expansion when one member is marked out for a date (derived
  from `❌ No` votes).
- Single `golfbot run` entry point that starts both the scheduler and the
  Telegram listener and runs until killed.

Exit criteria: launch once, walk away, get notifications for real matches.

### P4 — Operational polish

- Per-course hammer windows (burst polling around known release times,
  once we learn the schedule).
- Health-check: if N consecutive polls fail, send an admin DM.
- `launchd` plist for Mac mini auto-start.
- Optional Dockerfile for portability to Pi/EC2.

### P5+ — Future possibilities

- Grey Rock, Harvey Penick (different booking systems → separate scrapers).
- Cancellation monitoring.
- Calendar integration (e.g. Google Calendar event on booking).
- Weather signal in the notification.
- Direct WebTrac XHR endpoints if a cleaner API surface is discovered.

---

## Known unknowns (V1 prerequisites)

These need to be answered before V1 implementation. All resolvable via 30 min
of manual recon on the WebTrac site.

1. **WebTrac course codes** — what value of `multiselectlist_value` (or
   equivalent) corresponds to each of the 6 courses.
2. **CSRF token flow** — does each search request need a fresh token, or does
   the token persist across requests in the same session?
3. **HTML structure of results** — table rows? what fields are exposed
   (time, players, price, cart vs walking)?
4. **Booking URL format** — is the `[Open booking page]` link deep-linkable
   to a specific slot, or just the search page?
5. **Release schedule** — when do new tee times appear (e.g. 7 days ahead at
   midnight)? Needed for V4 hammer windows.

---

## Operational notes

- **Hosting:** Mac mini initially. APScheduler keeps the process alive; user
  starts it manually or via a `launchd` plist.
- **Logs:** `golfbot.log` rotating file, plus `poll_log` table for structured
  poll history.
- **Time:** all stored times are UTC; display times converted to
  America/Chicago.
- **Secrets:** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` (loaded
  via `python-dotenv`), never in `config.yaml`.

---

## Repo layout (proposed)

```
golfbot/
├── SPEC.md
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── config.yaml
├── .env.example
├── .gitignore               # ignores /data/, /.env
├── data/                    # created at runtime
│   ├── state.json
│   ├── bookings.jsonl
│   └── golfbot.log
├── golfbot/
│   ├── __init__.py
│   ├── __main__.py          # CLI entry: run | mock | scrape
│   ├── config.py            # pydantic models, YAML loader
│   ├── store.py             # state.json / bookings.jsonl read+write (atomic)
│   ├── models.py            # TeeTimeSlot, Vote, Booking dataclasses
│   ├── grading.py           # apply tier × time grading
│   ├── horizon.py           # 7-day window + post-booking override
│   ├── scraper/
│   │   ├── __init__.py
│   │   └── webtrac.py       # httpx + bs4 GolfATX scraper       (added in P2)
│   ├── mock_source.py       # synthetic tee-time injector       (P1)
│   ├── poller.py            # APScheduler job wiring            (added in P3)
│   ├── notifier.py          # Telegram send + in-place edit
│   └── bot.py               # python-telegram-bot handlers (commands + callbacks)
└── tests/
    ├── fixtures/            # saved WebTrac HTML responses (P2+)
    └── ...
```

Files marked `(P2)` / `(P3)` don't exist in earlier phases — the structure
grows with the phasing.
