# golfbot

Austin golf tee-time watcher. Polls public booking systems (GolfNow API,
GolfATX/WebTrac scraper) for desirable tee times and notifies a small group
via Telegram. The group votes inline; the admin books manually outside the
bot.

See [SPEC.md](SPEC.md) for the full design.

## Status

- **P1** ✅ Telegram bot harness (commands, callbacks, persistent state)
- **P2a** ✅ GolfNow provider
- **P2b** ✅ GolfATX/WebTrac provider (via `curl_cffi` for Cloudflare bypass)
- **P3** ✅ Scheduled scans + Telegram digest notifications

Next: per-member availability layer + hammer windows around release times.

## Setup

```bash
# Create venv and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Or with uv
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env — TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
# Edit config.yaml — fill in member telegram_user_ids (use /whoami)
```

## Usage

### Scrape (P2 — preview real availability)

The scrape command runs the providers, applies filters (day-of-week,
time-window, grade), applies Policy B dedup (best per course-date), and
prints what *would* fire as a notification.

```bash
# Full preview: every configured course × 7-day horizon
golfbot scrape

# Scope to one course (debugging or sanity check)
golfbot scrape --course riverside
golfbot scrape --course grey_rock_golf_club
golfbot scrape --course roy_kizer

# Scope to one date
golfbot scrape --date 2026-05-21
golfbot scrape --course morris_williams --date 2026-05-19

# See unfiltered API output (skip pipeline filters)
golfbot scrape --raw
golfbot scrape --course morris_williams --date 2026-05-19 --raw

# Override player count (default 3; try 2 for expansion-case preview)
golfbot scrape --players 2

# Full help
golfbot scrape --help
```

Output shows live per-fetch progress, a funnel summary, and the final
matches that would notify.

### Telegram bot — scheduled mode (P3)

```bash
# Start the bot: APScheduler runs scans on a cadence + Telegram listens for
# commands. First scan fires within ~10s, then on the configured interval.
golfbot run

# On a MacBook, wrap with `caffeinate -i` so macOS doesn't sleep the
# event loop mid-schedule:
caffeinate -i .venv/bin/golfbot run
```

**About `caffeinate`** (macOS): a built-in that holds a sleep assertion
while the wrapped process runs. `-i` prevents *idle sleep*. When you
Ctrl-C the bot, the assertion releases. Without it, if the system goes
idle (lid closed counts as idle on MacBook), APScheduler's asyncio loop
pauses and scheduled scans get missed; with `misfire_grace_time=None`
they fire on wake, but you'd rather not miss them in the first place.

For a Mac mini deployment, you typically don't need `caffeinate` — just
set "Prevent automatic sleeping when display is off" in System Settings
→ Battery (or `sudo pmset -a sleep 0`) and run `golfbot run` directly.

Lid-close sleep on a MacBook is *separate* from idle sleep and overrides
`-i`; if you want the bot to keep running with the lid closed, also run
on AC power and use `caffeinate -is` (or change the relevant pmset).

Once running, the bot posts a **digest message** to the group whenever the
match set changes — same shape as `scrape` output, one row per matching
slot with an inline "book" link. Notifications are deduplicated: if a poll
returns the same slots as the previous one, no new message fires.

Commands (DM the bot or send in the group):

| Command | Effect |
|---|---|
| `/tee` | Re-display the most recent scan's matches |
| `/status` | Bot state — horizon, pause flag, last poll |
| `/courses` | List courses being scanned |
| `/avail` | Show next-7-days availability grid for registered members |
| `/out <date> [date ...]` | Mark yourself OUT for one or more dates |
| `/in  <date> [date ...]` | Mark yourself back IN |
| `/pause` | Mute auto-scan notifications (admin) |
| `/resume` | Unmute (admin) |
| `/whoami` | Your Telegram user ID (used for adding to roster) |
| `/help` | Command list |

Date arg formats accepted by `/out` / `/in`: `mon`/`tue`/.../`sun` (next
occurrence, or today if same-day), `today`, `tomorrow`, ISO `2026-05-20`,
or `M/D` like `5/20`.

**Availability behavior:** the scanner consults each member's availability
per date. By default, the **admin** (`group.admin` in config) gating is on
— if the admin is out for a date, the scanner skips it entirely. For dates
the admin is in, the scanner queries providers with `min_players` set to
the count of available registered members, so missing players don't force
us to look for impossible-to-fill foursomes. Set `group.admin_required:
false` in config to disable the admin gate.

Members with `telegram_user_id: 0` in config are considered placeholders
and are not counted in availability calculations — they need to register
via `/whoami` first.

### Telegram bot — synthetic injection (P1 testing)

```bash
# Send a fake tee-time notification (per-slot voting model, separate from digest)
golfbot mock --course roy_kizer --date 2026-05-23 --time 08:00 --players 4 --grade A
```

This sidesteps providers and the digest path — it sends a single per-slot
message with Yes/No vote buttons. Used for testing the group-voting UX
without depending on real availability.

### Tests

```bash
pytest                       # all tests
pytest tests/test_pipeline.py -v
```

## Layout

See SPEC.md > Repo layout. Runtime state lives in `./data/` (gitignored).
