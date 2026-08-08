"""Tests for golfbot.notifier (pure rendering only; Telegram API not exercised)."""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.models import TeeTimeSlot
from golfbot.notifier import (
    _fmt_clock,
    _fmt_date,
    _fmt_time,
    build_keyboard_open,
    render_digest,
    render_full_listing,
    render_open,
    render_status,
)
from golfbot.providers.base import RawSlot
from golfbot.store import default_state

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


@pytest.fixture
def slot() -> TeeTimeSlot:
    return TeeTimeSlot(
        id="roy_kizer:2026-05-23:0740",
        course_key="roy_kizer",
        tee_date=date(2026, 5, 23),
        tee_time=time(7, 40),
        spots_open=4,
        holes=18,
        booking_url="https://example.com/book",
        first_seen_at=datetime(2026, 5, 15, 17, 0),
        last_seen_at=datetime(2026, 5, 15, 17, 0),
    )


def _match(course_key="roy_kizer", display="Roy Kizer", d="2026-05-18",
           t="07:40:00", spots=3, price=None, url="https://example.com/book/1") -> dict:
    return {
        "course_key": course_key,
        "course_display": display,
        "tee_date": d,
        "tee_time": t,
        "players_available": spots,
        "holes": 18,
        "booking_url": url,
        "price_usd": price,
        "provider": "golfatx",
    }


# ---------- formatting helpers ----------


@pytest.mark.parametrize("d,expected", [
    (date(2026, 5, 23), "Sat May 23"),
    (date(2026, 5, 1), "Fri May 1"),
])
def test_fmt_date(d, expected):
    assert _fmt_date(d) == expected


@pytest.mark.parametrize("t,expected", [
    (time(8, 0), "8:00 AM"),
    (time(7, 40), "7:40 AM"),
    (time(13, 5), "1:05 PM"),
    (time(12, 0), "12:00 PM"),
])
def test_fmt_time(t, expected):
    assert _fmt_time(t) == expected


def test_fmt_clock():
    assert _fmt_clock(datetime(2026, 5, 15, 14, 14)) == "2:14 PM"


# ---------- render_open (the Gold Star alert) ----------


def test_render_open_has_no_vote_tally(slot):
    """v2 removed voting — no Yes/No/Waiting block, no roster."""
    out = render_open(slot, "Roy Kizer")
    for gone in ("✅ Yes", "❌ No", "Waiting", "Availability", "Grade"):
        assert gone not in out


def test_render_open_carries_the_facts(slot):
    out = render_open(slot, "Roy Kizer")
    assert "Roy Kizer" in out
    assert "Sat May 23" in out
    assert "7:40 AM" in out
    assert "4 spots" in out


# ---------- keyboards ----------


def test_keyboard_open_is_a_single_url_button(slot):
    """SPEC v2: the booking link is the only button anywhere."""
    kb = build_keyboard_open(slot)
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert len(rows[0]) == 1
    assert rows[0][0].url == "https://example.com/book"


def test_keyboard_has_no_callback_buttons(slot):
    """A callback button would need a handler; v2 registers none."""
    for row in build_keyboard_open(slot).inline_keyboard:
        for button in row:
            assert button.callback_data is None


# ---------- render_status ----------


def test_render_status_default(cfg):
    out = render_status(default_state(), cfg, today=date(2026, 5, 15))
    assert "Roy Kizer" in out
    assert "Sat May 16 → Fri May 22 (7 days)" in out
    assert "🎯 Days: Mon, Tue, Wed, Thu, Fri" in out
    assert "⏰ Premium: 7:20 AM–8:00 AM" in out
    assert "🔔 Notifications: ON" in out


def test_render_status_lists_the_all_star_set(cfg):
    """SPEC v2 > Telegram commands: /status shows the all-star set."""
    out = render_status(default_state(), cfg, today=date(2026, 5, 15))
    star_line = next(ln for ln in out.splitlines() if ln.startswith("⭐"))
    for name in ("Jimmy Clay", "Roy Kizer", "Riverside", "Grey Rock"):
        assert name in star_line
    assert "Morris Williams" not in star_line   # scanned, but can't alert


def test_render_status_has_no_bookings_line(cfg):
    out = render_status(default_state(), cfg, today=date(2026, 5, 15))
    assert "Bookings" not in out


def test_render_status_paused(cfg):
    state = default_state()
    state["paused"] = True
    out = render_status(state, cfg, today=date(2026, 5, 15))
    assert "Sat May 16 → Fri May 22" in out
    assert "🔔 Notifications: OFF (paused)" in out


# ---------- render_digest ----------


def test_render_digest_with_matches(cfg):
    out = render_digest(
        [_match(), _match("riverside", "Riverside", d="2026-05-19", price=45.0)],
        datetime(2026, 5, 16, 11, 30), None, cfg,
    )
    assert "🏌️" in out
    assert "11:30 AM" in out
    assert "2 slots" in out
    assert "Mon 5/18" in out
    assert "Roy Kizer" in out
    assert "Riverside" in out
    assert "/full" in out


def test_render_digest_no_matches(cfg):
    out = render_digest([], datetime(2026, 5, 16, 11, 30), None, cfg)
    assert "No matches" in out
    assert "11:30 AM" in out


def test_render_digest_singular_slot(cfg):
    out = render_digest([_match()], datetime(2026, 5, 16, 11, 30), None, cfg)
    assert "1 slot" in out
    assert "1 slots" not in out


def test_render_digest_escapes_html(cfg):
    out = render_digest(
        [_match(display="Roy <b>Kizer</b>", url="https://x/?a=1&b=2")],
        datetime(2026, 5, 16, 11, 30), None, cfg,
    )
    assert "Roy &lt;b&gt;Kizer&lt;/b&gt;" in out
    assert "&amp;b=2" in out


def test_render_digest_has_no_roster(cfg):
    """Availability is gone — no 'Colby+Ed (Steve out)' fragments."""
    out = render_digest([_match()], datetime(2026, 5, 16, 11, 30), None, cfg)
    assert "out)" not in out


def test_render_digest_next_run_footer(cfg):
    out = render_digest(
        [], datetime(2026, 5, 16, 11, 30), datetime(2026, 5, 16, 12, 30), cfg,
    )
    assert "Next:" in out
    assert "Last scan:" in out


def test_render_digest_shows_forecast_when_weather_present(cfg):
    out = render_digest(
        [_match()], datetime(2026, 5, 16, 11, 30), None, cfg,
        weather={"2026-05-18": {"tmax": 87.4, "tmin": 66.2, "rain_pct": 12, "code": 1}},
    )
    assert "Forecast" in out
    assert "87°/66°" in out
    assert "Rain 12%" in out


# ---------- render_full_listing ----------


def _raw(course_key, d, t, spots=4, price=None) -> RawSlot:
    return RawSlot(
        course_key=course_key,
        tee_date=d,
        tee_time=t,
        players_available=spots,
        holes=18,
        booking_url="https://x/1",
        provider="golfatx",
        price_usd=price,
    )


def test_render_full_listing_basic(cfg):
    slots = [
        _raw("roy_kizer", date(2026, 5, 18), time(6, 31)),
        _raw("roy_kizer", date(2026, 5, 18), time(7, 30), spots=2),
        _raw("riverside", date(2026, 5, 20), time(14, 30), price=45.0),
    ]
    out = render_full_listing(slots, cfg, datetime(2026, 5, 16, 12, 30))
    assert "All Slots" in out
    assert "Mon 5/18" in out
    assert "Wed 5/20" in out
    assert "Roy Kizer (2):" in out
    assert "3 total slots" in out


def test_render_full_listing_includes_non_all_star_courses(cfg):
    """Scan all, alert on four: /full is the firehose, not the alert set."""
    out = render_full_listing(
        [_raw("morris_williams", date(2026, 5, 18), time(14, 30))],
        cfg, datetime(2026, 5, 16, 12, 30),
    )
    assert "Morris Williams" in out


def test_render_full_listing_includes_times_outside_premium_window(cfg):
    out = render_full_listing(
        [_raw("roy_kizer", date(2026, 5, 18), time(16, 45))],
        cfg, datetime(2026, 5, 16, 12, 30),
    )
    assert "16:45" in out


def test_render_full_listing_truncates_per_course(cfg):
    slots = [
        _raw("riverside", date(2026, 5, 18), time(7, i)) for i in range(15)
    ]
    out = render_full_listing(slots, cfg, datetime(2026, 5, 16, 12, 30))
    assert "(15):" in out
    assert "+5 more" in out


def test_render_full_listing_empty(cfg):
    out = render_full_listing([], cfg, datetime(2026, 5, 16, 12, 30))
    assert "No slots available" in out
