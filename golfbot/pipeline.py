"""Filter + grade RawSlots into notifiable Matches.

Pure functions, no I/O — same input always produces the same output, easy
to unit-test. The notifier consumes Match objects produced here; the
pipeline doesn't know about Telegram or state.

Reused by both the `scrape` preview CLI (P2.5) and the scheduled poller
(P3) so they behave identically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from golfbot.config import Config
from golfbot.grading import grade as grade_fn
from golfbot.grading import meets_threshold
from golfbot.providers.base import RawSlot

# weekday() index: Monday = 0
_DAY_INDEX: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

_GRADE_RANK: dict[str, int] = {"A": 3, "B": 2, "C": 1}


@dataclass(frozen=True)
class Match:
    """A slot that passed all filters with a grade attached.

    Carries the original RawSlot plus the bits the notifier needs.
    `members_in` / `members_out` are populated by the scanner once
    availability is known; they're empty for callers that don't compute
    them (e.g. `scrape --raw`).
    """
    raw: RawSlot
    grade: str           # "A" | "B" | "C"
    course_display: str
    course_tier: int
    members_in: tuple[str, ...] = ()
    members_out: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Filter steps                                                                #
# --------------------------------------------------------------------------- #


def is_desired_day(d: date, days_of_week: list[str]) -> bool:
    wanted = {_DAY_INDEX[name] for name in days_of_week}
    return d.weekday() in wanted


def filter_and_grade(slots: list[RawSlot], cfg: Config) -> list[Match]:
    """Apply time-window and grading filters in order.

    Drops slots that:
      - have a course_key not in cfg.courses
      - are outside the acceptable time window
      - grade below the notify_min_grade threshold

    Day-of-week filtering is no longer applied here — per-member weekly
    availability patterns (in `availability.AvailabilityRecord`) handle
    that more flexibly. The `cfg.search.days_of_week` field is now
    advisory (kept for backward compat with old configs).
    """
    by_key = {c.key: c for c in cfg.courses}
    ideal = cfg.time_windows.ideal
    acceptable = cfg.time_windows.acceptable
    min_grade = cfg.grading.notify_min_grade

    out: list[Match] = []
    for s in slots:
        course = by_key.get(s.course_key)
        if course is None:
            continue
        g = grade_fn(course.tier, s.tee_time, ideal, acceptable)
        if g is None or not meets_threshold(g, min_grade):
            continue
        out.append(Match(
            raw=s,
            grade=g,
            course_display=course.display,
            course_tier=course.tier,
        ))
    return out


def apply_policy_b(matches: list[Match]) -> list[Match]:
    """Best-per-(course, date): keep one Match per pair.

    Tiebreakers: higher grade > earlier tee time.
    """
    best: dict[tuple[str, date], Match] = {}
    for m in matches:
        key = (m.raw.course_key, m.raw.tee_date)
        cur = best.get(key)
        if cur is None or _is_better(m, cur):
            best[key] = m
    return list(best.values())


def _is_better(a: Match, b: Match) -> bool:
    a_rank = _GRADE_RANK[a.grade]
    b_rank = _GRADE_RANK[b.grade]
    if a_rank != b_rank:
        return a_rank > b_rank
    return a.raw.tee_time < b.raw.tee_time
