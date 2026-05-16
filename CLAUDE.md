# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**golfbot** — a self-hosted Telegram bot that monitors Austin municipal golf
tee times (GolfATX / WebTrac) and notifies a small fixed group when desirable
slots become available. The group votes inline; the admin books manually
outside the bot.

The full design — goals, scope, mocks, data model, phasing, known unknowns —
lives in [SPEC.md](SPEC.md). **Read SPEC.md before making non-trivial changes.**

## Status

**P1 (in progress)** — Telegram bot harness + mock-data injector. Module
stubs exist; implementations are still empty (`raise NotImplementedError`).

Phases (per SPEC.md > Phasing):

- **P1** — Telegram UX with mock injector. No scraping.
- **P2** — WebTrac scraper replaces mock source.
- **P3** — APScheduler runs the poller on a cadence.
- **P4** — Operational polish (hammer windows, health checks).

## Development commands

```bash
# Setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

# Run
golfbot run                       # start Telegram listener (not implemented yet)
golfbot mock --course roy_kizer --date 2026-05-23 --time 08:00 --players 4 --grade A

# Tests / lint
pytest
ruff check .
```

## Architecture

Single-process Python app. Two long-running asyncio tasks share flat-file state:

1. **Telegram listener** (`bot.py`) — `python-telegram-bot` long-polling.
   Handles slash commands and inline-button callback queries.
2. **Poller** (`poller.py`, P3+) — APScheduler-driven scrape jobs.

State persistence is **flat files**, not a database:
- `data/state.json` — live state (open slots, votes, pause flag, booking lock).
  Atomic write via temp file + `os.replace` under an `asyncio.Lock`.
- `data/bookings.jsonl` — append-only booking history.
- `data/golfbot.log` — rotating text log (poll history lives here).

## Conventions

- Times stored as ISO 8601 with `America/Chicago` offset; displayed in CT.
- Tee-time IDs are deterministic: `{course_key}:{YYYY-MM-DD}:{HHMM}:{players}`.
  Same physical slot → same id across polls → dedup is trivial.
- Admin-only actions (`/pause`, `/resume`, `/unbook`, `[📖 Booked it]`) gate
  on `config.group.admin`. Non-roster taps are silently ignored.
- A `❌ No` vote means "out for the entire day" (not just this slot). It
  triggers 2-player search expansion for that date.

## Adding/changing functionality

If a change affects the user-visible Telegram surface (message text,
buttons, commands) or the data model, update SPEC.md *first*, then implement.
The spec is the source of truth for UX; code follows it.
