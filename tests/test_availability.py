"""Tests for golfbot.availability."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from golfbot import availability as avail_mod
from golfbot.config import load

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "config.yaml")


# ---------- registered_members ----------


def test_registered_members_excludes_zero_ids(cfg):
    """Members with telegram_user_id 0 are placeholders; ignore them.
    Whatever's in committed config, all returned members should have
    real (non-zero) IDs."""
    members = avail_mod.registered_members(cfg)
    name_to_id = {m.name: m.telegram_user_id for m in cfg.group.members}
    for name in members:
        assert name_to_id[name] != 0
    for m in cfg.group.members:
        if m.telegram_user_id == 0:
            assert m.name not in members


# ---------- is_available default ----------


def test_default_is_available():
    assert avail_mod.is_available("Anyone", date(2026, 5, 20), {}) is True


def test_is_available_when_out():
    avail = {"Colby": avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])}
    assert avail_mod.is_available("Colby", date(2026, 5, 20), avail) is False
    assert avail_mod.is_available("Colby", date(2026, 5, 21), avail) is True


# ---------- set_out / set_in ----------


def test_set_out_then_set_in_roundtrip():
    avail: dict = {}
    avail_mod.set_out("Colby", [date(2026, 5, 20), date(2026, 5, 21)], avail)
    assert avail["Colby"].out_dates == [date(2026, 5, 20), date(2026, 5, 21)]
    avail_mod.set_in("Colby", [date(2026, 5, 20)], avail)
    assert avail["Colby"].out_dates == [date(2026, 5, 21)]
    avail_mod.set_in("Colby", [date(2026, 5, 21)], avail)
    assert avail["Colby"].out_dates == []


def test_set_out_dedupes():
    avail: dict = {}
    avail_mod.set_out("Colby", [date(2026, 5, 20)], avail)
    avail_mod.set_out("Colby", [date(2026, 5, 20)], avail)
    assert avail["Colby"].out_dates == [date(2026, 5, 20)]


def test_set_in_unknown_member_is_noop():
    avail: dict = {}
    avail_mod.set_in("Ghost", [date(2026, 5, 20)], avail)
    assert avail == {}


# ---------- date_should_be_scanned ----------


def test_date_scanned_when_admin_available(cfg):
    avail = {}
    assert avail_mod.date_should_be_scanned(date(2026, 5, 20), cfg, avail) is True


def test_date_skipped_when_admin_out_and_required(cfg):
    # admin_required defaults to False now; explicitly opt in to admin-centric mode.
    cfg = cfg.model_copy(update={
        "group": cfg.group.model_copy(update={"admin_required": True})
    })
    avail = {cfg.group.admin: avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])}
    assert avail_mod.date_should_be_scanned(date(2026, 5, 20), cfg, avail) is False


def test_date_scanned_when_admin_out_but_others_in(cfg):
    """Default (admin_required=False): date is still scanned as long as
    at least one registered member is available, even if admin is out."""
    assert cfg.group.admin_required is False
    # Admin out for a date, but Steve/Ed (if registered) are still in.
    avail = {cfg.group.admin: avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])}
    if len(avail_mod.registered_members(cfg)) > 1:
        assert avail_mod.date_should_be_scanned(date(2026, 5, 20), cfg, avail) is True


def test_date_skipped_when_all_members_out(cfg):
    """If every registered member is out for a date, skip it."""
    avail = {
        m: avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])
        for m in avail_mod.registered_members(cfg)
    }
    assert avail_mod.date_should_be_scanned(date(2026, 5, 20), cfg, avail) is False


def test_date_scanned_when_admin_required_false(cfg):
    cfg = cfg.model_copy(update={"group": cfg.group.model_copy(update={"admin_required": False})})
    avail = {cfg.group.admin: avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])}
    assert avail_mod.date_should_be_scanned(date(2026, 5, 20), cfg, avail) is True


# ---------- players_to_search_for ----------


def test_players_to_search_for_default_is_count_of_available(cfg):
    """Counts registered members who are currently available."""
    avail = {}
    expected = len(avail_mod.registered_members(cfg))
    assert avail_mod.players_to_search_for(date(2026, 5, 20), cfg, avail) == max(1, expected)


def test_players_to_search_for_min_one(cfg):
    """Even if everyone is out, floor at 1 (date would be skipped anyway
    upstream, but the helper itself is robust)."""
    avail = {
        m: avail_mod.AvailabilityRecord(out_dates=[date(2026, 5, 20)])
        for m in avail_mod.registered_members(cfg)
    }
    assert avail_mod.players_to_search_for(date(2026, 5, 20), cfg, avail) == 1


# ---------- parse_date_arg ----------


@pytest.mark.parametrize("s,today,expected", [
    ("today",     date(2026, 5, 16), date(2026, 5, 16)),
    ("tomorrow",  date(2026, 5, 16), date(2026, 5, 17)),
    ("2026-06-01", date(2026, 5, 16), date(2026, 6, 1)),
    # Today is Sat May 16, 2026. wed is next Wed → May 20.
    ("wed",       date(2026, 5, 16), date(2026, 5, 20)),
    # Same-day name returns today.
    ("sat",       date(2026, 5, 16), date(2026, 5, 16)),
    # M/D rolls forward if in past.
    ("5/20",      date(2026, 5, 16), date(2026, 5, 20)),
    ("5/10",      date(2026, 5, 16), date(2027, 5, 10)),
])
def test_parse_date_arg(s, today, expected):
    assert avail_mod.parse_date_arg(s, today) == expected


def test_parse_date_arg_invalid():
    assert avail_mod.parse_date_arg("nonsense", date(2026, 5, 16)) is None
    assert avail_mod.parse_date_arg("", date(2026, 5, 16)) is None


# ---------- load/save roundtrip ----------


def test_load_save_roundtrip():
    state: dict = {}
    avail: dict = {}
    avail_mod.set_out("Colby", [date(2026, 5, 20), date(2026, 5, 21)], avail)
    avail_mod.save_availability(state, avail)
    assert state["availability"]["Colby"]["out_dates"] == ["2026-05-20", "2026-05-21"]

    reloaded = avail_mod.load_availability(state)
    assert reloaded["Colby"].out_dates == [date(2026, 5, 20), date(2026, 5, 21)]


def test_load_prunes_past_dates():
    state = {
        "availability": {
            "Colby": {"out_dates": ["2000-01-01", "2099-01-01"]}
        }
    }
    reloaded = avail_mod.load_availability(state)
    assert reloaded["Colby"].out_dates == [date(2099, 1, 1)]


# ---------- build_avail_grid (UI helper) ----------


def test_build_avail_grid_solo_member(cfg):
    from golfbot.bot import build_avail_grid
    today = date(2026, 5, 16)
    text, keyboard = build_avail_grid(cfg, {}, today)

    # One row per horizon day
    rows = keyboard.inline_keyboard
    assert len(rows) == cfg.search.horizon_days

    # Each row: date label + one button per registered member
    members = avail_mod.registered_members(cfg)
    expected_per_row = 1 + len(members)
    for row in rows:
        assert len(row) == expected_per_row

    # First button is no-op (date label)
    assert rows[0][0].callback_data == "noop"

    # Member button text shows ✅ by default (no out_dates set)
    member_button = rows[0][1]
    assert member_button.text.startswith("✅ ")
    assert members[0] in member_button.text
    assert member_button.callback_data.startswith("av:")


def test_build_avail_grid_reflects_out_dates(cfg):
    from golfbot.bot import build_avail_grid
    today = date(2026, 5, 16)
    members = avail_mod.registered_members(cfg)
    out_date = today + timedelta(days=2)
    availability = {
        members[0]: avail_mod.AvailabilityRecord(out_dates=[out_date])
    }
    text, keyboard = build_avail_grid(cfg, availability, today)

    # Find the row for out_date (today+2 → index 1 since horizon starts today+1)
    rows = keyboard.inline_keyboard
    out_row = rows[1]
    member_button = out_row[1]
    assert member_button.text.startswith("❌ "), \
        f"expected ❌ for out date, got {member_button.text!r}"


def test_build_avail_grid_callback_data_fits_telegram_limit(cfg):
    """All callback_data must fit in 64 bytes (Telegram hard limit)."""
    from golfbot.bot import build_avail_grid
    today = date(2026, 5, 16)
    _, keyboard = build_avail_grid(cfg, {}, today)
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.callback_data is not None:
                assert len(btn.callback_data.encode("utf-8")) <= 64, \
                    f"callback_data too long: {btn.callback_data!r}"


# Make `timedelta` importable from test module
from datetime import timedelta  # noqa: E402
