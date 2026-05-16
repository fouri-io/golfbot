"""Tests for golfbot.scanner — digest dedup logic, no network."""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.pipeline import Match
from golfbot.providers.base import RawSlot
from golfbot.scanner import _signature, match_to_dict

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


def _match(course="roy_kizer", display="Roy Kizer", tier=1, d=date(2026, 5, 18),
           t=time(7, 30), players=3, grade="A") -> Match:
    return Match(
        raw=RawSlot(
            course_key=course,
            tee_date=d,
            tee_time=t,
            players_available=players,
            holes=18,
            booking_url="https://example.com/book/" + course,
            provider="golfatx",
            price_usd=None,
        ),
        grade=grade,
        course_display=display,
        course_tier=tier,
    )


def test_match_to_dict_roundtrip(cfg):
    m = _match()
    d = match_to_dict(m)
    assert d["course_key"] == "roy_kizer"
    assert d["course_display"] == "Roy Kizer"
    assert d["tee_date"] == "2026-05-18"
    assert d["tee_time"] == "07:30:00"
    assert d["grade"] == "A"
    assert d["players_available"] == 3
    assert d["booking_url"].startswith("https://")


def test_signature_identical_match_sets():
    a = [match_to_dict(_match())]
    b = [match_to_dict(_match())]
    assert _signature(a) == _signature(b)


def test_signature_detects_added_match():
    a = [match_to_dict(_match())]
    b = a + [match_to_dict(_match(d=date(2026, 5, 19)))]
    assert _signature(a) != _signature(b)


def test_signature_detects_removed_match():
    a = [match_to_dict(_match()), match_to_dict(_match(d=date(2026, 5, 19)))]
    b = a[:1]
    assert _signature(a) != _signature(b)


def test_signature_detects_players_count_change():
    """A slot dropping from 4 open to 3 open should count as a change —
    someone booked one seat, worth re-notifying."""
    a = [match_to_dict(_match(players=4))]
    b = [match_to_dict(_match(players=3))]
    assert _signature(a) != _signature(b)


def test_signature_ignores_grade_change():
    """Hypothetical: same slot regrades. We don't care for dedup purposes
    — slot identity is the same. (Wouldn't happen in practice since grade
    is derived from time which is part of the signature.)"""
    a = [match_to_dict(_match(grade="A"))]
    b = [match_to_dict(_match(grade="B"))]
    assert _signature(a) == _signature(b)


def test_signature_empty_lists_equal():
    assert _signature([]) == _signature([])
