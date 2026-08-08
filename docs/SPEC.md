# Austin Golf Tee Time Watcher — Spec (v2: Gold Star)

A lightweight, self-hosted bot that scans Austin golf tee times and pings a
small Telegram group **only when a genuinely great slot opens** — one of our
all-star courses, at a premium time, on a weekday. It stays silent otherwise.
The group coordinates and books offline; the bot is a pure scanner.

> **v2 pivot.** This spec was rewritten for the Gold Star pivot — see
> [ADR 0006](decisions/0006-gold-star-pivot.md). golfbot used to carry in-bot
> voting, per-member availability, and booking tracking; months of production
> use showed the group never used them. v2 deletes all of that. If you're
> reading old references to `✅ Yes`/`❌ No` votes, `/out`/`/in`/`/avail`, or
> `📖 Booked it`, they are **gone** — resolved by ADR 0006, which supersedes
> ADRs 0003 and 0004.

Build/run status lives in [README.md](../README.md#status) — the single source
of truth for what phase is done. This spec describes *intended behavior*, not
progress.

## Design philosophy

- **Silent by default.** No alert unless it clears the Gold Star bar. A scanner
  that cries wolf gets muted.
- **Notify, don't book.** Avoids ToS friction. Booking happens offline.
- **Tiny audience.** 3 fixed members, admin-led. No auth beyond a bot token.
- **Vibe app, not enterprise.** America/Chicago hard-coded, single config file,
  flat-file state.
- **Have a personality.** The group are self-described degenerate golf addicts.
  Alerts are snarky (see [Snark](#snark)).
- **Polite scraping.** ~hourly polling within an active window, real-browser
  User-Agent, jitter.

---

## The Gold Star rule

A scanned slot fires a **Gold Star alert** if and only if **all** of these hold:

| Condition | Value |
|---|---|
| Course | in the **all-star set** (see below) |
| Tee time | within the single global **premium window** (config) |
| Day | weekday, Mon–Fri |
| Open spots | **≥ 1** |

No grades, no tiers, no player-count matching. The premium window is the entire
quality bar. This replaces the old two-axis grading (retired,
[ADR 0003](decisions/0003-policy-b-best-per-course-date.md)).

### All-star set

Four courses. Only these can trigger an alert:

| Course | Provider |
|---|---|
| Jimmy Clay | GolfATX |
| Roy Kizer | GolfATX |
| Riverside | GolfNow |
| Grey Rock | GolfNow |

### Scan all, alert on four

Every configured course is still scraped each poll so `/full` shows the complete
picture on demand. Only the four all-star courses can fire an alert. Non-all-star
courses (Morris Williams, Lions, Star Ranch, Falconhead, Avery Ranch) appear in
`/full` but never ping.

### Re-alert semantics (dedup)

Slot identity is `course_key:YYYY-MM-DD:HHMM` — **player count is not part of the
ID**, so a slot is one record across polls regardless of how many spots are open.

- **First time** a slot qualifies → alert, remember `last_alerted_spots`.
- Slot seen again with **more** open spots than last alerted → **re-alert** (a
  better opportunity now), update `last_alerted_spots`.
- Slot seen again with **equal or fewer** spots → silent; just refresh
  `last_seen_at` and `spots_open`.
- Slot whose date has passed → pruned from state.

> GolfNow (`Riverside`, `Grey Rock`) reports open spots as a *lower bound*, not
> an exact count. GolfATX (`Jimmy Clay`, `Roy Kizer`) reports the real number.
> The spot count in an alert is exact only for GolfATX; for GolfNow it means
> "at least N."

---

## Notifications

### Gold Star alert (the only push notification)

One message per qualifying slot. Headline is randomized snark; the body is
factual. Mocks are literal — this is exactly what renders:

```
🎯 Book it now, dumbass

Jimmy Clay · Fri May 22
7:40 AM · 3 spots open

🔗 Book it
```

- The first line is drawn at random from the `alerts.headlines` pool (see
  [Snark](#snark)).
- `🔗 Book it` is an inline URL button to the provider's booking page.
- **No vote buttons, no roster, no tally.** The alert carries the signal and the
  link, nothing else.

A re-alert (spots increased) is an identical fresh message with the new count.

### `/full` — the on-demand firehose

`/full` lists **every** available slot from the last scan — all configured
courses, all times, not just the premium window and not just the all-star set.
This is the "let me actually browse everything" escape hatch. No push; only
shown when asked, and it reads the cached scan so it costs no API calls.

Grouped by date, then by course, showing the count and the earliest times.
Qualifying times are prefixed with **⭐**, and a count of current Gold Stars
sits in the header — so `/full` doubles as the way to review what alerted
after the alert messages have scrolled past. Mocks are literal:

```
🏌️ All Slots — 12:30 PM
9 courses · Mon, Tue, Wed, Thu, Fri · 18-hole
⭐ 3 Gold Stars

Mon 5/18 (14)  🌤️ 87°/66° · Rain 12%
  Roy Kizer (5): ⭐7:40, ⭐7:55, 9:10, 11:20, 14:05
  Lions (4): 8:15, 9:30, 13:00, 15:45

Tue 5/19 (9)
  Jimmy Clay (3): ⭐7:30, 10:15, 13:40
```

A ⭐ means the slot clears the Gold Star bar *right now* — it does not mean an
alert was sent for it (a slot already alerted at the same spot count stays
starred but silent).

When no slot qualifies the header line reads `☆ No Gold Stars`. Output is
truncated to fit Telegram's 4096-character limit, dropping whole trailing days
first and saying so.

### Expired

A slot whose tee date has passed is pruned silently. No "expired" message —
there's nothing to act on.

---

## Telegram commands

| Command | Who | Effect |
|---|---|---|
| `/full` | anyone | Full on-demand listing of everything currently open |
| `/scan` | anyone | Force an immediate scan |
| `/status` | anyone | Watch state: all-star set, premium window, last/next scan |
| `/courses` | anyone | List all scanned courses (all-star flagged) |
| `/pause` | admin | Mute all alerts until `/resume` |
| `/resume` | admin | Re-enable alerts |
| `/garmin` | admin | Run the sibling **garmin-golf** update script, relay its summary |
| `/whoami` | anyone (DM) | Reply with the user's Telegram numeric ID (onboarding) |

**Removed in v2:** `/out`, `/in`, `/avail` (availability layer), `/unbook`
(booking tracking), `/tee` (folded into `/full`). All per-slot inline buttons
except the booking link are gone.

### `/garmin` — external update hook

Unchanged from v1. A convenience hook unrelated to tee-time watching: shells out
to the [`garmin-golf`](../../garmin-golf) `update.sh` (sync rounds → AI coach →
rebuild + deploy the golf dashboard) and echoes its final stdout line back to the
group. Admin-only (it triggers a deploy). Runs off the event loop via
`asyncio.create_subprocess_exec` with a 10-minute timeout; a placeholder message
is edited in place with the result. Script path: `GARMIN_UPDATE_SCRIPT` env var
if set, else the sibling `../garmin-golf/update.sh`.

---

## Snark

Alert headlines come from a randomized pool. Requirements:

- Pick uniformly at random from `alerts.headlines`, but **never repeat the
  immediately previous headline** (track last-used in memory; re-roll on match).
- A headline may contain the token `{name}`, replaced with a random roster member
  from `group.members` — e.g. `"Quick — before {name} sees it"` → `"Quick —
  before Steve sees it"`.
- The pool lives in `config.yaml` so copy can change without touching code. If
  the pool is empty or missing, fall back to a single built-in default.

Starter pool (shipped in `config.yaml`):

```
Book it now, dumbass ⛳
You know you want it
Go scratch that itch
Stop reading. Start booking.
This slot won't book itself, degenerate
Quick — before {name} sees it
Beat {name} to it
Call in sick. This is why PTO exists.
Cancel your morning, you've got a tee time to steal
Golf > responsibilities
Your therapist said get a hobby. This counts.
Sneak out. We won't tell.
Premium time, prime excuse to skip work
Fresh cancellation just dropped 🎯
Somebody chickened out — their loss, your gain
Beat the heat, beat your buddies
Another day, another chance to wreck your handicap
A wild premium slot appears!
The course is calling and you must go
One does not simply ignore a 7:40 tee time
```

---

## Search behavior

- **Horizon:** rolling window, `today + start_offset_days` through
  `+ horizon_days`. Default offset 1 (tomorrow onward).
- **Days of week:** weekdays (Mon–Fri). Weekends off by default.
- **Holes:** 18.
- **No booking override.** v1 advanced the horizon and suppressed a booked date;
  with booking tracking removed, the horizon is a plain rolling window.
- **Active window:** scheduled scans only fire during `polling.active_window`
  (local time). `/scan` works any time.

---

## Config schema (v2)

`config.yaml` at the repo root. Secrets (bot token, chat ID) come from named env
vars.

```yaml
timezone: America/Chicago

search:
  horizon_days: 7
  start_offset_days: 1
  days_of_week: [monday, tuesday, wednesday, thursday, friday]
  holes: 18

# The single quality bar. A slot in this window (weekday, all-star course,
# ≥1 spot) is a Gold Star. Replaces v1's ideal/acceptable two-tier.
premium_window: { start: "07:20", end: "08:00" }

courses:
  # all_star: true → can fire an alert. All courses are scanned regardless
  # (so /full is complete); only all-star courses ping.
  - { key: jimmy_clay,            display: "Jimmy Clay",   provider: golfatx, provider_id: 1, all_star: true }
  - { key: roy_kizer,             display: "Roy Kizer",    provider: golfatx, provider_id: 2, all_star: true }
  - { key: riverside,             display: "Riverside",    provider: golfnow, provider_id: 888, all_star: true }
  - { key: grey_rock_golf_club,   display: "Grey Rock",    provider: golfnow, provider_id: 166, all_star: true }
  - { key: morris_williams,       display: "Morris Williams", provider: golfatx, provider_id: 3 }
  - { key: lions,                 display: "Lions",        provider: golfatx, provider_id: 4 }
  - { key: golf-club-star-ranch,  display: "Star Ranch",   provider: golfnow, provider_id: 151 }
  - { key: falconhead_golf_club,  display: "Falconhead",   provider: golfnow, provider_id: 321 }
  - { key: avery_ranch_golf_club, display: "Avery Ranch",  provider: golfnow, provider_id: 206 }

alerts:
  headlines:
    - "Book it now, dumbass ⛳"
    - "You know you want it"
    - "Go scratch that itch"
    - "Stop reading. Start booking."
    - "This slot won't book itself, degenerate"
    - "Quick — before {name} sees it"
    - "Beat {name} to it"
    - "Call in sick. This is why PTO exists."
    - "Cancel your morning, you've got a tee time to steal"
    - "Golf > responsibilities"
    - "Your therapist said get a hobby. This counts."
    - "Sneak out. We won't tell."
    - "Premium time, prime excuse to skip work"
    - "Fresh cancellation just dropped 🎯"
    - "Somebody chickened out — their loss, your gain"
    - "Beat the heat, beat your buddies"
    - "Another day, another chance to wreck your handicap"
    - "A wild premium slot appears!"
    - "The course is calling and you must go"
    - "One does not simply ignore a 7:40 tee time"

polling:
  default_interval_minutes: 60
  jitter_minutes: 5
  hammer_windows: []
  active_window: { start: "08:00", end: "20:00" }

group:
  admin: Colby
  members:
    - { name: Colby, telegram_user_id: 0 }   # fill via /whoami
    - { name: Steve, telegram_user_id: 0 }
    - { name: Ed,    telegram_user_id: 0 }

telegram:
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env:   TELEGRAM_CHAT_ID

# Optional daily forecast via Open-Meteo (no API key; cached in state.json).
weather:
  enabled: true
  latitude: 30.26715
  longitude: -97.74306
  cache_hours: 6
```

**Removed keys vs v1:** `time_windows` (→ `premium_window`), `grading`,
`search.default_players`, `search.expanded_players`, course `tier`.

---

## Data model (flat files)

State lives in `./data/` (gitignored, not backed up).

### `state.json` — live state, rewritten atomically

```json
{
  "paused": false,
  "pause_started_at": null,
  "last_poll_at": "2026-05-15T18:00:00-05:00",
  "tee_times": [
    {
      "id": "jimmy_clay:2026-05-22:0740",
      "course_key": "jimmy_clay",
      "tee_date": "2026-05-22",
      "tee_time": "07:40",
      "spots_open": 3,
      "holes": 18,
      "booking_url": "https://...",
      "first_seen_at": "2026-05-15T17:00:00-05:00",
      "last_seen_at": "2026-05-15T18:00:00-05:00",
      "last_alerted_spots": 3,
      "last_alerted_at": "2026-05-15T17:00:00-05:00"
    }
  ],
  "raw_slots": [],
  "weather": {}
}
```

- `tee_times` is the **dedup + re-alert ledger** for all-star qualifying slots.
  `last_alerted_spots` drives the "re-alert on increase" rule.
- `raw_slots` caches the last full scan (all courses, all times) so `/full` is
  free — no re-scrape.
- `weather` caches the Open-Meteo forecast.

**Gone from v1:** `votes` (per-slot), `bookings` (keyed by date),
`availability`, `horizon_override_until`, per-slot `message_id`/`status`.

### `bookings.jsonl`

**No longer written.** Booking is offline. The file is left on disk as history
if it exists; nothing appends to it.

### `golfbot.log`

Rotating text log. Poll attempts, errors, scraper anomalies. Poll history lives
here — grep is enough.

### Concurrency

Single process. All writes go through an `asyncio.Lock` + temp-file `os.replace`
for atomicity. Never run two `golfbot run` processes against one `data/`.

---

## Tech stack

- **Python 3.12+** (`from __future__ import annotations` everywhere)
- **`python-telegram-bot`** ≥21, long-polling
- **APScheduler** — interval + jitter, gated by `active_window`
- **`httpx`** + **`curl_cffi`** (curl_cffi only for WebTrac/Cloudflare —
  [ADR 0002](decisions/0002-curl-cffi-for-cloudflare.md))
- **BeautifulSoup + lxml** — WebTrac HTML parsing
- **pydantic v2 + PyYAML** — config validation
- **Flat JSON files** — no database
  ([ADR 0001](decisions/0001-flat-files-over-database.md))

---

## Providers

One module per booking system under `golfbot/providers/`. Each returns a
normalized `RawSlot` (course_key, date, time, spots, holes, booking_url,
provider, optional price) from `fetch_slots(courses, target_date, min_players)`.

- **GolfATX** (`golfatx.py`) — City of Austin WebTrac. Two-step: GET the search
  form to harvest a CSRF token, then GET search results. One search returns all
  Austin courses; filter client-side. **Exact** spot counts. A course missing
  from `WEBTRAC_NAME_BY_CODE` silently returns zero slots (most common "why is
  this course empty?" cause).
- **GolfNow** (`golfnow.py`) — POST to `/api/tee-times/tee-time-search-results`.
  Per-slot times carry a misleading `+00:00` offset; parse from `time.formatted`
  + meridian instead. Spot count is a **lower bound**, not exact.

---

## Weather

Optional daily forecast via Open-Meteo (no API key). Cached in `state.json`,
refreshed at most every `cache_hours`. Shown in the `/full` listing header when
enabled. Disable by setting `weather.enabled: false` or removing the block.

---

## Onboarding members

One-time, manual:

1. Member DMs the bot: `/whoami`
2. Bot replies: `Your Telegram ID is 87654321.`
3. Member sends that to Colby.
4. Colby pastes it into `config.yaml` under `group.members`.
5. Restart bot.

---

## Implementation slices (v2 refactor)

The pivot is mostly **subtractive** and ships in thin, independently-testable
slices. Each leaves the bot runnable.

1. **Config v2** — `premium_window` + `all_star` schema; drop `grading`,
   `time_windows`, player/tier keys; add `alerts.headlines`. Migrate
   `config.yaml`, update `config.py` validation. *Done when:* config tests pass
   on the new schema.
2. **Qualification rewrite** — replace `grading.py` + `pipeline.filter_and_grade`
   with the Gold Star rule; drop player-count filtering to `≥1`; scan-all /
   alert-on-four. *Done when:* pipeline tests assert only all-star + premium-window
   weekday slots qualify.
3. **Strip Model A + availability** — delete voting callbacks, tally rendering,
   `📖 Booked it`, `/unbook`, `models.Booking`, `bookings.jsonl` writes,
   `availability.py`, `/out` `/in` `/avail`. Simplify slot ID to
   `course:date:time` + `last_alerted_spots`. *Done when:* bot starts clean, no
   dead imports, no vote/availability surface.
4. **New alert + snark + `/full`** — rewrite `notifier.render_open` to the Gold
   Star format; add `pick_headline()` (random, no-repeat, `{name}` substitution);
   confirm `/full` shows the firehose. *Done when:* a mock winner renders the new
   text with a random headline; `/full` lists everything; headline logic is
   unit-tested with a seeded RNG.
5. **Docs + cleanup** — reconcile `docs/ARCHITECTURE.md` with the removals, bump
   README status, delete now-dead tests. *Done when:* docs match shipped
   behavior and `ruff`/`pytest` are green.

---

## Removed in v2 (explicit)

Deleted, not deprecated — see [ADR 0006](decisions/0006-gold-star-pivot.md):

- Per-slot voting (`✅ Yes` / `❌ No`), vote tally, `models.Vote`
- In-bot booking: `📖 Booked it`, `/unbook`, `models.Booking`,
  `bookings.jsonl` writes, post-booking horizon suppression
- Availability layer: `availability.py`, `/out`, `/in`, `/avail`,
  player-count-driven search expansion
- Grading: `grading.py`, A/B/C grades, course tiers, ideal/acceptable windows
- `/tee` (folded into `/full`)
