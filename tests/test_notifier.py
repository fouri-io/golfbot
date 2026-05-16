"""Tests for golfbot.notifier (pure rendering only; Telegram API not exercised)."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

from golfbot.models import TeeTimeSlot, Vote
from golfbot.config import load
from golfbot.notifier import (
    _fmt_clock,
    _fmt_date,
    _fmt_time,
    build_keyboard_booked,
    build_keyboard_open,
    render_booked,
    render_expired,
    render_open,
    render_skipped,
    render_status,
)

from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent

MEMBERS = ["Colby", "Steve", "Ed"]


@pytest.fixture
def slot() -> TeeTimeSlot:
    return TeeTimeSlot(
        id="roy_kizer:2026-05-23:0800:4",
        course_key="roy_kizer",
        tee_date=date(2026, 5, 23),
        tee_time=time(8, 0),
        players_open=4,
        holes=18,
        grade="A",
        booking_url="https://example.com/book",
        first_seen_at=datetime(2026, 5, 15, 17, 0),
        last_seen_at=datetime(2026, 5, 15, 17, 0),
        status="open",
    )


# ---------- formatting helpers ----------


@pytest.mark.parametrize("d,expected", [
    (date(2026, 5, 23), "Sat May 23"),
    (date(2026, 5, 1),  "Fri May 1"),
    (date(2026, 12, 31), "Thu Dec 31"),
])
def test_fmt_date(d, expected):
    assert _fmt_date(d) == expected


@pytest.mark.parametrize("t,expected", [
    (time(8, 0),  "8:00 AM"),
    (time(7, 30), "7:30 AM"),
    (time(12, 0), "12:00 PM"),
    (time(0, 15), "12:15 AM"),
    (time(14, 5), "2:05 PM"),
])
def test_fmt_time(t, expected):
    assert _fmt_time(t) == expected


def test_fmt_clock():
    assert _fmt_clock(datetime(2026, 5, 15, 14, 14)) == "2:14 PM"


# ---------- render_open ----------


def test_render_open_no_votes(slot):
    out = render_open(slot, "Roy Kizer", MEMBERS)
    assert "🏌️ Tee Time Found — Grade A" in out
    assert "Roy Kizer · Sat May 23" in out
    assert "8:00 AM · 4 players · 18 holes" in out
    # Empty buckets are "—"; all members waiting.
    assert "✅ Yes (0): —" in out
    assert "❌ No (0):  —" in out
    assert "⏳ Waiting:   Colby, Ed, Steve" in out


def test_render_open_with_votes(slot):
    now = datetime(2026, 5, 15, 17, 5)
    slot.votes = {
        "Colby": Vote(vote="yes", voted_at=now),
        "Steve": Vote(vote="yes", voted_at=now),
        "Ed":    Vote(vote="no",  voted_at=now),
    }
    out = render_open(slot, "Roy Kizer", MEMBERS)
    assert "✅ Yes (2): Colby, Steve" in out
    assert "❌ No (1):  Ed" in out
    assert "⏳ Waiting:   —" in out


def test_render_open_partial_votes(slot):
    now = datetime(2026, 5, 15, 17, 5)
    slot.votes = {"Colby": Vote(vote="yes", voted_at=now)}
    out = render_open(slot, "Roy Kizer", MEMBERS)
    assert "✅ Yes (1): Colby" in out
    assert "❌ No (0):  —" in out
    assert "⏳ Waiting:   Ed, Steve" in out


# ---------- render_booked ----------


def test_render_booked_shows_roster_and_timestamp(slot):
    now = datetime(2026, 5, 15, 17, 5)
    slot.votes = {
        "Colby": Vote(vote="yes", voted_at=now),
        "Steve": Vote(vote="yes", voted_at=now),
        "Ed":    Vote(vote="no",  voted_at=now),
    }
    out = render_booked(slot, "Roy Kizer", "Colby", datetime(2026, 5, 15, 14, 14))
    assert "🏌️ BOOKED ✅" in out
    assert "✅ Yes: Colby, Steve" in out
    assert "❌ No:  Ed" in out
    assert "Booked by Colby at 2:14 PM" in out
    assert "Notifications paused through Sat May 23." in out


def test_render_booked_empty_no_voters(slot):
    out = render_booked(slot, "Roy Kizer", "Colby", datetime(2026, 5, 15, 14, 14))
    assert "✅ Yes: —" in out
    assert "❌ No:  —" in out


# ---------- render_expired ----------


def test_render_expired(slot):
    out = render_expired(slot, "Roy Kizer")
    assert out == "⌛ Expired — Roy Kizer tee time was Sat May 23, 8:00 AM"


# ---------- keyboards ----------


def test_keyboard_open_layout(slot):
    kb = build_keyboard_open(slot)
    rows = kb.inline_keyboard
    assert len(rows) == 3
    # Row 1: URL button only
    assert len(rows[0]) == 1
    assert rows[0][0].url == "https://example.com/book"
    # Row 2: Yes/No
    assert [b.text for b in rows[1]] == ["✅ Yes", "❌ No"]
    assert [b.callback_data for b in rows[1]] == [
        f"yes:{slot.id}", f"no:{slot.id}",
    ]
    # Row 3: admin actions
    assert [b.text for b in rows[2]] == ["📖 Booked it", "🚫 Skip", "🔕 Pause"]
    assert [b.callback_data for b in rows[2]] == [
        f"book:{slot.id}", f"skip:{slot.id}", f"pause:{slot.id}",
    ]


def test_keyboard_booked_only_undo(slot):
    kb = build_keyboard_booked(slot)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "↩️ Undo"
    assert btn.callback_data == f"undo:{slot.id}"


def test_callback_data_fits_telegram_limit(slot):
    """Telegram enforces a 64-byte callback_data limit."""
    kb = build_keyboard_open(slot)
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data is not None:
                assert len(btn.callback_data.encode("utf-8")) <= 64


# ---------- render_skipped ----------


def test_render_skipped(slot):
    out = render_skipped(slot, "Roy Kizer")
    assert out == "🚫 Skipped — Roy Kizer · Sat May 23, 8:00 AM"


# ---------- render_status ----------


def test_render_status_default(monkeypatch):
    from golfbot.store import default_state
    cfg = load(REPO_ROOT / "config.yaml")
    state = default_state()
    out = render_status(state, cfg, today=date(2026, 5, 15))
    assert "Roy Kizer" in out
    assert "Jimmy Clay" in out
    assert "Riverside" in out
    assert "Grey Rock" in out
    assert "Sat May 16 → Fri May 22 (7 days)" in out
    assert "🎯 Days: Mon, Tue, Wed, Thu, Fri" in out
    assert "⏰ Ideal: 7:30 AM–8:00 AM" in out
    assert "Acceptable: 7:00 AM–9:00 AM" in out
    assert "📌 Bookings: — (none)" in out
    assert "🔔 Notifications: ON" in out


def test_render_digest_with_matches():
    from golfbot.notifier import render_digest
    cfg = load(REPO_ROOT / "config.yaml")
    matches = [
        {
            "course_key": "roy_kizer",
            "course_display": "Roy Kizer",
            "course_tier": 1,
            "tee_date": "2026-05-18",
            "tee_time": "07:30:00",
            "grade": "A",
            "players_available": 3,
            "holes": 18,
            "booking_url": "https://example.com/book/1",
            "price_usd": None,
            "provider": "golfatx",
        },
        {
            "course_key": "riverside",
            "course_display": "Riverside",
            "course_tier": 1,
            "tee_date": "2026-05-19",
            "tee_time": "07:30:00",
            "grade": "A",
            "players_available": 3,
            "holes": 18,
            "booking_url": "https://example.com/book/2",
            "price_usd": 45.0,
            "provider": "golfnow",
        },
    ]
    out = render_digest(
        matches, datetime(2026, 5, 16, 11, 30), None, cfg
    )
    assert "🏌️" in out
    assert "11:30 AM" in out
    assert "2 available matches" in out
    assert "Roy Kizer" in out
    assert "Riverside" in out
    assert "$45" in out
    assert '<a href="https://example.com/book/1">book</a>' in out
    assert "/tee" in out


def test_render_digest_no_matches():
    from golfbot.notifier import render_digest
    cfg = load(REPO_ROOT / "config.yaml")
    out = render_digest([], datetime(2026, 5, 16, 11, 30), None, cfg)
    assert "No matches" in out
    assert "11:30 AM" in out


def test_render_digest_escapes_html():
    """Course names with HTML special chars must be escaped."""
    from golfbot.notifier import render_digest
    cfg = load(REPO_ROOT / "config.yaml")
    matches = [{
        "course_key": "x", "course_display": "Course <evil> &",
        "course_tier": 1, "tee_date": "2026-05-18", "tee_time": "07:30:00",
        "grade": "A", "players_available": 3, "holes": 18,
        "booking_url": "https://example.com", "price_usd": None,
        "provider": "golfatx",
    }]
    out = render_digest(matches, datetime(2026, 5, 16, 11, 30), None, cfg)
    assert "Course &lt;evil&gt; &amp;" in out
    assert "<evil>" not in out


def test_render_digest_with_bookings():
    from golfbot.notifier import render_digest
    cfg = load(REPO_ROOT / "config.yaml")
    matches = [
        {
            "course_key": "roy_kizer", "course_display": "Roy Kizer",
            "course_tier": 1, "tee_date": "2026-05-18", "tee_time": "07:30:00",
            "grade": "A", "players_available": 3, "holes": 18,
            "booking_url": "https://x/1", "price_usd": None,
            "provider": "golfatx", "members_in": ["Colby"], "members_out": [],
        },
        {
            "course_key": "riverside", "course_display": "Riverside",
            "course_tier": 1, "tee_date": "2026-05-20", "tee_time": "07:30:00",
            "grade": "A", "players_available": 3, "holes": 18,
            "booking_url": "https://x/2", "price_usd": 45.0,
            "provider": "golfnow", "members_in": ["Colby"], "members_out": [],
        },
    ]
    bookings = {
        date(2026, 5, 18): {
            **matches[0],
            "booked_at": "2026-05-16T12:00:00",
            "booked_by": "Colby",
        }
    }
    out = render_digest(matches, datetime(2026, 5, 16, 11, 30), None, cfg, bookings=bookings)
    # Booking section appears
    assert "📌 BOOKED" in out
    # Roy Kizer is in the booked section
    assert "Roy Kizer" in out
    # Roy Kizer should NOT appear in "available matches" list
    available_section = out.split("available match")[1] if "available match" in out else ""
    assert "Roy Kizer" not in available_section
    # Riverside should be visible
    assert "Riverside" in out


def test_build_digest_keyboard_confirm_and_cancel():
    from golfbot.notifier import build_digest_keyboard
    matches = [
        {
            "course_key": "roy_kizer", "course_display": "Roy Kizer",
            "course_tier": 1, "tee_date": "2026-05-18", "tee_time": "07:30:00",
            "grade": "A", "players_available": 3, "holes": 18,
            "booking_url": "https://x/1", "price_usd": None,
            "provider": "golfatx",
        },
        {
            "course_key": "riverside", "course_display": "Riverside",
            "course_tier": 1, "tee_date": "2026-05-20", "tee_time": "07:30:00",
            "grade": "A", "players_available": 3, "holes": 18,
            "booking_url": "https://x/2", "price_usd": 45.0,
            "provider": "golfnow",
        },
    ]
    bookings = {
        date(2026, 5, 18): {
            **matches[0],
            "booked_at": "2026-05-16T12:00:00", "booked_by": "Colby",
        }
    }
    kb = build_digest_keyboard(matches, bookings)
    # Should have one cancel row and one confirm row (Riverside only —
    # Roy Kizer is filtered out as already booked).
    all_btns = [b for row in kb.inline_keyboard for b in row]
    cancel_btns = [b for b in all_btns if b.callback_data.startswith("cx:")]
    confirm_btns = [b for b in all_btns if b.callback_data.startswith("cn:")]
    assert len(cancel_btns) == 1
    assert cancel_btns[0].callback_data == "cx:2026-05-18"
    assert len(confirm_btns) == 1
    assert confirm_btns[0].callback_data.startswith("cn:riverside:2026-05-20:")


def test_build_digest_keyboard_no_bookings_yet():
    from golfbot.notifier import build_digest_keyboard
    matches = [{
        "course_key": "roy_kizer", "course_display": "Roy Kizer",
        "course_tier": 1, "tee_date": "2026-05-18", "tee_time": "07:30:00",
        "grade": "A", "players_available": 3, "holes": 18,
        "booking_url": "https://x/1", "price_usd": None, "provider": "golfatx",
    }]
    kb = build_digest_keyboard(matches, {})
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert all(b.callback_data.startswith("cn:") for b in all_btns)


def test_build_digest_keyboard_callback_data_under_64_bytes():
    from golfbot.notifier import build_digest_keyboard
    matches = [{
        "course_key": "grey_rock_golf_club", "course_display": "Grey Rock Golf Club",
        "course_tier": 1, "tee_date": "2026-05-18", "tee_time": "07:30:00",
        "grade": "A", "players_available": 3, "holes": 18,
        "booking_url": "https://x/1", "price_usd": None, "provider": "golfnow",
    }]
    kb = build_digest_keyboard(matches, {})
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64


def test_render_digest_next_run_footer():
    from golfbot.notifier import render_digest
    cfg = load(REPO_ROOT / "config.yaml")
    out = render_digest(
        [], datetime(2026, 5, 16, 11, 30), datetime(2026, 5, 16, 12, 30), cfg,
    )
    assert "Next scan:" in out


def test_render_status_paused_and_booked():
    from golfbot.store import default_state
    cfg = load(REPO_ROOT / "config.yaml")
    state = default_state()
    state["paused"] = True
    state["horizon_override_until"] = "2026-05-23"
    state["tee_times"].append({
        "id": "roy_kizer:2026-05-23:0800:4",
        "course_key": "roy_kizer",
        "tee_date": "2026-05-23",
        "tee_time": "08:00:00",
        "status": "booked",
    })
    out = render_status(state, cfg, today=date(2026, 5, 15))
    # Horizon shifts past the booking
    assert "Sun May 24 → Sat May 30" in out
    assert "📌 Bookings: roy_kizer · Sat May 23, 8:00 AM" in out
    assert "🔔 Notifications: OFF (paused)" in out
