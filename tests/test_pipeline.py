"""Tests for golfbot.pipeline."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.pipeline import (
    Match,
    apply_policy_b,
    filter_and_grade,
    is_desired_day,
)
from golfbot.providers.base import RawSlot

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


def _slot(course: str, d: date, t: time, players: int = 3) -> RawSlot:
    return RawSlot(
        course_key=course,
        tee_date=d,
        tee_time=t,
        players_available=players,
        holes=18,
        booking_url=f"https://example.com/{course}/{d}/{t.strftime('%H%M')}",
        provider="golfnow",
        price_usd=45.0,
    )


# ---------- is_desired_day ----------


def test_is_desired_day_weekdays():
    # 2026-05-18 is a Monday
    assert is_desired_day(date(2026, 5, 18), ["monday", "tuesday"]) is True
    assert is_desired_day(date(2026, 5, 19), ["monday", "tuesday"]) is True
    assert is_desired_day(date(2026, 5, 20), ["monday", "tuesday"]) is False


def test_is_desired_day_weekend():
    # 2026-05-23 is a Saturday
    assert is_desired_day(date(2026, 5, 23), ["saturday", "sunday"]) is True
    assert is_desired_day(date(2026, 5, 24), ["saturday", "sunday"]) is True
    assert is_desired_day(date(2026, 5, 22), ["saturday", "sunday"]) is False  # Fri


# ---------- filter_and_grade ----------


def test_filter_drops_wrong_day_of_week(cfg):
    # 2026-05-23 = Sat, config defaults to Mon-Fri
    slots = [_slot("roy_kizer", date(2026, 5, 23), time(7, 30))]
    assert filter_and_grade(slots, cfg) == []


def test_filter_drops_unknown_course(cfg):
    slots = [_slot("pebble_beach", date(2026, 5, 18), time(7, 30))]
    assert filter_and_grade(slots, cfg) == []


def test_filter_drops_outside_acceptable_window(cfg):
    # acceptable = 07:00-09:00; 14:00 is way out
    slots = [_slot("roy_kizer", date(2026, 5, 18), time(14, 0))]
    assert filter_and_grade(slots, cfg) == []


def test_filter_drops_below_min_grade(cfg):
    # notify_min_grade = B; tier-2 course in acceptable but non-ideal = C
    # Morris Williams is tier-2; 8:30 is acceptable but not ideal → C → dropped
    slots = [_slot("morris_williams", date(2026, 5, 18), time(8, 30))]
    assert filter_and_grade(slots, cfg) == []


def test_filter_keeps_grade_A(cfg):
    # Roy Kizer is tier-1; 7:30 is in ideal window → Grade A
    slots = [_slot("roy_kizer", date(2026, 5, 18), time(7, 30))]
    matches = filter_and_grade(slots, cfg)
    assert len(matches) == 1
    assert matches[0].grade == "A"
    assert matches[0].course_display == "Roy Kizer"
    assert matches[0].course_tier == 1


def test_filter_keeps_grade_B_tier2_ideal(cfg):
    # Morris Williams is tier-2; 7:30 is ideal → B
    slots = [_slot("morris_williams", date(2026, 5, 18), time(7, 30))]
    matches = filter_and_grade(slots, cfg)
    assert len(matches) == 1
    assert matches[0].grade == "B"


def test_filter_keeps_grade_B_tier1_acceptable(cfg):
    # Roy Kizer tier-1 at 8:30 (acceptable, not ideal) → B
    slots = [_slot("roy_kizer", date(2026, 5, 18), time(8, 30))]
    matches = filter_and_grade(slots, cfg)
    assert len(matches) == 1
    assert matches[0].grade == "B"


# ---------- apply_policy_b ----------


def test_policy_b_picks_higher_grade(cfg):
    # Two slots at Roy Kizer on the same date: one A, one B → keep A
    matches = [
        Match(_slot("roy_kizer", date(2026, 5, 18), time(8, 30)), "B", "Roy Kizer", 1),
        Match(_slot("roy_kizer", date(2026, 5, 18), time(7, 45)), "A", "Roy Kizer", 1),
    ]
    out = apply_policy_b(matches)
    assert len(out) == 1
    assert out[0].grade == "A"
    assert out[0].raw.tee_time == time(7, 45)


def test_policy_b_tiebreaks_by_earlier_time(cfg):
    matches = [
        Match(_slot("roy_kizer", date(2026, 5, 18), time(8, 0)), "A", "Roy Kizer", 1),
        Match(_slot("roy_kizer", date(2026, 5, 18), time(7, 30)), "A", "Roy Kizer", 1),
    ]
    out = apply_policy_b(matches)
    assert len(out) == 1
    assert out[0].raw.tee_time == time(7, 30)


def test_policy_b_keeps_distinct_pairs(cfg):
    """Different (course, date) pairs each get their own slot."""
    matches = [
        Match(_slot("roy_kizer", date(2026, 5, 18), time(7, 30)), "A", "Roy Kizer", 1),
        Match(_slot("riverside", date(2026, 5, 18), time(7, 30)), "B", "Riverside", 2),
        Match(_slot("roy_kizer", date(2026, 5, 19), time(7, 30)), "A", "Roy Kizer", 1),
    ]
    out = apply_policy_b(matches)
    assert len(out) == 3
