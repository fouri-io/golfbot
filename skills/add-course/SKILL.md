---
name: add-course
description: Add a golf course to golfbot's scan roster, or wire up a new booking provider. Use when the user wants to watch a new course, says a course is missing from results, or asks to support a new booking system. Covers the config entry, provider mapping, fixture, test, and live verification.
---

# Adding a course

Nine courses are already wired. The work is small but there are two places that
must agree, and a course added to `config.yaml` alone will silently return
nothing.

## 1. Identify the provider and its id

| Provider | `provider_id` is | How to find it |
|---|---|---|
| `golfnow` | Integer GolfNow **facilityId** | Open the course on golfnow.com; the id is in the tee-time-search URL |
| `golfatx` | WebTrac **code string** | The course's code in the City of Austin WebTrac search |

Course keys are lowercase snake_case and must be unique — `config.py` validates
uniqueness and will refuse to load on a duplicate.

## 2. Add the config entry

In `config.yaml` under `courses:`:

```yaml
- { key: new_course, display: "New Course", provider: golfnow, provider_id: 1234, all_star: true }
```

`all_star` is the only quality knob, and it is the group's judgment about the
course, not a fact about it:

- **`all_star: true`** — can fire a Gold Star alert. Four courses hold this
  today; adding a fifth makes the bot louder.
- **omitted (default `false`)** — still scanned and still shown in `/full`,
  but can never ping.

**Ask which one rather than guessing.** Marking a course all-star when the group
wouldn't actually drop everything for it is how a scanner earns a mute.

## 3. GolfATX only — extend the name map

`providers/golfatx.py` holds `WEBTRAC_NAME_BY_CODE`, mapping WebTrac's display
name to the course code. **A GolfATX course missing from this map is skipped
with a warning and produces zero slots** — this is the step that gets forgotten.

WebTrac returns every Austin muni course in one search, so the map is how
results get matched back to our keys. The name must match WebTrac's own
spelling exactly.

## 4. Verify against the live provider

```bash
.venv/bin/golfbot scrape --course new_course --raw     # unfiltered — is anything coming back?
.venv/bin/golfbot scrape --course new_course           # through the filters
```

`--raw` first. If raw returns rows but the filtered run is empty, the course is
working and the Gold Star rule is doing its job — not all-star, outside the
premium window, a weekend, or zero open spots. That's not a bug.

If raw returns nothing:

- **GolfATX** — token harvest failure, a Cloudflare block, or a missing entry
  in `WEBTRAC_NAME_BY_CODE`. Check the logs; they distinguish these.
- **GolfNow** — usually a wrong `facilityId`, or genuinely no availability on
  the dates scanned. Try a date you can confirm has open times.

## 5. Record a fixture and test it

Capture one real response, save it as
`tests/fixtures/{provider}_{course_key}_{YYYY-MM-DD}`, and add a parse test
alongside the existing ones in `tests/test_providers_{provider}.py`.

The fixture is the contract — when the provider breaks in production, diffing
live output against it is the fastest way to see what changed. See
[rules/testing.md](../../rules/testing.md).

## Adding a whole new provider

Implement the `Provider` protocol in `providers/base.py`: a `name` attribute and
`async fetch_slots(courses, target_date, min_players) -> list[RawSlot]`. Then:

- Filter `courses` to the ones you own (`c.provider == self.name`) before
  issuing any request.
- Normalize to `RawSlot`. If the API doesn't expose per-slot seat counts, set
  `players_available` to the value you queried with and **document that it's a
  lower bound** — GolfNow does this and the asymmetry matters downstream.
- Log and return `[]` on failure. A provider outage must not fail the whole
  scan.
- Add `ProviderName` to the `Literal` in `config.py` and register the provider
  in `__main__.py`.

## Done when

- [ ] `golfbot scrape --course <key>` returns rows (or the absence is explained)
- [ ] Fixture committed and a parse test passes
- [ ] `ruff check .` and `pytest` are clean
- [ ] `all_star` confirmed with the owner, not assumed
