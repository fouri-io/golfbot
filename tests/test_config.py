"""Tests for golfbot.config."""
from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from golfbot.config import (
    Config,
    TimeWindow,
    TimeWindows,
    load,
    resolve_telegram_secrets,
)

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
    assert cfg.grading.notify_min_grade == "B"
    assert cfg.time_windows.ideal.start == time(7, 30)
    assert cfg.time_windows.acceptable.end == time(9, 0)


def test_course_by_key():
    cfg = load(SAMPLE_CONFIG)
    rk = cfg.course_by_key("roy_kizer")
    assert rk is not None
    assert rk.tier == 1
    assert cfg.course_by_key("nonexistent") is None


# ---------- TimeWindow / TimeWindows ----------


def test_time_window_rejects_inverted():
    with pytest.raises(ValueError, match="must be before end"):
        TimeWindow(start=time(9, 0), end=time(7, 0))


def test_time_window_rejects_equal():
    with pytest.raises(ValueError, match="must be before end"):
        TimeWindow(start=time(8, 0), end=time(8, 0))


def test_ideal_must_fit_acceptable():
    with pytest.raises(ValueError, match="must fit within acceptable"):
        TimeWindows(
            ideal=TimeWindow(start=time(6, 0), end=time(10, 0)),
            acceptable=TimeWindow(start=time(7, 0), end=time(9, 0)),
        )


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
