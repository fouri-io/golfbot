"""Two-axis grading: course tier × time-of-day window.

|  Tier | Ideal | Acceptable | Outside |
|-------|-------|------------|---------|
|   1   |   A   |     B      |   None  |
|   2   |   B   |     C      |   None  |

Tier 3+ courses are not graded (returns None); add explicit rules below if
new tiers are introduced.

Both time windows are checked **inclusive** on both ends: a slot at exactly
the window boundary counts as inside. The "ideal" window is assumed to fit
within "acceptable" (validated in config).

See SPEC.md > Grading.
"""
from __future__ import annotations

from datetime import time

from golfbot.config import TimeWindow

# (tier, window-name) -> grade
_GRADES: dict[tuple[int, str], str] = {
    (1, "ideal"): "A",
    (1, "acceptable"): "B",
    (2, "ideal"): "B",
    (2, "acceptable"): "C",
}

_GRADE_RANK: dict[str, int] = {"A": 3, "B": 2, "C": 1}


def _in_window(t: time, w: TimeWindow) -> bool:
    return w.start <= t <= w.end


def _classify_time(t: time, ideal: TimeWindow, acceptable: TimeWindow) -> str | None:
    """Return 'ideal' | 'acceptable' | None."""
    if _in_window(t, ideal):
        return "ideal"
    if _in_window(t, acceptable):
        return "acceptable"
    return None


def grade(
    tier: int,
    slot_time: time,
    ideal: TimeWindow,
    acceptable: TimeWindow,
) -> str | None:
    """Grade a single tee-time slot. Returns 'A' | 'B' | 'C' | None.

    None means "do not notify" — either time is outside both windows, or
    the tier has no grading rule.
    """
    bucket = _classify_time(slot_time, ideal, acceptable)
    if bucket is None:
        return None
    return _GRADES.get((tier, bucket))


def meets_threshold(g: str, min_grade: str) -> bool:
    """True if `g` is at least as good as `min_grade` (A > B > C)."""
    return _GRADE_RANK[g] >= _GRADE_RANK[min_grade]
