"""Tests for golfbot.horizon."""
from __future__ import annotations

from datetime import date

import pytest

from golfbot.horizon import current_window


def test_default_window():
    # today=Fri 2026-05-15, offset=1, horizon=7
    # Expect: [Sat May 16, Fri May 22]
    start, end = current_window(date(2026, 5, 15), 1, 7)
    assert start == date(2026, 5, 16)
    assert end == date(2026, 5, 22)


def test_zero_offset():
    # offset=0 means "today onward"
    start, end = current_window(date(2026, 5, 15), 0, 7)
    assert start == date(2026, 5, 15)
    assert end == date(2026, 5, 21)


def test_single_day_horizon():
    start, end = current_window(date(2026, 5, 15), 1, 1)
    assert start == end == date(2026, 5, 16)


def test_rejects_zero_horizon():
    with pytest.raises(ValueError, match="horizon_days must be >= 1"):
        current_window(date(2026, 5, 15), 1, 0)


def test_rejects_negative_offset():
    with pytest.raises(ValueError, match="start_offset_days must be >= 0"):
        current_window(date(2026, 5, 15), -1, 7)
