"""Tests for golfbot.mock_source (pure construction only)."""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.mock_source import build_mock_slot

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


def test_build_mock_slot(cfg):
    now = datetime(2026, 5, 15, 17, 0)
    slot = build_mock_slot(cfg, "roy_kizer", date(2026, 5, 23), time(7, 40), 4, now)
    assert slot.id == "roy_kizer:2026-05-23:0740"
    assert slot.course_key == "roy_kizer"
    assert slot.tee_date == date(2026, 5, 23)
    assert slot.tee_time == time(7, 40)
    assert slot.spots_open == 4
    assert slot.holes == cfg.search.holes
    assert slot.first_seen_at == now == slot.last_seen_at
    assert slot.booking_url.startswith("https://")


def test_build_mock_slot_starts_unalerted(cfg):
    """A fresh mock slot must look never-alerted, so should_alert() fires."""
    slot = build_mock_slot(
        cfg, "roy_kizer", date(2026, 5, 23), time(7, 40), 4,
        datetime(2026, 5, 15, 17, 0),
    )
    assert slot.last_alerted_spots is None
    assert slot.last_alerted_at is None


def test_build_mock_slot_unknown_course_raises(cfg):
    with pytest.raises(ValueError, match="unknown course 'pebble_beach'"):
        build_mock_slot(
            cfg, "pebble_beach", date(2026, 5, 23), time(7, 40), 4,
            datetime(2026, 5, 15, 17, 0),
        )
