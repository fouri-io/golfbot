"""Tests for golfbot.actions — the Gold Star dedup + re-alert ledger."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from golfbot import actions
from golfbot.actions import SlotNotFound

NOW = datetime.fromisoformat("2026-05-15T17:00:00-05:00")
LATER = datetime.fromisoformat("2026-05-15T18:00:00-05:00")


def _state(*slots) -> dict:
    return {"paused": False, "pause_started_at": None, "tee_times": list(slots)}


def _slot(slot_id="jimmy_clay:2026-05-22:0740", spots=3, tee_date="2026-05-22", **kw):
    d = {
        "id": slot_id,
        "course_key": slot_id.split(":")[0],
        "tee_date": tee_date,
        "tee_time": "07:40:00",
        "spots_open": spots,
        "holes": 18,
        "booking_url": "https://example.com/book",
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "last_alerted_spots": None,
        "last_alerted_at": None,
    }
    d.update(kw)
    return d


# ---------- find_slot ----------


def test_find_slot_returns_match():
    s = _slot()
    assert actions.find_slot(_state(s), s["id"]) is s


def test_find_slot_raises_when_missing():
    with pytest.raises(SlotNotFound):
        actions.find_slot(_state(), "nope:2026-05-22:0740")


# ---------- upsert_slot ----------


def test_upsert_adds_new_slot():
    state = _state()
    slot, is_new = actions.upsert_slot(state, _slot(), NOW)
    assert is_new is True
    assert len(state["tee_times"]) == 1
    assert slot["id"] == "jimmy_clay:2026-05-22:0740"


def test_upsert_existing_refreshes_without_duplicating():
    state = _state(_slot(spots=2))
    incoming = _slot(spots=4)
    slot, is_new = actions.upsert_slot(state, incoming, LATER)
    assert is_new is False
    assert len(state["tee_times"]) == 1
    assert slot["spots_open"] == 4
    assert slot["last_seen_at"] == LATER.isoformat()


def test_upsert_preserves_first_seen_and_alert_history():
    state = _state(_slot(spots=2, last_alerted_spots=2, last_alerted_at=NOW.isoformat()))
    slot, _ = actions.upsert_slot(state, _slot(spots=3), LATER)
    assert slot["first_seen_at"] == NOW.isoformat()
    assert slot["last_alerted_spots"] == 2
    assert slot["last_alerted_at"] == NOW.isoformat()


def test_upsert_distinct_ids_coexist():
    state = _state()
    actions.upsert_slot(state, _slot("jimmy_clay:2026-05-22:0740"), NOW)
    actions.upsert_slot(state, _slot("roy_kizer:2026-05-22:0740"), NOW)
    assert len(state["tee_times"]) == 2


# ---------- should_alert / record_alert (docs/SPEC.md > Re-alert semantics) ----


def test_alerts_first_time_seen():
    assert actions.should_alert(_slot(spots=1)) is True


def test_realerts_when_more_spots_open():
    assert actions.should_alert(_slot(spots=3, last_alerted_spots=2)) is True


def test_silent_when_spot_count_unchanged():
    assert actions.should_alert(_slot(spots=3, last_alerted_spots=3)) is False


def test_silent_when_fewer_spots():
    """Someone took a seat — a worse opportunity, not worth a ping."""
    assert actions.should_alert(_slot(spots=1, last_alerted_spots=3)) is False


def test_record_alert_stamps_current_count():
    slot = _slot(spots=3)
    actions.record_alert(slot, NOW)
    assert slot["last_alerted_spots"] == 3
    assert slot["last_alerted_at"] == NOW.isoformat()


def test_alert_cycle_goes_quiet_after_recording():
    slot = _slot(spots=3)
    assert actions.should_alert(slot) is True
    actions.record_alert(slot, NOW)
    assert actions.should_alert(slot) is False

    # Spots increase -> alert again, then quiet again at the new level.
    slot["spots_open"] = 4
    assert actions.should_alert(slot) is True
    actions.record_alert(slot, LATER)
    assert actions.should_alert(slot) is False


# ---------- prune_past_slots ----------


def test_prune_drops_only_past_dates():
    state = _state(
        _slot("a:2026-05-20:0740", tee_date="2026-05-20"),
        _slot("b:2026-05-22:0740", tee_date="2026-05-22"),
        _slot("c:2026-05-25:0740", tee_date="2026-05-25"),
    )
    removed = actions.prune_past_slots(state, date(2026, 5, 22))
    assert removed == 1
    assert [s["id"] for s in state["tee_times"]] == [
        "b:2026-05-22:0740", "c:2026-05-25:0740",
    ]


def test_prune_keeps_today():
    state = _state(_slot("a:2026-05-22:0740", tee_date="2026-05-22"))
    assert actions.prune_past_slots(state, date(2026, 5, 22)) == 0


def test_prune_empty_ledger():
    state = _state()
    assert actions.prune_past_slots(state, date(2026, 5, 22)) == 0


# ---------- set_paused ----------


def test_set_paused_records_start_time():
    state = _state()
    actions.set_paused(state, True, NOW)
    assert state["paused"] is True
    assert state["pause_started_at"] == NOW.isoformat()


def test_repausing_preserves_original_timestamp():
    state = _state()
    actions.set_paused(state, True, NOW)
    actions.set_paused(state, True, LATER)
    assert state["pause_started_at"] == NOW.isoformat()


def test_resume_clears_timestamp():
    state = _state()
    actions.set_paused(state, True, NOW)
    actions.set_paused(state, False, None)
    assert state["paused"] is False
    assert state["pause_started_at"] is None
