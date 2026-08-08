"""Tests for golfbot.config."""
from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from golfbot.config import (
    Config,
    TimeWindow,
    load,
    resolve_telegram_secrets,
)

V1_ONLY_CONFIG = """\
timezone: America/Chicago
search: {horizon_days: 7, start_offset_days: 1, days_of_week: [monday], holes: 18}
time_windows:
  ideal:      {start: "07:30", end: "08:00"}
  acceptable: {start: "07:00", end: "09:00"}
courses:
  - {key: roy_kizer, display: "Roy Kizer", tier: 1, provider: golfatx, provider_id: 2}
grading: {notify_min_grade: B}
polling: {default_interval_minutes: 60, jitter_minutes: 5}
group: {admin: Colby, members: [{name: Colby, telegram_user_id: 1}]}
telegram: {bot_token_env: T, chat_id_env: C}
"""

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CONFIG = REPO_ROOT / "config.yaml"


# ---------- whole-file load against the repo's actual config ----------


def test_load_repo_config():
    cfg = load(SAMPLE_CONFIG)
    assert isinstance(cfg, Config)
    assert cfg.timezone == "America/Chicago"
    assert cfg.tz.key == "America/Chicago"
    # Course list is user-tunable; check the keys we know are there.
    keys = {c.key for c in cfg.courses}
    assert {"roy_kizer", "jimmy_clay", "lions", "riverside",
            "morris_williams", "grey_rock_golf_club"} <= keys
    assert len(cfg.courses) >= 6
    assert cfg.group.admin == "Colby"
    assert {m.name for m in cfg.group.members} == {"Colby", "Steve", "Ed"}
    assert cfg.search.days_of_week == [
        "monday", "tuesday", "wednesday", "thursday", "friday",
    ]
    assert cfg.premium_window.start == time(7, 20)
    assert cfg.premium_window.end == time(8, 0)


def test_course_by_key():
    cfg = load(SAMPLE_CONFIG)
    rk = cfg.course_by_key("roy_kizer")
    assert rk is not None
    assert cfg.course_by_key("nonexistent") is None


# ---------- v2: all_star ----------


def test_all_star_set_matches_spec():
    """SPEC v2 > All-star set: exactly these four can fire an alert."""
    cfg = load(SAMPLE_CONFIG)
    assert {c.key for c in cfg.courses if c.all_star} == {
        "jimmy_clay", "roy_kizer", "riverside", "grey_rock_golf_club",
    }


def test_non_all_star_courses_are_still_scanned():
    """Scan all, alert on four — non-all-star courses stay configured."""
    cfg = load(SAMPLE_CONFIG)
    assert {c.key for c in cfg.courses if not c.all_star} >= {
        "morris_williams", "lions",
    }


def test_all_star_defaults_to_false():
    cfg = load(SAMPLE_CONFIG)
    lions = cfg.course_by_key("lions")
    assert lions is not None and lions.all_star is False


def test_rejects_config_with_no_all_star_course(tmp_path):
    bad = SAMPLE_CONFIG.read_text().replace("all_star: true", "all_star: false")
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="no course has all_star"):
        load(p)


# ---------- v2: alerts.headlines ----------


def test_headlines_load_from_config():
    cfg = load(SAMPLE_CONFIG)
    assert len(cfg.alerts.headlines) >= 10
    assert any("{name}" in h for h in cfg.alerts.headlines)


def test_alerts_block_is_optional(tmp_path):
    """A missing pool is legal; the renderer falls back to a built-in default."""
    text = SAMPLE_CONFIG.read_text()
    head, _, _ = text.partition("\nalerts:")
    _, _, tail = text.partition("\npolling:")
    p = tmp_path / "config.yaml"
    p.write_text(head + "\npolling:" + tail)
    cfg = load(p)
    assert cfg.alerts.headlines == []


# ---------- v2: v1 schema rejection ----------


def test_rejects_v1_schema_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(V1_ONLY_CONFIG)
    with pytest.raises(ValueError, match="uses the v1 schema"):
        load(p)


def test_v1_rejection_names_every_removed_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(V1_ONLY_CONFIG)
    with pytest.raises(ValueError) as exc:
        load(p)
    msg = str(exc.value)
    assert "time_windows" in msg and "premium_window" in msg
    assert "grading" in msg
    assert "courses[].tier" in msg and "all_star" in msg


def test_rejects_removed_search_player_keys(tmp_path):
    bad = SAMPLE_CONFIG.read_text().replace(
        "  holes: 18", "  holes: 18\n  default_players: 3\n  expanded_players: 2"
    )
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match=r"search\.default_players"):
        load(p)


# ---------- TimeWindow / TimeWindows ----------


def test_time_window_rejects_inverted():
    with pytest.raises(ValueError, match="must be before end"):
        TimeWindow(start=time(9, 0), end=time(7, 0))


def test_time_window_rejects_equal():
    with pytest.raises(ValueError, match="must be before end"):
        TimeWindow(start=time(8, 0), end=time(8, 0))


def test_premium_window_rejects_inverted(tmp_path):
    bad = SAMPLE_CONFIG.read_text().replace(
        'premium_window: { start: "07:20", end: "08:00" }',
        'premium_window: { start: "09:00", end: "07:00" }',
    )
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="must be before end"):
        load(p)


def test_all_star_courses_helper():
    cfg = load(SAMPLE_CONFIG)
    assert [c.key for c in cfg.all_star_courses()] == [
        "jimmy_clay", "roy_kizer", "riverside", "grey_rock_golf_club",
    ]


# ---------- cross-field validation in Config ----------


def test_admin_must_be_in_members(tmp_path):
    bad = SAMPLE_CONFIG.read_text().replace("admin: Colby", "admin: Greg")
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="admin 'Greg' is not in members"):
        load(p)


def test_invalid_timezone(tmp_path):
    bad = SAMPLE_CONFIG.read_text().replace("America/Chicago", "Mars/Olympus")
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="unknown timezone"):
        load(p)


def test_duplicate_course_keys(tmp_path):
    # Replace the second course's key with the first course's key.
    bad = SAMPLE_CONFIG.read_text().replace(
        "key: jimmy_clay", "key: roy_kizer", 1
    )
    p = tmp_path / "config.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="duplicate course keys"):
        load(p)


def test_load_rejects_non_mapping(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="did not parse to a mapping"):
        load(p)


# ---------- resolve_telegram_secrets ----------


def test_resolve_telegram_secrets_ok(monkeypatch):
    cfg = load(SAMPLE_CONFIG)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123:def456")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100456")
    token, chat_id = resolve_telegram_secrets(cfg)
    assert token == "abc123:def456"
    assert chat_id == -100456


def test_resolve_telegram_secrets_missing_token(monkeypatch):
    cfg = load(SAMPLE_CONFIG)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100456")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not set"):
        resolve_telegram_secrets(cfg)


def test_resolve_telegram_secrets_blank_token(monkeypatch):
    cfg = load(SAMPLE_CONFIG)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100456")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not set"):
        resolve_telegram_secrets(cfg)


def test_resolve_telegram_secrets_non_int_chat(monkeypatch):
    cfg = load(SAMPLE_CONFIG)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "not_a_number")
    with pytest.raises(RuntimeError, match="must be an integer"):
        resolve_telegram_secrets(cfg)
