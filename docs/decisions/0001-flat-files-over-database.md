# 0001 — Flat JSON files instead of a database

**Status:** Accepted

## Context

golfbot serves a fixed group of three people on one self-hosted process. Live
state is a few hundred KB at most: current matches, per-member availability,
one booking per date, a weather cache. There is exactly one writer process.

## Decision

Persist to flat files in `data/`:

- `state.json` — all live state, rewritten in full on every change.
- `bookings.jsonl` — append-only history.

Concurrency is handled by the atomic-rename pattern: write to a temp file, then
`os.replace`. Writes are serialized by an `asyncio.Lock` in `store.py`; reads
take no lock, because a reader always sees either the complete old file or the
complete new one.

## Consequences

Good:

- State is human-readable and hand-editable when something goes wrong at 6am.
- No schema migrations, no daemon, no connection handling.
- Tests use `tmp_path` and a real file — no fixtures, no fakes, no mocking.

Bad:

- Whole-file rewrite means cost grows with total state, not with the size of
  the change. Fine at this scale; it would not be at 100× the data.
- No queries. Anything you want to ask has to be answerable by loading the
  whole object into memory and filtering in Python.
- Only safe with a **single writer process**. Running two `golfbot run`
  instances against the same `data/` directory will silently lose writes —
  last writer wins on the full document.

## Revisit if

State stops fitting comfortably in memory, or a second writer becomes necessary
(e.g. a web UI alongside the bot). At that point SQLite is the obvious next
step — it keeps the single-file, zero-daemon property.
