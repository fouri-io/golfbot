"""Flat-file persistence.

- state.json     — live state, rewritten atomically on every change.
- bookings.jsonl — append-only booking history.

We operate at the dict level here; conversion to/from dataclasses
(`TeeTimeSlot` etc.) belongs to the caller. datetime/date/time values are
serialized as ISO 8601 strings via the JSON `default` hook; callers parse
them back to typed objects when needed.

See SPEC.md > Data model (flat files).
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
        "tee_times": [],
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


def append_booking(path: Path | str, booking: dict[str, Any]) -> None:
    """Append one JSON object as a line to bookings.jsonl.

    POSIX guarantees that single writes under PIPE_BUF (4096 bytes) to a
    file opened in append mode are atomic, so no lock is needed for
    individual lines.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(booking, default=_json_default) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def read_bookings(path: Path | str) -> list[dict[str, Any]]:
    """Read all booking lines. Returns [] if the file is missing."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                out.append(json.loads(stripped))
    return out
