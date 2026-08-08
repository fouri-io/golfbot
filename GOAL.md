# GOAL — golfbot v2 (Gold Star pivot)

This is the target a Claude Code session works toward. Read it, then advance the
refactor one slice at a time. It's self-contained: a fresh session with no prior
context can act on it.

## Source of truth

- **What to build:** [docs/SPEC.md](docs/SPEC.md) — the v2 spec. This is
  authoritative for behavior; the code is a transcription of it.
- **Why:** [docs/decisions/0006-gold-star-pivot.md](docs/decisions/0006-gold-star-pivot.md)
  — the pivot's rationale and the full "removed in v2" list.
- **How to work here:** [CLAUDE.md](CLAUDE.md) — conventions and guardrails.
  Follow them; they still apply in every mode.

Do not re-derive the design. If SPEC.md and this file disagree, SPEC.md wins —
stop and flag it.

## Target

golfbot becomes a **scanner-only** tool matching SPEC.md v2: it pings the group
only for a **Gold Star** (all-star course + premium weekday window + ≥1 spot),
with randomized snark headlines, a `/full` on-demand firehose, and no in-bot
voting, availability, or booking.

## Done-when (objective, checkable)

The goal is complete when **all** hold:

- [ ] All 5 slices below are done.
- [ ] `pytest -q` passes with **0 failures** (baseline is 1 failure — see below).
- [ ] `ruff check .` is clean (baseline is 19 errors — see below).
- [ ] These no longer exist in the tree: `golfbot/grading.py`,
      `golfbot/availability.py`, per-slot vote callbacks, `📖 Booked it` /
      `/unbook` / `/out` / `/in` / `/avail` handlers, `models.Vote`,
      `models.Booking`, and writes to `data/bookings.jsonl`.
- [ ] A mock Gold Star slot renders the new alert format from SPEC.md
      (snark headline + `🔗 Book it`), with a randomized, no-immediate-repeat
      headline and `{name}` substitution.
- [ ] `/full` lists every configured course's open slots (all times).

## Baseline (as of 2026-08-07, before any slice)

- `pytest`: **1 failed, 204 passed**. The one failure is
  `tests/test_availability.py::test_load_save_roundtrip` — it lives in
  `availability.py`, which **Slice 3 deletes**, so this failure disappears with
  the module. Do not spend effort "fixing" it; deleting the module and its test
  file resolves it.
- `ruff check .`: **19 errors** (13 auto-fixable). The refactor should end clean;
  fix violations in code you touch, and clear the rest in Slice 5.

## Slices (do in order; one at a time)

Each slice is independently testable and leaves the bot runnable. See
[SPEC.md → Implementation slices](docs/SPEC.md#implementation-slices-v2-refactor)
for detail. **Stop and summarize after each slice — do not chain slices in one
unattended run.**

1. **Config v2** — new `premium_window` + course `all_star` flag + `alerts.headlines`;
   drop `time_windows`, `grading`, `search.default_players`/`expanded_players`,
   course `tier`. Migrate `config.yaml`, update `config.py` validation + tests.
   *Done when:* config tests pass on the new schema and `golfbot` still starts.

2. **Qualification rewrite** — replace `grading.py` + `pipeline.filter_and_grade`
   with the Gold Star rule; player filter becomes `spots ≥ 1`; scan-all /
   alert-on-four. *Done when:* pipeline tests assert only all-star + premium-window
   weekday slots qualify.

3. **Strip Model A + availability** — delete voting callbacks, tally rendering,
   `📖 Booked it`, `/unbook`, `models.Booking`, `models.Vote`, `bookings.jsonl`
   writes, `availability.py`, `/out` `/in` `/avail`, and their test files.
   Simplify slot ID to `course:date:time` + `last_alerted_spots`.
   *Done when:* bot starts clean, no dead imports, no vote/availability surface.

4. **New alert + snark + `/full`** — rewrite the alert renderer to the SPEC v2
   format; add `pick_headline()` (random, no-immediate-repeat, `{name}` sub);
   confirm `/full` is the firehose. *Done when:* a mock winner renders the new
   text; headline logic is unit-tested with a seeded RNG.

5. **Docs + cleanup** — reconcile `docs/ARCHITECTURE.md` with the removals, bump
   README status, clear remaining ruff. *Done when:* docs match shipped behavior
   and `ruff`/`pytest` are green.

## Guardrails (enforced by you, not by permission prompts)

This may run in bypass/no-permissions mode, so nothing will stop you — honor
these deliberately:

- **One slice per run.** Finish a slice, run `pytest -q` + `ruff check .`,
  summarize, and stop. Let the human review before the next slice.
- **Deletions (Slice 3):** per CLAUDE.md, pause and confirm before deleting
  files. List exactly what you'll delete first.
- **Never commit or push** unprompted. Branch for the work; let the human review
  the diff.
- **Live group chat.** Do not send real Telegram messages while testing —
  exercise renderers via unit tests / mock injection, not the live bot.
- **Tests:** never hit a live provider or write to real `data/` from a test
  (use fixtures + `tmp_path`).
- Update `docs/SPEC.md` first if a slice reveals a needed behavior change the
  spec doesn't already cover.
