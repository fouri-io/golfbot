"""Search-window computation.

Default: [today + start_offset_days, today + start_offset_days + horizon_days - 1].
After a booking on date D, the window starts at D+1 until D has passed,
then snaps back to the default.

See SPEC.md > Search behavior.
"""
from __future__ import annotations

from datetime import date, timedelta


def current_window(
    today: date,
    start_offset_days: int,
    horizon_days: int,
    booked_through: date | None,
) -> tuple[date, date]:
    """Return the inclusive (start, end) date range to search.

    `booked_through` is honored only if it is today or in the future; a
    booking from the past is ignored (the caller can also explicitly clear it).
    """
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1, got {horizon_days}")
    if start_offset_days < 0:
        raise ValueError(f"start_offset_days must be >= 0, got {start_offset_days}")

    if booked_through is not None and booked_through >= today:
        start = booked_through + timedelta(days=1)
    else:
        start = today + timedelta(days=start_offset_days)

    end = start + timedelta(days=horizon_days - 1)
    return start, end
