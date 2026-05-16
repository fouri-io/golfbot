"""Tests for golfbot.models (dataclass <-> dict round-trips)."""
from __future__ import annotations

from datetime import date, datetime, time

from golfbot.models import Booking, TeeTimeSlot, Vote, make_slot_id


def test_make_slot_id():
    sid = make_slot_id("roy_kizer", date(2026, 5, 23), time(8, 0), 4)
    assert sid == "roy_kizer:2026-05-23:0800:4"


def test_make_slot_id_pads_hours():
    sid = make_slot_id("lions", date(2026, 5, 23), time(7, 5), 2)
    assert sid == "lions:2026-05-23:0705:2"


def test_vote_roundtrip():
    v = Vote(vote="yes", voted_at=datetime(2026, 5, 15, 17, 2))
    d = v.to_dict()
    assert d == {"vote": "yes", "voted_at": "2026-05-15T17:02:00"}
    assert Vote.from_dict(d) == v


def test_tee_time_slot_roundtrip():
    slot = TeeTimeSlot(
        id="roy_kizer:2026-05-23:0800:4",
        course_key="roy_kizer",
        tee_date=date(2026, 5, 23),
        tee_time=time(8, 0),
        players_open=4,
        holes=18,
        grade="A",
        booking_url="https://example.com",
        first_seen_at=datetime(2026, 5, 15, 17, 0),
        last_seen_at=datetime(2026, 5, 15, 18, 0),
        status="open",
        message_id=4421,
        votes={"Colby": Vote(vote="yes", voted_at=datetime(2026, 5, 15, 17, 2))},
    )
    d = slot.to_dict()
    assert d["tee_date"] == "2026-05-23"
    assert d["tee_time"] == "08:00:00"
    assert d["message_id"] == 4421
    assert d["votes"]["Colby"]["vote"] == "yes"
    assert TeeTimeSlot.from_dict(d) == slot


def test_tee_time_slot_default_votes_and_message_id():
    slot = TeeTimeSlot(
        id="x",
        course_key="lions",
        tee_date=date(2026, 5, 24),
        tee_time=time(7, 30),
        players_open=2,
        holes=18,
        grade="B",
        booking_url="https://example.com",
        first_seen_at=datetime(2026, 5, 15, 17, 0),
        last_seen_at=datetime(2026, 5, 15, 17, 0),
        status="open",
    )
    d = slot.to_dict()
    assert d["message_id"] is None
    assert d["votes"] == {}
    assert TeeTimeSlot.from_dict(d) == slot


def test_booking_roundtrip():
    b = Booking(
        booked_at=datetime(2026, 5, 15, 14, 14),
        booked_by="Colby",
        course_key="roy_kizer",
        tee_date=date(2026, 5, 23),
        tee_time=time(8, 0),
        players=4,
        roster={"yes": ["Colby", "Steve"], "no": ["Ed"]},
    )
    d = b.to_dict()
    assert d["undone_at"] is None
    assert d["tee_date"] == "2026-05-23"
    assert Booking.from_dict(d) == b


def test_booking_with_undo():
    b = Booking(
        booked_at=datetime(2026, 5, 15, 14, 14),
        booked_by="Colby",
        course_key="roy_kizer",
        tee_date=date(2026, 5, 23),
        tee_time=time(8, 0),
        players=4,
        roster={"yes": ["Colby"], "no": []},
        undone_at=datetime(2026, 5, 15, 15, 0),
    )
    d = b.to_dict()
    assert d["undone_at"] == "2026-05-15T15:00:00"
    assert Booking.from_dict(d) == b
