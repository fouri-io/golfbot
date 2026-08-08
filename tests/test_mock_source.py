"""Tests for golfbot.mock_source."""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import golfbot.mock_source as mock_source
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


async def test_inject_passes_headline_to_send(cfg, tmp_path, monkeypatch):
    """Regression: inject() must hand send_new_slot a headline.

    Slice 4 made `headline` a required arg on send_new_slot but left inject()
    calling it the old way; the CLI blew up with a TypeError at runtime. No
    test exercised the inject() call path, so pytest stayed green. This guards
    that seam without touching Telegram or the network.
    """
    captured = {}

    async def fake_send(bot, chat_id, slot, course_display, headline):
        captured.update(course_display=course_display, headline=headline)
        return 4242

    monkeypatch.setattr(mock_source.notifier, "send_new_slot", fake_send)

    fake_bot = MagicMock()
    fake_bot.__aenter__ = AsyncMock(return_value=fake_bot)
    fake_bot.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mock_source, "Bot", lambda token: fake_bot)

    _slot, msg_id = await mock_source.inject(
        cfg, "fake-token", 123, tmp_path / "state.json",
        "jimmy_clay", date(2026, 8, 10), time(7, 40), 3,
    )

    assert msg_id == 4242
    assert captured["course_display"] == "Jimmy Clay"
    assert isinstance(captured["headline"], str) and captured["headline"]
