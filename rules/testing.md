# Testing rules

## Layout

One `tests/test_<module>.py` per source module, mirroring `golfbot/`.
`pytest.ini_options` sets `asyncio_mode = "auto"`, so `async def test_*` needs
no decorator.

```bash
pytest                                   # everything
pytest tests/test_pipeline.py -v         # one file
pytest tests/test_pipeline.py::test_rejects_weekend -v
pytest -k gold_star                      # by name
```

## Hard rules

- **No live network in tests, ever.** Providers are tested against recorded
  fixtures in `tests/fixtures/`. A test suite that hits GolfNow or WebTrac is
  slow, flaky, and rude to someone else's servers.
- **No writes outside `tmp_path`.** `store` and anything touching `data/` uses
  pytest's `tmp_path`. A test must never touch the real `data/state.json` —
  that's live state for a bot three people are actually using.
- **No Telegram calls.** Test the render functions, which are pure. The
  `send_*` coroutines are thin wrappers; if one needs coverage, fake the `Bot`.
- **Freeze time, or derive from it.** Anything reading "now" takes it as a
  parameter or gets it from an injected clock. Where that isn't possible —
  `scan_and_notify` reads the wall clock to compute its horizon — derive the
  test's dates from the *live* horizon rather than hardcoding them, so the test
  doesn't rot as the calendar moves. See `tests/test_scanner.py`.
- **Disable weather on test configs.** `scan_and_notify` refreshes the forecast
  from Open-Meteo when the cache is stale. Pass a config with
  `weather=None` or the suite makes a real HTTP call.

## What deserves a test

The pure modules are where tests pay for themselves — `pipeline`, `horizon`,
`actions`, `models`. Same input, same output, no setup.

The two rules deserve the most coverage: **what qualifies**
(`pipeline.qualifies`) and **what's worth announcing** (`actions.should_alert`).
Test each condition failing on its own, not just the happy path.

Bias toward **table-driven** cases for anything with windows or thresholds: the
premium window is inclusive on both ends, so the exact-boundary case is the one
that breaks.

Always cover:

- Empty results (a provider returning nothing is normal, not exceptional).
- Boundary times at the exact edge of `premium_window` — inclusive on both
  ends, so 07:20 and 08:00 both qualify.
- Round-tripping — `to_dict` → `from_dict` must be lossless. A silent
  round-trip bug in the availability layer went unnoticed for months before
  the module was deleted; that class of bug is exactly why these exist.
- Anything randomised (headline picking) gets a **seeded RNG**, never a bare
  `random`. Assert the property, not one lucky draw.

## Adding a provider fixture

Capture a real response once, save it under `tests/fixtures/` named
`{provider}_{course}_{date}`, and commit it. Scrub anything identifying. The
fixture is the contract — when a provider breaks in production, diffing live
output against the fixture is the fastest way to see what changed.

## Before handing work back

```bash
ruff check .
pytest
```

Both clean, or say plainly which isn't and why.
