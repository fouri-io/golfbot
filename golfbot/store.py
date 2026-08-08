"""Flat-file persistence.

- state.json — live state, rewritten atomically on every change.

v2: `bookings.jsonl` is no longer written. Booking happens offline, so the
bot records none of it. An existing file is left on disk as history;
nothing appends to it (docs/SPEC.md > Data model).

We operate at the dict level here; conversion to/from dataclasses
(`TeeTimeSlot` etc.) belongs to the caller. datetime/date/time values are
serialized as ISO 8601 strings via the JSON `default` hook; callers parse
them back to typed objects when needed.

See docs/SPEC.md > Data model (flat files).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

# Serializes concurrent state.json writers. Reads don't need the lock thanks
# to the atomic-rename pattern (a reader sees either the old file or the new
# file, never a partial write).
_write_lock = asyncio.Lock()


def default_state() -> dict[str, Any]:
    """The empty starting state shape."""
    return {
        "paused": False,
        "pause_started_at": None,
        "last_poll_at": None,
        "last_alert_at": None,
        "next_run_at": None,
        "tee_times": [],   # the Gold Star dedup + re-alert ledger
        "raw_slots": [],   # last full scan, cached so /full is free
        "weather": {},
    }


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    raise TypeError(f"{type(obj).__name__} is not JSON-serializable")


def load_state(path: Path | str) -> dict[str, Any]:
    """Read state.json. Returns `default_state()` if the file is missing or empty.

    Synchronous: relies on atomic-rename writes — readers can never observe
    a half-written file.
    """
    p = Path(path)
    if not p.exists():
        return default_state()
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return default_state()
    return json.loads(text)


async def save_state(path: Path | str, state: dict[str, Any]) -> None:
    """Atomic write under an asyncio lock: temp file + os.replace.

    Concurrent calls from different async tasks are serialized.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, default=_json_default)
    async with _write_lock:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, p)


