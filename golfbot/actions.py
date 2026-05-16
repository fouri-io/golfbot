"""Pure state mutations.

Take a `state` dict + inputs, mutate it in place, return either nothing or a
side-effect record (a booking line, an undo record). No I/O, no Telegram
calls — the caller (bot.py / mock_source.py) handles persistence and
external effects.

Slots inside `state["tee_times"]` are stored as their dict form
(`TeeTimeSlot.to_dict()`). This module operates at the dict level so it
doesn't depend on the dataclass beyond ID construction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


class ActionError(Exception):
    """Caller-visible error: tried to do something invalid (skip a booked
    slot, vote on an expired slot, undo without an active booking, ...)."""


class SlotNotFound(ActionError):
    def __init__(self, slot_id: str):
        super().__init__(f"slot not found: {slot_id}")
        self.slot_id = slot_id


# --------------------------------------------------------------------------- #
# Lookups                                                                      #
# --------------------------------------------------------------------------- #


def find_slot(state: dict[str, Any], slot_id: str) -> dict[str, Any]:
    for s in state["tee_times"]:
        if s["id"] == slot_id:
            return s
    raise SlotNotFound(slot_id)


def find_slot_by_message(state: dict[str, Any], message_id: int) -> dict[str, Any] | None:
    """Return the slot whose Telegram message_id matches, or None."""
    for s in state["tee_times"]:
        if s.get("message_id") == message_id:
            return s
    return None


# --------------------------------------------------------------------------- #
# Mutations                                                                    #
# --------------------------------------------------------------------------- #


def upsert_slot(
    state: dict[str, Any],
    slot_dict: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """Add a slot if id is new, or refresh last_seen_at on an existing slot.

    Returns (slot_in_state, is_new).
    """
    for existing in state["tee_times"]:
        if existing["id"] == slot_dict["id"]:
            existing["last_seen_at"] = now.isoformat()
            return existing, False
    state["tee_times"].append(slot_dict)
    return slot_dict, True


def attach_message_id(
    state: dict[str, Any],
    slot_id: str,
    message_id: int,
) -> dict[str, Any]:
    slot = find_slot(state, slot_id)
    slot["message_id"] = message_id
    return slot


def record_vote(
    state: dict[str, Any],
    slot_id: str,
    member_name: str,
    vote: str,
    now: datetime,
) -> dict[str, Any]:
    if vote not in {"yes", "no"}:
        raise ActionError(f"invalid vote: {vote!r}")
    slot = find_slot(state, slot_id)
    if slot["status"] != "open":
        raise ActionError(f"slot {slot_id} is {slot['status']}, cannot vote")
    slot.setdefault("votes", {})[member_name] = {
        "vote": vote,
        "voted_at": now.isoformat(),
    }
    return slot


def mark_booked(
    state: dict[str, Any],
    slot_id: str,
    booked_by: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mark slot as booked, set horizon override, build a booking record.

    Returns (slot, booking_record_for_jsonl).
    """
    slot = find_slot(state, slot_id)
    if slot["status"] != "open":
        raise ActionError(f"slot {slot_id} is {slot['status']}, cannot book")
    slot["status"] = "booked"
    slot["booked_by"] = booked_by
    slot["booked_at"] = now.isoformat()
    state["horizon_override_until"] = slot["tee_date"]

    booking = {
        "booked_at": now.isoformat(),
        "booked_by": booked_by,
        "course_key": slot["course_key"],
        "tee_date": slot["tee_date"],
        "tee_time": slot["tee_time"],
        "players": slot["players_open"],
        "roster": _compute_roster(slot),
        "undone_at": None,
    }
    return slot, booking


def mark_skipped(state: dict[str, Any], slot_id: str) -> dict[str, Any]:
    slot = find_slot(state, slot_id)
    if slot["status"] != "open":
        raise ActionError(f"slot {slot_id} is {slot['status']}, cannot skip")
    slot["status"] = "skipped"
    return slot


def set_paused(state: dict[str, Any], paused: bool, now: datetime | None) -> None:
    """Toggle the global pause flag.

    `pause_started_at` records when the *current* pause began; re-pausing
    while already paused preserves the original timestamp. Resuming clears it.
    """
    state["paused"] = paused
    if not paused:
        state["pause_started_at"] = None
    elif state.get("pause_started_at") is None and now is not None:
        state["pause_started_at"] = now.isoformat()


def undo_booking(
    state: dict[str, Any],
    slot_id: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reverse a booking. Returns (slot, undo_record_for_jsonl).

    Clears the horizon override and flips the slot status back to open so it
    can be re-acted-on (booked again, skipped, expired).
    """
    slot = find_slot(state, slot_id)
    if slot["status"] != "booked":
        raise ActionError(f"slot {slot_id} is {slot['status']}, cannot undo")
    slot["status"] = "open"
    slot.pop("booked_by", None)
    slot.pop("booked_at", None)
    state["horizon_override_until"] = None

    undo_record = {
        "undone_at": now.isoformat(),
        "course_key": slot["course_key"],
        "tee_date": slot["tee_date"],
        "tee_time": slot["tee_time"],
    }
    return slot, undo_record


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _compute_roster(slot: dict[str, Any]) -> dict[str, list[str]]:
    votes = slot.get("votes", {})
    return {
        "yes": sorted(n for n, v in votes.items() if v["vote"] == "yes"),
        "no":  sorted(n for n, v in votes.items() if v["vote"] == "no"),
    }
