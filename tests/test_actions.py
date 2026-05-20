"""Tests for golfbot.actions (pure state mutations)."""
from __future__ import annotations

from datetime import datetime

import pytest

from golfbot import actions
from golfbot.store import default_state

NOW = datetime(2026, 5, 15, 17, 0)


def _make_slot(slot_id: str = "roy_kizer:2026-05-23:0800:4", status: str = "open") -> dict:
    return {
        "id": slot_id,
        "course_key": "roy_kizer",
        "tee_date": "2026-05-23",
        "tee_time": "08:00:00",
        "players_open": 4,
        "holes": 18,
        "grade": "A",
        "booking_url": "https://example.com/book",
        "first_seen_at": "2026-05-15T17:00:00",
        "last_seen_at": "2026-05-15T17:00:00",
        "status": status,
        "message_id": None,
        "votes": {},
    }


# ---------- find_slot / find_slot_by_message ----------


def test_find_slot_returns_existing():
    state = default_state()
    slot = _make_slot()
    state["tee_times"].append(slot)
    assert actions.find_slot(state, slot["id"]) is slot


def test_find_slot_raises_when_missing():
    state = default_state()
    with pytest.raises(actions.SlotNotFound):
        actions.find_slot(state, "missing")


def test_find_slot_by_message():
    state = default_state()
    s = _make_slot()
    s["message_id"] = 42
    state["tee_times"].append(s)
    assert actions.find_slot_by_message(state, 42) is s
    assert actions.find_slot_by_message(state, 99) is None


# ---------- upsert_slot ----------


def test_upsert_adds_new_slot():
    state = default_state()
    slot = _make_slot()
    result, is_new = actions.upsert_slot(state, slot, NOW)
    assert is_new is True
    assert result is slot
    assert state["tee_times"] == [slot]


def test_upsert_refreshes_existing_last_seen():
    state = default_state()
    slot = _make_slot()
    state["tee_times"].append(slot)
    later = datetime(2026, 5, 15, 18, 30)

    new_input = _make_slot()
    result, is_new = actions.upsert_slot(state, new_input, later)
    assert is_new is False
    assert result is slot  # the existing dict, not the new one
    assert slot["last_seen_at"] == later.isoformat()
    assert len(state["tee_times"]) == 1


# ---------- record_vote ----------


def test_record_vote_yes():
    state = default_state()
    state["tee_times"].append(_make_slot())
    slot = actions.record_vote(state, _make_slot()["id"], "Colby", "yes", NOW)
    assert slot["votes"]["Colby"] == {"vote": "yes", "voted_at": NOW.isoformat()}


def test_record_vote_replaces_prior_vote():
    state = default_state()
    state["tee_times"].append(_make_slot())
    sid = _make_slot()["id"]
    actions.record_vote(state, sid, "Colby", "yes", NOW)
    later = datetime(2026, 5, 15, 17, 30)
    actions.record_vote(state, sid, "Colby", "no", later)
    slot = actions.find_slot(state, sid)
    assert slot["votes"]["Colby"]["vote"] == "no"
    assert slot["votes"]["Colby"]["voted_at"] == later.isoformat()


def test_record_vote_rejects_invalid_value():
    state = default_state()
    state["tee_times"].append(_make_slot())
    with pytest.raises(actions.ActionError, match="invalid vote"):
        actions.record_vote(state, _make_slot()["id"], "Colby", "maybe", NOW)


def test_record_vote_rejects_non_open_slot():
    state = default_state()
    state["tee_times"].append(_make_slot(status="booked"))
    with pytest.raises(actions.ActionError, match="cannot vote"):
        actions.record_vote(state, _make_slot()["id"], "Colby", "yes", NOW)


# ---------- mark_booked ----------


def test_mark_booked_sets_status_and_booking():
    state = default_state()
    state["tee_times"].append(_make_slot())
    slot, booking = actions.mark_booked(state, _make_slot()["id"], "Colby", NOW)
    assert slot["status"] == "booked"
    assert slot["booked_by"] == "Colby"
    assert slot["booked_at"] == NOW.isoformat()
    assert booking["booked_by"] == "Colby"
    assert booking["tee_date"] == "2026-05-23"
    assert booking["undone_at"] is None
    # No longer touches horizon_override_until (dead state field).
    assert "horizon_override_until" not in state


def test_mark_booked_captures_roster():
    state = default_state()
    state["tee_times"].append(_make_slot())
    sid = _make_slot()["id"]
    actions.record_vote(state, sid, "Colby", "yes", NOW)
    actions.record_vote(state, sid, "Steve", "yes", NOW)
    actions.record_vote(state, sid, "Ed", "no", NOW)

    _, booking = actions.mark_booked(state, sid, "Colby", NOW)
    assert booking["roster"] == {"yes": ["Colby", "Steve"], "no": ["Ed"]}


def test_mark_booked_rejects_already_booked():
    state = default_state()
    state["tee_times"].append(_make_slot(status="booked"))
    with pytest.raises(actions.ActionError, match="cannot book"):
        actions.mark_booked(state, _make_slot()["id"], "Colby", NOW)


# ---------- mark_skipped ----------


def test_mark_skipped():
    state = default_state()
    state["tee_times"].append(_make_slot())
    slot = actions.mark_skipped(state, _make_slot()["id"])
    assert slot["status"] == "skipped"


def test_mark_skipped_rejects_non_open():
    state = default_state()
    state["tee_times"].append(_make_slot(status="booked"))
    with pytest.raises(actions.ActionError, match="cannot skip"):
        actions.mark_skipped(state, _make_slot()["id"])


# ---------- set_paused ----------


def test_set_paused_true_records_timestamp():
    state = default_state()
    actions.set_paused(state, True, NOW)
    assert state["paused"] is True
    assert state["pause_started_at"] == NOW.isoformat()


def test_set_paused_idempotent_preserves_original_timestamp():
    state = default_state()
    actions.set_paused(state, True, NOW)
    later = datetime(2026, 5, 15, 18, 0)
    actions.set_paused(state, True, later)
    assert state["pause_started_at"] == NOW.isoformat()


def test_set_paused_false_clears_timestamp():
    state = default_state()
    actions.set_paused(state, True, NOW)
    actions.set_paused(state, False, None)
    assert state["paused"] is False
    assert state["pause_started_at"] is None


# ---------- undo_booking ----------


def test_undo_booking_reverts_status():
    state = default_state()
    state["tee_times"].append(_make_slot())
    sid = _make_slot()["id"]
    actions.mark_booked(state, sid, "Colby", NOW)

    later = datetime(2026, 5, 15, 18, 0)
    slot, undo = actions.undo_booking(state, sid, later)
    assert slot["status"] == "open"
    assert "booked_by" not in slot
    assert "booked_at" not in slot
    assert undo["undone_at"] == later.isoformat()
    assert undo["tee_date"] == "2026-05-23"


def test_undo_booking_rejects_non_booked():
    state = default_state()
    state["tee_times"].append(_make_slot())
    with pytest.raises(actions.ActionError, match="cannot undo"):
        actions.undo_booking(state, _make_slot()["id"], NOW)
