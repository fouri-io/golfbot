"""Booking state.

User taps `✓ #N` on a match → confirmed booking recorded.
User taps `↩️ Cancel` on a booking → removed.

One active booking per date. Confirming a new match for an already-booked
date replaces the previous booking (the user changed their mind / moved
to a different course/time).

Bookings persist in `state.json` under `bookings`, keyed by ISO date.
Past dates are auto-pruned on load so the file doesn't grow forever.

The booking record is essentially a frozen Match dict (course / time /
roster at confirmation time) plus `booked_at` and `booked_by`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


def load_bookings(state: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """Read the bookings section; drop entries whose tee_date is in the past."""
    raw = state.get("bookings") or {}
    today = date.today()
    out: dict[date, dict[str, Any]] = {}
    for date_iso, record in raw.items():
        try:
            d = date.fromisoformat(date_iso)
        except ValueError:
            continue
        if d < today:
            continue
        out[d] = record
    return out


def save_bookings(
    state: dict[str, Any],
    bookings: dict[date, dict[str, Any]],
) -> None:
    state["bookings"] = {d.isoformat(): record for d, record in bookings.items()}


def add_booking(
    bookings: dict[date, dict[str, Any]],
    match_dict: dict[str, Any],
    booked_by: str,
    now: datetime,
) -> dict[str, Any]:
    """Confirm a booking from a Match dict. Returns the stored record.

    Replaces any existing booking for the same date.
    """
    tee_date = date.fromisoformat(match_dict["tee_date"])
    record = dict(match_dict)
    record["booked_at"] = now.isoformat()
    record["booked_by"] = booked_by
    bookings[tee_date] = record
    return record


def cancel_booking(
    bookings: dict[date, dict[str, Any]],
    tee_date: date,
) -> dict[str, Any] | None:
    """Remove the booking for a date. Returns the removed record, or None."""
    return bookings.pop(tee_date, None)


def match_is_booked(
    match_dict: dict[str, Any],
    bookings: dict[date, dict[str, Any]],
) -> bool:
    """True if this Match dict matches an existing booking — same date and
    same course (we treat any time/players change for the same course on
    the same date as 'the booking covers this match')."""
    try:
        tee_date = date.fromisoformat(match_dict["tee_date"])
    except (KeyError, ValueError):
        return False
    booking = bookings.get(tee_date)
    if booking is None:
        return False
    return (
        booking.get("course_key") == match_dict.get("course_key")
        and booking.get("tee_time") == match_dict.get("tee_time")
    )
