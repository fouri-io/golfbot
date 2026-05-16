"""Tests for golfbot.horizon."""
from __future__ import annotations

from datetime import date

import pytest

from golfbot.horizon import current_window


# ---------- default window (no booking) ----------


def test_default_window():
    # today=Fri 2026-05-15, offset=1, horizon=7
    # Expect: [Sat May 16, Fri May 22]
    start, end = current_window(date(2026, 5, 15), 1, 7, booked_through=None)
    assert start == date(2026, 5, 16)
    assert end == date(2026, 5, 22)


def test_zero_offset():
    # offset=0 means "today onward"
    start, end = current_window(date(2026, 5, 15), 0, 7, None)
    assert start == date(2026, 5, 15)
    assert end == date(2026, 5, 21)


def test_single_day_horizon():
    start, end = current_window(date(2026, 5, 15), 1, 1, None)
    assert start == end == date(2026, 5, 16)


# ---------- booking override ----------


def test_booking_in_future_shifts_window():
    # Booked through Sat May 23. Today is Fri May 15.
    # Override: start = May 24, end = May 30 (7 days).
    start, end = current_window(
        today=date(2026, 5, 15),
        start_offset_days=1,
        horizon_days=7,
        booked_through=date(2026, 5, 23),
    )
    assert start == date(2026, 5, 24)
    assert end == date(2026, 5, 30)


def test_booking_today_still_overrides():
    # Booked through today → override still applies (notifications suppressed
    # through today; next window starts tomorrow).
    start, end = current_window(
        today=date(2026, 5, 15),
        start_offset_days=1,
        horizon_days=7,
        booked_through=date(2026, 5, 15),
    )
    assert start == date(2026, 5, 16)
    assert end == date(2026, 5, 22)


def test_booking_in_past_is_ignored():
    # Booked date has passed → snap back to default window.
    start, end = current_window(
        today=date(2026, 5, 15),
        start_offset_days=1,
        horizon_days=7,
        booked_through=date(2026, 5, 10),
    )
    assert start == date(2026, 5, 16)
    assert end == date(2026, 5, 22)


# ---------- validation ----------


def test_rejects_zero_horizon():
    with pytest.raises(ValueError, match="horizon_days must be >= 1"):
        current_window(date(2026, 5, 15), 1, 0, None)


def test_rejects_negative_offset():
    with pytest.raises(ValueError, match="start_offset_days must be >= 0"):
        current_window(date(2026, 5, 15), -1, 7, None)
