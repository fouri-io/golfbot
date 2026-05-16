"""Tests for golfbot.grading."""
from __future__ import annotations

from datetime import time

import pytest

from golfbot.config import TimeWindow
from golfbot.grading import grade, meets_threshold

IDEAL = TimeWindow(start=time(7, 30), end=time(8, 0))
ACCEPTABLE = TimeWindow(start=time(7, 0), end=time(9, 0))


# ---------- core grading rules ----------


@pytest.mark.parametrize(
    "tier,slot,expected",
    [
        # Tier 1 in ideal → A
        (1, time(7, 30), "A"),
        (1, time(7, 45), "A"),
        (1, time(8, 0),  "A"),    # boundary inclusive
        # Tier 1 in acceptable but not ideal → B
        (1, time(7, 0),  "B"),    # boundary inclusive (start of acceptable)
        (1, time(7, 15), "B"),
        (1, time(8, 30), "B"),
        (1, time(9, 0),  "B"),    # boundary inclusive (end of acceptable)
        # Tier 2 in ideal → B
        (2, time(7, 30), "B"),
        (2, time(8, 0),  "B"),
        # Tier 2 in acceptable but not ideal → C
        (2, time(7, 0),  "C"),
        (2, time(8, 30), "C"),
        (2, time(9, 0),  "C"),
        # Outside acceptable → None regardless of tier
        (1, time(6, 30), None),
        (1, time(9, 30), None),
        (2, time(6, 0),  None),
        # Tier 3+: no rule → None
        (3, time(7, 30), None),
    ],
)
def test_grade_matrix(tier, slot, expected):
    assert grade(tier, slot, IDEAL, ACCEPTABLE) == expected


# ---------- threshold helper ----------


@pytest.mark.parametrize(
    "g,min_g,expected",
    [
        ("A", "A", True),
        ("A", "B", True),
        ("A", "C", True),
        ("B", "A", False),
        ("B", "B", True),
        ("B", "C", True),
        ("C", "A", False),
        ("C", "B", False),
        ("C", "C", True),
    ],
)
def test_meets_threshold(g, min_g, expected):
    assert meets_threshold(g, min_g) is expected
