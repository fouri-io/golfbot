"""Search-window computation.

Simple rolling window: [today + start_offset_days,
today + start_offset_days + horizon_days - 1].

Earlier versions shifted the start after a booking to skip past the
booked date. That logic was dropped — the bot is now a "what's available
now" view; bookings are displayed alongside the rolling window, not
gating it.
"""
from __future__ import annotations

from datetime import date, timedelta


def current_window(
    today: date,
    start_offset_days: int,
    horizon_days: int,
) -> tuple[date, date]:
    """Return the inclusive (start, end) date range to search."""
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1, got {horizon_days}")
    if start_offset_days < 0:
        raise ValueError(f"start_offset_days must be >= 0, got {start_offset_days}")

    start = today + timedelta(days=start_offset_days)
    end = start + timedelta(days=horizon_days - 1)
    return start, end
