"""Tests for golfbot.models."""
from __future__ import annotations

from datetime import date, datetime, time

from golfbot.models import TeeTimeSlot, make_slot_id

# ---------- make_slot_id ----------


def test_make_slot_id_shape():
    assert make_slot_id("roy_kizer", date(2026, 5, 23), time(8, 0)) == (
        "roy_kizer:2026-05-23:0800"
    )


def test_make_slot_id_is_deterministic():
    a = make_slot_id("jimmy_clay", date(2026, 5, 22), time(7, 40))
    b = make_slot_id("jimmy_clay", date(2026, 5, 22), time(7, 40))
    assert a == b


def test_slot_id_excludes_spot_count():
    """docs/SPEC.md > Re-alert semantics: a slot is one record across polls
    regardless of how many spots are open, so the id must not encode them."""
    slot_2 = _slot(spots=2)
    slot_4 = _slot(spots=4)
    assert slot_2.id == slot_4.id


def test_make_slot_id_distinguishes_time_and_date():
    base = make_slot_id("lions", date(2026, 5, 22), time(7, 40))
    assert base != make_slot_id("lions", date(2026, 5, 22), time(7, 50))
    assert base != make_slot_id("lions", date(2026, 5, 23), time(7, 40))
    assert base != make_slot_id("roy_kizer", date(2026, 5, 22), time(7, 40))


# ---------- TeeTimeSlot ----------


NOW = datetime.fromisoformat("2026-05-15T17:00:00-05:00")


def _slot(spots: int = 3, **kw) -> TeeTimeSlot:
    d = date(2026, 5, 22)
    t = time(7, 40)
    defaults = dict(
        id=make_slot_id("jimmy_clay", d, t),
        course_key="jimmy_clay",
        tee_date=d,
        tee_time=t,
        spots_open=spots,
        holes=18,
        booking_url="https://example.com/book",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    defaults.update(kw)
    return TeeTimeSlot(**defaults)   # type: ignore[arg-type]


def test_roundtrip_is_lossless():
    slot = _slot()
    assert TeeTimeSlot.from_dict(slot.to_dict()) == slot


def test_roundtrip_with_alert_bookkeeping():
    slot = _slot(last_alerted_spots=2, last_alerted_at=NOW)
    restored = TeeTimeSlot.from_dict(slot.to_dict())
    assert restored == slot
    assert restored.last_alerted_spots == 2
    assert restored.last_alerted_at == NOW


def test_never_alerted_defaults_to_none():
    slot = _slot()
    assert slot.last_alerted_spots is None
    assert slot.last_alerted_at is None
    assert TeeTimeSlot.from_dict(slot.to_dict()).last_alerted_at is None


def test_to_dict_serializes_dates_as_iso_strings():
    d = _slot().to_dict()
    assert d["tee_date"] == "2026-05-22"
    assert d["tee_time"] == "07:40:00"
    assert d["first_seen_at"] == NOW.isoformat()


def test_to_dict_has_no_v1_keys():
    """Votes, grades, booking and message state are gone in v2."""
    d = _slot().to_dict()
    for gone in ("votes", "grade", "status", "message_id", "players_open"):
        assert gone not in d
