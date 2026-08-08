"""Tests for golfbot.store."""
from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from golfbot.store import default_state, load_state, save_state

# ---------- state.json ----------


def test_load_state_missing_returns_default(tmp_path):
    state = load_state(tmp_path / "state.json")
    assert state == default_state()
    assert state["paused"] is False
    assert state["tee_times"] == []


def test_load_state_empty_file_returns_default(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("")
    assert load_state(p) == default_state()


async def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    state = default_state()
    state["paused"] = True
    state["tee_times"].append({"id": "roy_kizer:2026-05-23:0800", "spots_open": 4})
    await save_state(p, state)

    loaded = load_state(p)
    assert loaded["paused"] is True
    assert loaded["tee_times"][0]["id"] == "roy_kizer:2026-05-23:0800"


async def test_save_state_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "state.json"
    await save_state(p, default_state())
    assert p.exists()


async def test_save_state_serializes_datetime(tmp_path):
    p = tmp_path / "state.json"
    state = default_state()
    state["last_poll_at"] = datetime(2026, 5, 15, 18, 0, 0)
    state["tee_times"].append({"tee_time": time(8, 0), "tee_date": date(2026, 5, 23)})
    await save_state(p, state)

    # On disk these are ISO strings.
    raw = json.loads(p.read_text())
    assert raw["last_poll_at"] == "2026-05-15T18:00:00"
    assert raw["tee_times"][0]["tee_time"] == "08:00:00"
    assert raw["tee_times"][0]["tee_date"] == "2026-05-23"


async def test_save_state_rejects_unknown_type(tmp_path):
    p = tmp_path / "state.json"
    state = default_state()
    state["bad"] = object()
    with pytest.raises(TypeError, match="not JSON-serializable"):
        await save_state(p, state)


async def test_save_state_atomic_no_tmp_file_left_behind(tmp_path):
    p = tmp_path / "state.json"
    await save_state(p, default_state())
    siblings = list(p.parent.iterdir())
    assert siblings == [p], f"unexpected files in dir: {siblings}"
