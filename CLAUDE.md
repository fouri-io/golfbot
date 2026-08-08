# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project Overview

**Project:** golfbot
**Purpose:** Get three friends onto a great Austin tee time without anyone
refreshing a booking site. The bot watches availability and pings Telegram
**only** for a Gold Star — an all-star course, at a premium time, on a weekday.
It stays silent otherwise.
**Owner:** Colby ([@fouri-io](https://github.com/fouri-io))
**Status:** See [README.md](README.md#status) — the single source of truth for
what's shipped. Do not restate status here; that's how this file went stale
before.

It is a personal convenience tool for a fixed group of three, self-hosted, and
has been running in production for several months. It is not a product and has
no users beyond the group.

golfbot is a **pure scanner**. It notifies; it never books. The group
coordinates and books offline. v2 deliberately deleted in-bot voting,
per-member availability, booking tracking and A/B/C grading — months of
production use showed the group never used them
([ADR 0006](docs/decisions/0006-gold-star-pivot.md)).

## Architecture & Structure

```
golfbot/
├── CLAUDE.md              ← you are here
├── README.md              ← human quickstart + status
├── config.yaml            ← courses, windows, roster, polling (committed)
├── .env                   ← secrets (gitignored, never read or edit)
├── .claude/
│   ├── settings.local.json  (gitignored)
│   └── skills -> ../skills  (symlink, so skills are both visible and invocable)
├── docs/
│   ├── SPEC.md            ← source of truth for UX + data model
│   ├── ARCHITECTURE.md    ← as-built module graph and data flow
│   └── decisions/         ← ADRs: why it's built this way
├── rules/                 ← standards: python, telegram-ux, testing
├── skills/                ← reusable workflows (committed, discoverable)
├── golfbot/               ← source
│   └── providers/         ← one module per booking system
├── tests/
│   └── fixtures/          ← recorded provider responses; tests never hit live
└── data/                  ← runtime state (gitignored, NOT backed up)
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before any non-trivial
change — it's the only place the module graph is written down.

## Key Commands

```bash
# Setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then fill TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

# Run
golfbot run                   # Telegram listener + scheduled scans
caffeinate -i .venv/bin/golfbot run    # on macOS — see Known Quirks

# Preview a scan without Telegram or persistence
golfbot scrape
golfbot scrape --course roy_kizer --date 2026-05-21
golfbot scrape --raw          # skip the rule — is the provider returning anything?

# Send one synthetic Gold Star (POSTS TO THE LIVE CHAT)
golfbot mock --course roy_kizer --date 2026-05-22 --time 07:40 --spots 3

# Test
pytest                                          # all
pytest tests/test_pipeline.py -v                # one file
pytest tests/test_pipeline.py::test_name -v     # one test
pytest -k gold_star                             # by name

# Lint
ruff check .
ruff check . --fix
```

There is **no CI and no formatter configured yet** — both are planned. Until
then, `ruff check .` and `pytest` before handing work back is manual and
mandatory.

## Tech Stack & Conventions

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | `from __future__ import annotations` everywhere |
| Bot | `python-telegram-bot` ≥21 | Long-polling, not webhooks |
| Scheduling | APScheduler | Interval + jitter, gated by `active_window` |
| Config | pydantic v2 + PyYAML | `config.yaml` → validated `Config` |
| HTTP | `httpx`, `curl_cffi` | curl_cffi only for Cloudflare — [ADR 0002](docs/decisions/0002-curl-cffi-for-cloudflare.md) |
| Parsing | BeautifulSoup + lxml | WebTrac HTML |
| Storage | Flat JSON files | No database — [ADR 0001](docs/decisions/0001-flat-files-over-database.md) |
| Lint | ruff (`E,F,I,B,UP,SIM`, line 100) | Clean — keep it that way |
| Tests | pytest, `asyncio_mode=auto` | All green; no live network anywhere |
| CI | none yet | Planned |

Detailed standards live in [`rules/`](rules/) — read the relevant one before
writing code:

| File | Covers |
|---|---|
| [`rules/python.md`](rules/python.md) | Typing, purity boundaries, docstrings, known debt |
| [`rules/telegram-ux.md`](rules/telegram-ux.md) | Spec-first rule, ID/time conventions, noise discipline |
| [`rules/testing.md`](rules/testing.md) | No live network, no real `data/`, frozen time |

## Skill Routing

Skills are the primary interface. When a task matches one, invoke it.

| Task | Skill |
|---|---|
| Add a course, or support a new booking provider | `/add-course` |
| Change any Telegram message, button, command, or the data model | `/spec-sync` — **before** writing code |

## Knowledge Architecture

| Folder | Owner | Purpose |
|---|---|---|
| `docs/SPEC.md` | Both | Intended UX + data model. Source of truth for behavior. |
| `docs/ARCHITECTURE.md` | Claude | As-built module graph. Keep current with structural change. |
| `docs/decisions/` | Both | ADRs — the *why*. Append, don't rewrite history. |
| `rules/` | Both | Standards Claude must follow. |
| `skills/` | Both | Reusable workflows. |

SPEC says *what the bot does*; ADRs say *why it's built that way*; ARCHITECTURE
says *where the code is*. When two disagree, that's a bug — report it rather
than picking one.

## Working Rules

### Role routing

Identify the mode before starting:

| Mode | When | Behavior |
|---|---|---|
| Planning | New feature, unclear scope | Interview, write spec, propose checkpoints |
| Building | Clear spec exists | Execute, checkpoint at defined points |
| Reviewing | Output exists | Evaluate against criteria, flag issues |
| Debugging | Something broke | Reproduce → hypothesize → fix → verify |

### Task execution pattern

1. **Tight scope** — one clear unit of work
2. **Clear checkpoint** — define "done" before starting
3. **Review output** — owner verifies at each checkpoint
4. **Adjust / repeat**

Surface key decisions for explicit confirmation rather than deciding silently.

### Guardrails

🟢 **Always do (autopilot)**

- Run `ruff check .` and `pytest` before presenting code
- Write or update tests when changing logic
- Update `docs/SPEC.md` *first* for any user-visible change (`/spec-sync`)
- Running `golfbot scrape` against live providers is fine — keep it at human
  rate, no added parallelism
- Sending real Telegram messages to the group is fine (owner's explicit call)
- Editing `config.yaml` is fine

🟡 **Ask first**

- **Before any commit or push.** Branch for any multi-file change and let the
  owner review the diff first. Never push to `origin/main` unprompted.
- Before deleting files, or deleting anything under `data/` — it's live state
  and is not backed up
- Before adding a **new push notification**. The Gold Star alert is deliberately
  the only one; silence is the feature. Anything else goes behind a command.
- Before re-introducing anything ADR 0006 deleted (voting, availability,
  booking tracking, grading). Those are decisions, not gaps.

🔴 **Never do**

- Never commit secrets — `.env`, bot tokens, chat IDs. Don't read `.env` either;
  `config.py` resolves secrets through named env vars by design.
- Never write to the real `data/state.json` from a test; use `tmp_path`
- Never hit a live provider from a test; use `tests/fixtures/`
- Never run two `golfbot run` processes against the same `data/` — the
  full-document write pattern silently loses data

## Verification Standards

Before presenting any change:

**Code**

- [ ] `pytest` passes (or the failure is pre-existing and named as such)
- [ ] `ruff check .` clean
- [ ] New logic has tests
- [ ] No secrets or environment-specific values hardcoded
- [ ] Scoped to the stated task — no drive-by refactors

**Docs**

- [ ] No placeholder text left behind
- [ ] Claims about behavior verified against the code, not recalled
- [ ] Cross-references resolve (files exist, sections exist)

**Provider / scrape work**

- [ ] Compared row counts before and after a filter change, and explained any drop
- [ ] Spot-checked 3 parsed slots against the raw response
- [ ] Empty results handled as normal, not as an error
- [ ] Boundary times at the exact edge of `premium_window` tested (inclusive both ends)

## Context I Want Claude to Know

**My role:** Serious recreational golfer; this is a weekend project shared with
three golf buddies, not commercial work. I make all the calls on scope and
intent.

**Working style:** Options before commitment. For anything non-trivial, show me
2–3 approaches with a recommendation before writing code.

**Anti-patterns to avoid:**

- **Don't over-explain.** I'm technical. Skip the tutorial framing and the
  recap of what you just did.
- **Show options before committing** to a non-trivial approach.
- Don't restate status or behavior in multiple files — link to the one source.
  That's how this file drifted into being wrong for months.

## Project-Specific Notes

### Known quirks

- **macOS sleep kills the scheduler.** Always run under `caffeinate -i`, on a
  laptop *and* on a Mac mini. Without it, idle sleep silently stops scans.
- **GolfNow lies about time offsets.** Per-slot `time.date` carries `+00:00`
  while the value is actually local Central. Parsing goes through
  `time.formatted` instead.
- **GolfNow `players_available` is a lower bound**, not a count — it's the
  number we queried with. GolfATX exposes the real number. Don't treat them
  the same.
- **`bot.py` and `scanner.py` use function-local imports** to dodge import
  cycles. Existing debt; don't copy the pattern.
- **A GolfATX course missing from `WEBTRAC_NAME_BY_CODE`** returns zero slots
  with only a log warning. This is the most common "why is this course empty?"
  cause.
- **`data/` is gitignored and unbacked.** The Gold Star ledger lives only there;
  wiping it means every currently-open slot re-alerts once.
- **The bot registers zero callback handlers.** v2's only button is a URL
  button. If you add a callback button you must add its handler too — there is
  no fallback handler to catch it.
- **`{name}` in a headline** is substituted at send time from the roster. A
  headline pool entry is config copy, not code.

### Active decisions

Full records in [`docs/decisions/`](docs/decisions/). In force:

- Flat JSON files over a database — single writer, atomic rename
- `curl_cffi` for WebTrac; `httpx` everywhere else
- Gold Star rule: all-star course + premium window + weekday + >=1 spot, and
  re-alert only when open spots increase
- Scan every course, alert on the four all-stars, so `/full` stays complete

Superseded, kept for history — do not build on them: ADR 0003 (grading and
best-per-course-date) and ADR 0004 (availability layer), both retired by
[ADR 0006](docs/decisions/0006-gold-star-pivot.md).

### People

Three-person group: **Colby** (owner/admin, books manually outside the bot),
**Steve**, **Ed**. Admin-only actions gate on `config.group.admin`. Non-roster
taps are silently ignored — no error reply.
