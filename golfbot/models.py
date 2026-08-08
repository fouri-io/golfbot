"""Domain dataclasses: TeeTimeSlot.

Mirrors the on-disk shape in state.json (see docs/SPEC.md > Data model).
`from_dict` / `to_dict` keep the store layer plain-JSON-only so conversion
is explicit at the seam.

v2 (Gold Star pivot): `Vote` and `Booking` are gone — the group votes and
books offline, so the bot carries neither. `TeeTimeSlot` is now purely a
dedup + re-alert ledger entry (docs/decisions/0006-gold-star-pivot.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


def make_slot_id(course_key: str, tee_date: date, tee_time: time) -> str:
    """Deterministic id: '{course_key}:{YYYY-MM-DD}:{HHMM}'.

    Player count is deliberately NOT part of the id: a slot is one record
    across polls regardless of how many spots are open, so a slot going
    from 2 spots to 3 is the *same* slot with more availability, not a new
    one (docs/SPEC.md > Re-alert semantics).
    """
    return f"{course_key}:{tee_date.isoformat()}:{tee_time.strftime('%H%M')}"


@dataclass
class TeeTimeSlot:
    """A qualifying tee-time slot tracked for dedup and re-alerting.

    `last_alerted_spots` is what drives the re-alert rule: we ping again
    only when the slot reappears with MORE open spots than we last
    announced. `None` means it has never been alerted.
    """
    id: str
    course_key: str
    tee_date: date
    tee_time: time
    spots_open: int
    holes: int
    booking_url: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_alerted_spots: int | None = None
    last_alerted_at: datetime | None = None

    @classmethod
    def from_dict(cls, d: dict) -> TeeTimeSlot:
        alerted_at = d.get("last_alerted_at")
        return cls(
            id=d["id"],
            course_key=d["course_key"],
            tee_date=date.fromisoformat(d["tee_date"]),
            tee_time=time.fromisoformat(d["tee_time"]),
            spots_open=d["spots_open"],
            holes=d["holes"],
            booking_url=d["booking_url"],
            first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(d["last_seen_at"]),
            last_alerted_spots=d.get("last_alerted_spots"),
            last_alerted_at=datetime.fromisoformat(alerted_at) if alerted_at else None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "course_key": self.course_key,
            "tee_date": self.tee_date.isoformat(),
            "tee_time": self.tee_time.isoformat(),
            "spots_open": self.spots_open,
            "holes": self.holes,
            "booking_url": self.booking_url,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "last_alerted_spots": self.last_alerted_spots,
            "last_alerted_at": (
                self.last_alerted_at.isoformat() if self.last_alerted_at else None
            ),
        }
