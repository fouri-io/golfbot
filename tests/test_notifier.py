"""Tests for golfbot.notifier (pure rendering only; Telegram API not exercised)."""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from golfbot.config import load
from golfbot.models import TeeTimeSlot
from golfbot.notifier import (
    FALLBACK_HEADLINE,
    _fmt_clock,
    _fmt_date,
    _fmt_time,
    build_keyboard_open,
    pick_headline,
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


# ---------- pick_headline (docs/SPEC.md > Snark) ----------


def test_headline_comes_from_the_configured_pool(cfg):
    rng = random.Random(0)
    out = pick_headline(cfg, rng=rng, state={"last": None})
    templates = {h.replace("{name}", n) for h in cfg.alerts.headlines
                 for n in [m.name for m in cfg.group.members]}
    assert out in set(cfg.alerts.headlines) | templates


def test_headline_never_repeats_the_previous_one(cfg):
    """Seeded RNG so the sequence is reproducible; the re-roll must hold
    across a long run. Compared on templates, since {name} substitution
    would otherwise mask a repeat."""
    rng = random.Random(1234)
    state: dict = {"last": None}
    seen: list[str | None] = []
    for _ in range(200):
        pick_headline(cfg, rng=rng, state=state)
        seen.append(state["last"])
    assert all(a != b for a, b in pairwise(seen))


def test_headline_pool_gets_reasonable_coverage(cfg):
    """No-repeat must not collapse to alternating between two lines."""
    rng = random.Random(99)
    state: dict = {"last": None}
    seen = set()
    for _ in range(200):
        pick_headline(cfg, rng=rng, state=state)
        seen.add(state["last"])
    assert len(seen) == len(cfg.alerts.headlines)


def test_headline_substitutes_a_roster_name(cfg):
    cfg = cfg.model_copy(update={
        "alerts": cfg.alerts.model_copy(update={"headlines": ["Beat {name} to it"]}),
    })
    out = pick_headline(cfg, rng=random.Random(7), state={"last": None})
    assert "{name}" not in out
    assert out.replace("Beat ", "").replace(" to it", "") in {
        m.name for m in cfg.group.members
    }


def test_headline_falls_back_when_pool_is_empty(cfg):
    cfg = cfg.model_copy(update={
        "alerts": cfg.alerts.model_copy(update={"headlines": []}),
    })
    assert pick_headline(cfg, rng=random.Random(0), state={"last": None}) == (
        FALLBACK_HEADLINE
    )


def test_single_headline_pool_does_not_hang(cfg):
    """A one-line pool can't satisfy no-repeat; it must not spin forever."""
    cfg = cfg.model_copy(update={
        "alerts": cfg.alerts.model_copy(update={"headlines": ["Only one"]}),
    })
    state: dict = {"last": None}
    rng = random.Random(0)
    assert [pick_headline(cfg, rng=rng, state=state) for _ in range(3)] == [
        "Only one"] * 3


def test_headline_state_is_isolated_per_caller(cfg):
    """Tests pass their own state so they don't share module globals."""
    a: dict = {"last": None}
    b: dict = {"last": None}
    pick_headline(cfg, rng=random.Random(0), state=a)
    pick_headline(cfg, rng=random.Random(0), state=b)
    assert a["last"] == b["last"]     # same seed, independent state


# ---------- render_open (the Gold Star alert) ----------


def test_render_open_matches_spec_format(slot):
    """docs/SPEC.md > Gold Star alert — the mock is literal."""
    out = render_open(slot, "Jimmy Clay", "Book it now, dumbass")
    assert out == (
        "🎯 Book it now, dumbass\n"
        "\n"
        "Jimmy Clay · Sat May 23\n"
        "7:40 AM · 4 spots open"
    )


def test_render_open_has_no_vote_tally(slot):
    """v2 removed voting — no Yes/No/Waiting block, no roster."""
    out = render_open(slot, "Roy Kizer", "whatever")
    for gone in ("✅ Yes", "❌ No", "Waiting", "Availability", "Grade"):
        assert gone not in out


def test_render_open_carries_the_facts(slot):
    out = render_open(slot, "Roy Kizer", "whatever")
    assert "Roy Kizer" in out
    assert "Sat May 23" in out
    assert "7:40 AM" in out
    assert "4 spots" in out


def test_render_open_singular_spot(slot):
    one = replace(slot, spots_open=1)
    assert "1 spot open" in render_open(one, "Roy Kizer", "x")


def test_render_open_escapes_html(slot):
    """Headlines are user-editable config and the message is sent as HTML."""
    out = render_open(slot, "Roy & <b>Kizer</b>", "Beat <Steve> & win")
    assert "&lt;Steve&gt; &amp; win" in out
    assert "Roy &amp; &lt;b&gt;Kizer&lt;/b&gt;" in out


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


def test_render_status_shows_next_scan(cfg):
    """SPEC v2 > Telegram commands: /status reports last AND next scan."""
    state = default_state()
    state["next_run_at"] = (datetime.now(cfg.tz) + timedelta(minutes=42)).isoformat()
    out = render_status(state, cfg, today=date(2026, 5, 15))
    next_line = next(ln for ln in out.splitlines() if ln.startswith("⏭"))
    assert "in 41m" in next_line or "in 42m" in next_line


def test_render_status_next_scan_unscheduled(cfg):
    out = render_status(default_state(), cfg, today=date(2026, 5, 15))
    assert "⏭ Next scan: — (not scheduled)" in out


def test_render_status_paused(cfg):
    state = default_state()
    state["paused"] = True
    out = render_status(state, cfg, today=date(2026, 5, 15))
    assert "Sat May 16 → Fri May 22" in out
    assert "🔔 Notifications: OFF (paused)" in out


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


def test_render_full_listing_shows_forecast_on_date_heading(cfg):
    """docs/SPEC.md > Weather: shown in the /full listing when enabled."""
    out = render_full_listing(
        [_raw("roy_kizer", date(2026, 5, 18), time(7, 40))],
        cfg, datetime(2026, 5, 16, 12, 30),
        weather={"2026-05-18": {"tmax": 87.4, "tmin": 66.2, "rain_pct": 12, "code": 1}},
    )
    assert "87°/66°" in out
    assert "Rain 12%" in out


def test_render_full_listing_without_forecast_is_unchanged(cfg):
    """No cached weather must not leave a dangling separator."""
    slots = [_raw("roy_kizer", date(2026, 5, 18), time(7, 40))]
    bare = render_full_listing(slots, cfg, datetime(2026, 5, 16, 12, 30))
    assert "Mon 5/18</b> (1)" in bare
    assert "°" not in bare
