"""Provider abstraction.

Each booking-system integration (GolfNow, GolfATX/WebTrac, future others)
implements `Provider.fetch_slots(...)` returning a normalized `RawSlot`
list. The pipeline doesn't know which provider produced what; it only
sees `RawSlot`s.

See SPEC.md > providers (P2+).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Protocol

from golfbot.config import Course


@dataclass(frozen=True)
class RawSlot:
    """A normalized tee-time slot returned by any provider.

    `players_available` is a **lower bound** — the value we queried with.
    Some providers (GolfNow) don't expose how many seats are open per slot,
    only that the slot accepts the requested party size or more.
    """
    course_key: str
    tee_date: date
    tee_time: time
    players_available: int
    holes: int
    booking_url: str
    provider: str
    price_usd: float | None = None
    extra: dict = field(default_factory=dict)   # provider-specific debug


class Provider(Protocol):
    """Stateless coroutine-style provider.

    `fetch_slots` is called once per date in the search horizon. The
    provider filters `courses` to those it owns (matched by `provider`
    field on the Course) before issuing requests.
    """

    name: str

    async def fetch_slots(
        self,
        courses: list[Course],
        target_date: date,
        min_players: int,
    ) -> list[RawSlot]:
        ...
