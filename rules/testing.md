# Testing rules

## Layout

One `tests/test_<module>.py` per source module, mirroring `golfbot/`.
`pytest.ini_options` sets `asyncio_mode = "auto"`, so `async def test_*` needs
no decorator.

```bash
pytest                                   # everything
pytest tests/test_pipeline.py -v         # one file
pytest tests/test_pipeline.py::test_policy_b_prefers_higher_grade -v
pytest -k policy_b                       # by name
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
- **Freeze time.** Anything reading "now" takes it as a parameter or gets it
  from an injected clock. Tests that depend on the wall clock fail at midnight,
  on a Sunday, or in a different month.

## What deserves a test

The pure modules are where tests pay for themselves — `pipeline`, `grading`,
`horizon`, `actions`, `availability`, `models`. Same input, same output, no
setup.

Bias toward **table-driven** cases for anything with windows or thresholds:
grading boundaries are inclusive on both ends, so the exact-boundary case is
the one that breaks.

Always cover:

- Empty results (a provider returning nothing is normal, not exceptional).
- Boundary times at the exact edge of `ideal` and `acceptable`.
- Round-tripping — `to_dict` → `from_dict` must be lossless. Availability
  currently has a **failing round-trip test**; that class of bug is exactly
  why these exist.

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
