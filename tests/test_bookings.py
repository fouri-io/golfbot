"""Tests for golfbot.bookings."""
from __future__ import annotations

from datetime import date, datetime

from golfbot import bookings as bookings_mod


def _match(course="roy_kizer", d="2026-05-18", t="07:30:00"):
    return {
        "course_key": course,
        "course_display": "Roy Kizer",
        "course_tier": 1,
        "tee_date": d,
        "tee_time": t,
        "grade": "A",
        "players_available": 3,
        "holes": 18,
        "booking_url": "https://example.com/book",
        "price_usd": None,
        "provider": "golfatx",
        "members_in": ["Colby"],
        "members_out": [],
    }


def test_add_and_load_roundtrip():
    state: dict = {}
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(), "Colby", datetime(2026, 5, 16, 12, 0))
    bookings_mod.save_bookings(state, bookings)
    assert "2026-05-18" in state["bookings"]
    assert state["bookings"]["2026-05-18"]["booked_by"] == "Colby"

    reloaded = bookings_mod.load_bookings(state)
    assert date(2026, 5, 18) in reloaded


def test_add_replaces_same_date():
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(course="roy_kizer"), "Colby", datetime(2026, 5, 16))
    bookings_mod.add_booking(bookings, _match(course="riverside"), "Colby", datetime(2026, 5, 16))
    assert len(bookings) == 1
    assert bookings[date(2026, 5, 18)]["course_key"] == "riverside"


def test_cancel_returns_removed_record():
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(), "Colby", datetime(2026, 5, 16))
    removed = bookings_mod.cancel_booking(bookings, date(2026, 5, 18))
    assert removed is not None
    assert removed["course_key"] == "roy_kizer"
    assert bookings == {}


def test_cancel_unknown_date_returns_none():
    bookings: dict = {}
    assert bookings_mod.cancel_booking(bookings, date(2026, 5, 18)) is None


def test_load_prunes_past_dates():
    state = {"bookings": {"2000-01-01": _match(d="2000-01-01"), "2099-01-01": _match(d="2099-01-01")}}
    reloaded = bookings_mod.load_bookings(state)
    assert date(2000, 1, 1) not in reloaded
    assert date(2099, 1, 1) in reloaded


def test_match_is_booked_true_for_same_date_course_time():
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(), "Colby", datetime(2026, 5, 16))
    assert bookings_mod.match_is_booked(_match(), bookings) is True


def test_match_is_booked_false_for_different_course():
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(course="roy_kizer"), "Colby", datetime(2026, 5, 16))
    assert bookings_mod.match_is_booked(_match(course="riverside"), bookings) is False


def test_match_is_booked_false_for_different_time():
    bookings: dict = {}
    bookings_mod.add_booking(bookings, _match(t="07:30:00"), "Colby", datetime(2026, 5, 16))
    assert bookings_mod.match_is_booked(_match(t="08:30:00"), bookings) is False


def test_match_is_booked_false_when_no_booking():
    assert bookings_mod.match_is_booked(_match(), {}) is False
