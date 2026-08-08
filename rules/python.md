# Python standards

Applies to everything under `golfbot/` and `tests/`.

## Baseline

- **Python 3.12+.** Use modern syntax: `X | None` not `Optional[X]`,
  `list[str]` not `List[str]`, `match` where it genuinely reads better.
- `from __future__ import annotations` at the top of every module. Every
  existing module does this; keep it.
- Line length **100** (`pyproject.toml` sets it; don't fight it).
- Ruff rule set is `E, F, I, B, UP, SIM`. If a rule is wrong for a specific
  line, `# noqa: RULE` with a reason — never loosen the global config to
  silence one site.

## Typing

- Annotate all function signatures, including `-> None`.
- Public functions take and return domain types (`Match`, `RawSlot`,
  `Config`), not bare dicts. Dicts are for the storage seam only.
- `store.py` is deliberately dict-level — conversion to and from dataclasses
  belongs to the caller. Don't push dataclasses down into it.

## Structure

- **Pure by default.** `pipeline`, `grading`, `horizon`, `actions`, and the
  render half of `notifier` do no I/O and must stay that way. They're the
  parts that are cheap to test; keeping them pure is what makes the tests
  cheap.
- Network access belongs in `providers/` and `weather.py`. Filesystem access
  belongs in `store.py`. If you're adding I/O anywhere else, that's a signal
  the code is in the wrong module.
- Config is parsed once into a validated pydantic `Config` and passed down.
  Don't read `os.environ` or re-open `config.yaml` deep in the call stack.

## Docstrings

Module docstrings explain *why the module exists and what invariant it holds*,
not what the functions are named. The existing ones are the standard — match
their density. Note real quirks where they'd otherwise bite someone (see
`providers/golfnow.py` on the `+00:00` offset lie).

Cross-reference `docs/SPEC.md` by section when a module implements a spec'd
behavior.

## Known debt — don't replicate

- **Function-local imports** in `bot.py` and `scanner.py`
  (`from golfbot import scanner as _scanner` inside a handler) work around
  import cycles. They are not a pattern to copy. New code should import at
  module top; if that creates a cycle, the module boundary is wrong.
- `bot.py` (1042 lines) and `notifier.py` (799) are oversized and slated for
  splitting. Don't add to them without asking whether the new code belongs in
  a new module.
