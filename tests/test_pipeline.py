"""Tests for golfbot.pipeline — the Gold Star rule.

docs/SPEC.md > The Gold Star rule: a slot qualifies iff ALL hold —
all-star course, premium window, weekday, >=1 open spot.
"""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.pipeline import (
    gold_star_slots,
    in_premium_window,
    is_desired_day,
    qualifies,
)
from golfbot.providers.base import RawSlot

REPO_ROOT = Path(__file__).resolve().parent.parent

# Calendar anchors used throughout. 2026-05-18 is a Monday.
MON = date(2026, 5, 18)
FRI = date(2026, 5, 22)
SAT = date(2026, 5, 23)
SUN = date(2026, 5, 24)

# config.yaml: premium_window 07:20–08:00
IN_WINDOW = time(7, 40)


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


def _slot(course: str, d: date = MON, t: time = IN_WINDOW, spots: int = 3) -> RawSlot:
    return RawSlot(
        course_key=course,
        tee_date=d,
        tee_time=t,
        players_available=spots,
        holes=18,
        booking_url=f"https://example.com/{course}/{d}/{t.strftime('%H%M')}",
        provider="golfnow",
        price_usd=45.0,
    )


# ---------- is_desired_day ----------


def test_is_desired_day_weekdays():
    assert is_desired_day(MON, ["monday", "tuesday"]) is True
    assert is_desired_day(date(2026, 5, 19), ["monday", "tuesday"]) is True
    assert is_desired_day(date(2026, 5, 20), ["monday", "tuesday"]) is False


def test_is_desired_day_weekend():
    assert is_desired_day(SAT, ["saturday", "sunday"]) is True
    assert is_desired_day(SUN, ["saturday", "sunday"]) is True
    assert is_desired_day(FRI, ["saturday", "sunday"]) is False


# ---------- the four Gold Star conditions, one at a time ----------


def test_qualifies_when_all_conditions_hold(cfg):
    assert qualifies(_slot("roy_kizer"), cfg) is True


def test_rejects_non_all_star_course(cfg):
    """Morris Williams is scanned but is not all-star — it can never alert."""
    assert qualifies(_slot("morris_williams"), cfg) is False


def test_rejects_unknown_course(cfg):
    assert qualifies(_slot("pebble_beach"), cfg) is False


def test_rejects_weekend(cfg):
    assert qualifies(_slot("roy_kizer", d=SAT), cfg) is False
    assert qualifies(_slot("roy_kizer", d=SUN), cfg) is False


def test_rejects_outside_premium_window(cfg):
    assert qualifies(_slot("roy_kizer", t=time(7, 0)), cfg) is False    # too early
    assert qualifies(_slot("roy_kizer", t=time(8, 30)), cfg) is False   # too late
    assert qualifies(_slot("roy_kizer", t=time(14, 0)), cfg) is False


def test_rejects_zero_spots(cfg):
    assert qualifies(_slot("roy_kizer", spots=0), cfg) is False


def test_accepts_single_spot(cfg):
    """v2 drops player-count matching entirely — one open spot is enough."""
    assert qualifies(_slot("roy_kizer", spots=1), cfg) is True


# ---------- window boundaries are inclusive on both ends ----------


def test_premium_window_boundaries_are_inclusive(cfg):
    assert in_premium_window(_slot("roy_kizer", t=time(7, 20)), cfg) is True
    assert in_premium_window(_slot("roy_kizer", t=time(8, 0)), cfg) is True


def test_just_outside_premium_window_is_rejected(cfg):
    assert in_premium_window(_slot("roy_kizer", t=time(7, 19)), cfg) is False
    assert in_premium_window(_slot("roy_kizer", t=time(8, 1)), cfg) is False


# ---------- gold_star_slots ----------


def test_gold_star_slots_keeps_only_all_star_courses(cfg):
    """Scan all, alert on four — the non-all-star slots are dropped here."""
    slots = [
        _slot("roy_kizer"),
        _slot("jimmy_clay"),
        _slot("riverside"),
        _slot("grey_rock_golf_club"),
        _slot("morris_williams"),
        _slot("lions"),
        _slot("avery_ranch_golf_club"),
    ]
    out = gold_star_slots(slots, cfg)
    assert {m.raw.course_key for m in out} == {
        "roy_kizer", "jimmy_clay", "riverside", "grey_rock_golf_club",
    }


def test_gold_star_slots_sets_course_display(cfg):
    out = gold_star_slots([_slot("roy_kizer")], cfg)
    assert len(out) == 1
    assert out[0].course_display == "Roy Kizer"
    assert out[0].members_in == ()
    assert out[0].members_out == ()


def test_gold_star_slots_keeps_every_qualifying_time(cfg):
    """No Policy B: same course+date no longer collapses to one best slot.

    ADR 0003 is superseded — each qualifying slot is its own Match so the
    state ledger can dedup and re-alert per slot.
    """
    slots = [
        _slot("roy_kizer", t=time(7, 20)),
        _slot("roy_kizer", t=time(7, 40)),
        _slot("roy_kizer", t=time(8, 0)),
    ]
    out = gold_star_slots(slots, cfg)
    assert len(out) == 3
    assert sorted(m.raw.tee_time for m in out) == [time(7, 20), time(7, 40), time(8, 0)]


def test_gold_star_slots_empty_input(cfg):
    assert gold_star_slots([], cfg) == []


def test_gold_star_slots_all_rejected(cfg):
    assert gold_star_slots([_slot("lions"), _slot("roy_kizer", d=SAT)], cfg) == []
