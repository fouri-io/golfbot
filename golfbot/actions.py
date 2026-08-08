"""Pure state mutations — the Gold Star dedup + re-alert ledger.

Take a `state` dict + inputs, mutate it in place, return what the caller
needs. No I/O, no Telegram calls — the caller (bot.py / mock_source.py)
handles persistence and external effects.

Slots inside `state["tee_times"]` are stored as their dict form
(`TeeTimeSlot.to_dict()`). This module operates at the dict level so it
doesn't depend on the dataclass.

v2: voting, booking, and skip actions are gone (the group does all of that
offline). What remains is the ledger that decides whether a qualifying slot
is worth alerting about — see docs/SPEC.md > Re-alert semantics.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


class ActionError(Exception):
    """Caller-visible error: tried to do something invalid."""


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


# --------------------------------------------------------------------------- #
# The re-alert ledger                                                          #
# --------------------------------------------------------------------------- #


def upsert_slot(
    state: dict[str, Any],
    slot_dict: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """Add a slot if its id is new, or refresh an existing one in place.

    Refreshing updates `last_seen_at` and `spots_open` but preserves
    `first_seen_at` and the alert bookkeeping.

    Returns (slot_in_state, is_new).
    """
    for existing in state["tee_times"]:
        if existing["id"] == slot_dict["id"]:
            existing["last_seen_at"] = now.isoformat()
            existing["spots_open"] = slot_dict["spots_open"]
            existing["booking_url"] = slot_dict["booking_url"]
            return existing, False
    state["tee_times"].append(slot_dict)
    return slot_dict, True


def should_alert(slot: dict[str, Any]) -> bool:
    """Whether this slot warrants a push right now.

    docs/SPEC.md > Re-alert semantics:
      - never alerted        -> alert
      - MORE spots than last alerted -> re-alert (better opportunity)
      - equal or fewer spots -> stay silent
    """
    last = slot.get("last_alerted_spots")
    if last is None:
        return True
    return slot["spots_open"] > last


def record_alert(slot: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Stamp the slot as alerted at its current spot count."""
    slot["last_alerted_spots"] = slot["spots_open"]
    slot["last_alerted_at"] = now.isoformat()
    return slot


def prune_past_slots(state: dict[str, Any], today: date) -> int:
    """Drop slots whose tee date has passed. Returns how many were removed.

    SPEC: expired slots are pruned silently — there's nothing to act on.
    """
    keep = [s for s in state["tee_times"] if date.fromisoformat(s["tee_date"]) >= today]
    removed = len(state["tee_times"]) - len(keep)
    state["tee_times"] = keep
    return removed


# --------------------------------------------------------------------------- #
# Pause flag                                                                   #
# --------------------------------------------------------------------------- #


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
