"""Gold Star qualification: which RawSlots are worth pinging the group about.

Pure functions, no I/O — same input always produces the same output, easy
to unit-test. The notifier consumes Match objects produced here; the
pipeline doesn't know about Telegram or state.

Reused by both the `scrape` preview CLI and the scheduled scan so they
behave identically.

The v2 rule (docs/SPEC.md > The Gold Star rule) is a single conjunction:

    all-star course  AND  premium window  AND  weekday  AND  >=1 spot open

No grades, no tiers, no player-count matching — the premium window is the
entire quality bar. Replaces v1's two-axis grading and Policy B
best-per-course-date selection (docs/decisions/0006-gold-star-pivot.md,
superseding ADR 0003).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from golfbot.config import Config
from golfbot.providers.base import RawSlot

# weekday() index: Monday = 0
_DAY_INDEX: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass(frozen=True)
class Match:
    """A slot that cleared the Gold Star bar.

    Carries the original RawSlot plus the display name the notifier needs.
    v2 has no roster: the bot no longer tracks who is in or out.
    """
    raw: RawSlot
    course_display: str


# --------------------------------------------------------------------------- #
# The Gold Star rule                                                          #
# --------------------------------------------------------------------------- #


def is_desired_day(d: date, days_of_week: list[str]) -> bool:
    wanted = {_DAY_INDEX[name] for name in days_of_week}
    return d.weekday() in wanted


def in_premium_window(slot: RawSlot, cfg: Config) -> bool:
    """Inclusive on both ends — a slot exactly at the boundary counts."""
    w = cfg.premium_window
    return w.start <= slot.tee_time <= w.end


def qualifies(slot: RawSlot, cfg: Config) -> bool:
    """True if this slot is a Gold Star.

    Every condition must hold. Order is cheapest-first; the course lookup
    also filters out slots for courses that aren't configured at all.
    """
    course = cfg.course_by_key(slot.course_key)
    if course is None or not course.all_star:
        return False
    if not is_desired_day(slot.tee_date, cfg.search.days_of_week):
        return False
    if not in_premium_window(slot, cfg):
        return False
    return slot.players_available >= 1


def gold_star_slots(slots: list[RawSlot], cfg: Config) -> list[Match]:
    """Keep only the slots that clear the Gold Star bar.

    Every configured course is scanned so `/full` can show the complete
    picture; only all-star courses survive this filter and can alert
    (docs/SPEC.md > Scan all, alert on four).

    No per-course-date collapsing: each qualifying slot is its own Match.
    Dedup and re-alert-on-more-spots are handled by the state ledger, not
    here.
    """
    out: list[Match] = []
    for s in slots:
        if not qualifies(s, cfg):
            continue
        course = cfg.course_by_key(s.course_key)
        assert course is not None   # qualifies() already rejected unknown keys
        out.append(Match(raw=s, course_display=course.display))
    return out
