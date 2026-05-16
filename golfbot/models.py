"""Domain dataclasses: TeeTimeSlot, Vote, Booking.

These mirror the on-disk shape in state.json / bookings.jsonl
(see SPEC.md > Data model). Each class has `from_dict` / `to_dict` so the
store layer stays plain-JSON-only and conversion is explicit at the seam.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


def make_slot_id(course_key: str, tee_date: date, tee_time: time, players_open: int) -> str:
    """Deterministic id: '{course_key}:{YYYY-MM-DD}:{HHMM}:{players}'."""
    return f"{course_key}:{tee_date.isoformat()}:{tee_time.strftime('%H%M')}:{players_open}"


@dataclass
class Vote:
    """A single member's vote on a tee-time notification."""
    vote: str            # "yes" | "no"
    voted_at: datetime

    @classmethod
    def from_dict(cls, d: dict) -> Vote:
        return cls(vote=d["vote"], voted_at=datetime.fromisoformat(d["voted_at"]))

    def to_dict(self) -> dict:
        return {"vote": self.vote, "voted_at": self.voted_at.isoformat()}


@dataclass
class TeeTimeSlot:
    """A specific tee-time slot at a course on a date.

    `id` is a deterministic composite (course:date:time:players) used for
    dedup across polls.
    """
    id: str
    course_key: str
    tee_date: date
    tee_time: time
    players_open: int
    holes: int
    grade: str           # "A" | "B" | "C"
    booking_url: str
    first_seen_at: datetime
    last_seen_at: datetime
    status: str          # "open" | "booked" | "skipped" | "expired"
    message_id: int | None = None
    votes: dict[str, Vote] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> TeeTimeSlot:
        return cls(
            id=d["id"],
            course_key=d["course_key"],
            tee_date=date.fromisoformat(d["tee_date"]),
            tee_time=time.fromisoformat(d["tee_time"]),
            players_open=d["players_open"],
            holes=d["holes"],
            grade=d["grade"],
            booking_url=d["booking_url"],
            first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(d["last_seen_at"]),
            status=d["status"],
            message_id=d.get("message_id"),
            votes={n: Vote.from_dict(v) for n, v in d.get("votes", {}).items()},
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "course_key": self.course_key,
            "tee_date": self.tee_date.isoformat(),
            "tee_time": self.tee_time.isoformat(),
            "players_open": self.players_open,
            "holes": self.holes,
            "grade": self.grade,
            "booking_url": self.booking_url,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "status": self.status,
            "message_id": self.message_id,
            "votes": {n: v.to_dict() for n, v in self.votes.items()},
        }


@dataclass
class Booking:
    """An admin-confirmed booking (mirrors a line in bookings.jsonl)."""
    booked_at: datetime
    booked_by: str
    course_key: str
    tee_date: date
    tee_time: time
    players: int
    roster: dict[str, list[str]]   # {"yes": [...], "no": [...]}
    undone_at: datetime | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Booking:
        return cls(
            booked_at=datetime.fromisoformat(d["booked_at"]),
            booked_by=d["booked_by"],
            course_key=d["course_key"],
            tee_date=date.fromisoformat(d["tee_date"]),
            tee_time=time.fromisoformat(d["tee_time"]),
            players=d["players"],
            roster=d.get("roster", {"yes": [], "no": []}),
            undone_at=datetime.fromisoformat(d["undone_at"]) if d.get("undone_at") else None,
        )

    def to_dict(self) -> dict:
        return {
            "booked_at": self.booked_at.isoformat(),
            "booked_by": self.booked_by,
            "course_key": self.course_key,
            "tee_date": self.tee_date.isoformat(),
            "tee_time": self.tee_time.isoformat(),
            "players": self.players,
            "roster": self.roster,
            "undone_at": self.undone_at.isoformat() if self.undone_at else None,
        }
