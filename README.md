# golfbot

Austin golf tee-time watcher. Polls public booking systems (GolfNow API,
GolfATX/WebTrac scraper) and pings a small Telegram group **only when an
all-star course opens at a premium weekday time** (a "Gold Star"). Pure
scanner — the group coordinates and books offline.

See [docs/SPEC.md](docs/SPEC.md) for the full design, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the code is wired.

## Status

- **v2 Gold Star** ✅ shipped — scanner-only. Voting, per-member availability,
  in-bot booking and A/B/C grading are gone
  ([ADR 0006](docs/decisions/0006-gold-star-pivot.md)).
- Providers: GolfNow ✅ · GolfATX/WebTrac ✅ (via `curl_cffi` for Cloudflare)
- Scheduled scans ✅ · Gold Star alerts ✅ · `/full` firehose ✅

## The Gold Star rule

You get pinged **only** when all four hold:

| Condition | Value |
|---|---|
| Course | in the all-star set — Jimmy Clay, Roy Kizer, Riverside, Grey Rock |
| Tee time | inside `premium_window` (default 07:20–08:00) |
| Day | weekday, Mon–Fri |
| Open spots | ≥ 1 |

Every configured course is still scanned so `/full` shows everything; only the
four all-stars can fire an alert. A slot re-alerts only if it reappears with
**more** open spots than last announced — otherwise it stays silent.

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

### Run the bot

```bash
# APScheduler runs scans on a cadence + Telegram listens for commands.
golfbot run

# On any Mac, wrap with `caffeinate -i` so macOS doesn't sleep the event
# loop mid-schedule:
caffeinate -i .venv/bin/golfbot run
```

**About `caffeinate`** (macOS): a built-in that holds a sleep assertion while
the wrapped process runs. `-i` prevents *idle sleep*; Ctrl-C releases it.

Use it on a laptop **and** on a Mac mini. Even where the OS is configured never
to sleep, wrapping the bot keeps the assertion process-scoped, so a reset pmset
config (OS update, factory reset) can't silently stop scheduled scans. Zero CPU
cost, no downside.

Lid-close sleep on a MacBook is *separate* from idle sleep and overrides `-i`.
To keep running with the lid shut, be on AC power and use `caffeinate -is`.

### Commands

| Command | Who | Effect |
|---|---|---|
| `/full` | anyone | Every open slot in horizon — all courses, all times, with ⭐ on current Gold Stars |
| `/scan` | anyone | Force a scan right now |
| `/status` | anyone | All-star set, premium window, last/next scan |
| `/courses` | anyone | All scanned courses (⭐ = can alert) |
| `/pause` | admin | Mute alerts |
| `/resume` | admin | Unmute |
| `/garmin` | admin | Run the sibling garmin-golf update script |
| `/whoami` | anyone | Your Telegram user ID (for onboarding) |
| `/help` | anyone | Command list |

The **only** button anywhere is the `🔗 Book it` link on an alert. There are no
vote, confirm or cancel buttons — booking happens offline.

### Preview a scan from the terminal

`scrape` applies the same Gold Star rule the scheduler uses, then prints what
*would* alert. No Telegram, no state written.

```bash
golfbot scrape                              # full horizon, every course
golfbot scrape --course roy_kizer           # scope to one course
golfbot scrape --date 2026-05-21            # scope to one date
golfbot scrape --raw                        # unfiltered — skip the rule entirely
golfbot scrape --players 2                  # min open spots to query with
golfbot scrape --help
```

Use `--raw` first when a course looks empty: if raw returns rows but the
filtered run doesn't, the rule is doing its job (not all-star, outside the
window, or a weekend) — that's not a bug.

### Send a test alert

```bash
golfbot mock --course roy_kizer --date 2026-05-22 --time 07:40 --spots 3
```

Sidesteps the providers and pushes one synthetic Gold Star to the group, so you
can check the alert renders without waiting for a real cancellation. **This
posts to the live chat.**

### Tests

```bash
pytest                                          # all
pytest tests/test_pipeline.py -v                # one file
pytest tests/test_pipeline.py::test_name -v     # one test
pytest -k gold_star                             # by name
ruff check .
```

## Layout

| Path | Contents |
|---|---|
| `golfbot/` | Source; `providers/` holds one module per booking system |
| `tests/` | Test suite; `fixtures/` holds recorded provider responses |
| `docs/` | [SPEC](docs/SPEC.md), [ARCHITECTURE](docs/ARCHITECTURE.md), [decisions/](docs/decisions/) |
| `rules/` | Coding standards ([python](rules/python.md), [telegram-ux](rules/telegram-ux.md), [testing](rules/testing.md)) |
| `skills/` | Reusable workflows, also exposed to Claude Code via `.claude/skills` |
| `data/` | Runtime state — gitignored and **not backed up** |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module graph.
