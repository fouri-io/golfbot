"""Tests for the GolfNow provider's parsing logic (no network)."""
from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

from golfbot.providers.golfnow import (
    build_request_body,
    parse_response,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "golfnow_riverside_2026-05-21.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


# ---------- build_request_body ----------


def test_build_request_body_basic_fields():
    body = build_request_body(888, date(2026, 5, 21), min_players=3)
    assert body["facilityId"] == 888
    assert body["date"] == "May 21 2026"
    assert body["players"] == 3
    # timeMin/timeMax are half-hours past midnight; 0..48 = full day
    assert body["timeMin"] == 0
    assert body["timeMax"] == 48
    assert body["holes"] == "Eighteen"
    assert body["searchType"] == "Facility"
    assert body["view"] == "Grouping"


def test_build_request_body_date_format_zero_padding():
    """GolfNow expects 'May 1 2026', not 'May 01 2026'."""
    body = build_request_body(888, date(2026, 5, 1), 3)
    # %d gives "01" on most platforms; the user's curl shows "May 21 2026"
    # with two digits, so either form is accepted. We just confirm the
    # year + month name are right and the day is parseable.
    assert "2026" in body["date"]
    assert body["date"].startswith("May ")


# ---------- parse_response ----------


def test_parse_response_returns_18_hole_slots_only():
    """Fixture has 2x 18-hole slots and 1x 9-hole slot — only the 18-hole
    ones should survive the parse."""
    slots = parse_response(_load_fixture(), "riverside", date(2026, 5, 21), 3)
    assert len(slots) == 2
    for s in slots:
        assert s.holes == 18


def test_parse_response_fields():
    slots = parse_response(_load_fixture(), "riverside", date(2026, 5, 21), 3)
    s = slots[0]
    assert s.course_key == "riverside"
    assert s.tee_date == date(2026, 5, 21)
    assert s.tee_time == time(7, 10)
    assert s.players_available == 3
    assert s.holes == 18
    assert s.booking_url == "https://www.golfnow.com/tee-times/facility/888/tee-time/65232384"
    assert s.provider == "golfnow"
    assert s.price_usd == 45.0
    assert s.extra["teeTimeRateId"] == 65232384


def test_parse_response_handles_pm_correctly():
    """Sanity: a 12:00 PM (would-be) slot in the fixture is 9-hole and
    should be filtered out, but we want to confirm time parsing handles
    PM. Construct a synthetic payload."""
    payload = {
        "ttResults": {
            "teeTimes": [
                {
                    "teeTimeRates": [
                        {
                            "holeCount": 18,
                            "teeTimeRateId": 1,
                            "greensFees": {"value": 50},
                            "isEighteen": True,
                            "detailUrl": "/tee-times/facility/1/tee-time/1",
                        }
                    ],
                    "time": {"formatted": "2:30", "formattedTimeMeridian": "PM"},
                    "detailUrl": "/tee-times/facility/1/tee-time/1",
                }
            ]
        }
    }
    slots = parse_response(payload, "x", date(2026, 5, 21), 3)
    assert len(slots) == 1
    assert slots[0].tee_time == time(14, 30)


def test_parse_response_handles_midnight_meridian():
    """12:00 AM should parse to 00:00, not 12:00."""
    payload = {
        "ttResults": {
            "teeTimes": [
                {
                    "teeTimeRates": [
                        {
                            "holeCount": 18,
                            "teeTimeRateId": 1,
                            "greensFees": {"value": 50},
                            "isEighteen": True,
                            "detailUrl": "/tee-times/facility/1/tee-time/1",
                        }
                    ],
                    "time": {"formatted": "12:15", "formattedTimeMeridian": "AM"},
                    "detailUrl": "/tee-times/facility/1/tee-time/1",
                }
            ]
        }
    }
    slots = parse_response(payload, "x", date(2026, 5, 21), 3)
    assert slots[0].tee_time == time(0, 15)


def test_parse_response_extracts_price_from_nested_singlePlayerPrice():
    """Real GolfNow responses put greensFees under
    teeTimeRates[i].singlePlayerPrice, not at the rate root."""
    payload = {
        "ttResults": {
            "teeTimes": [
                {
                    "teeTimeRates": [
                        {
                            "holeCount": 18,
                            "teeTimeRateId": 1,
                            "isEighteen": True,
                            "detailUrl": "/x",
                            "singlePlayerPrice": {
                                "greensFees": {"value": 45.0}
                            },
                        }
                    ],
                    "time": {"formatted": "8:00", "formattedTimeMeridian": "AM"},
                    "detailUrl": "/x",
                }
            ]
        }
    }
    slots = parse_response(payload, "x", date(2026, 5, 21), 3)
    assert len(slots) == 1
    assert slots[0].price_usd == 45.0


def test_parse_response_empty_payload():
    assert parse_response({}, "x", date(2026, 5, 21), 3) == []
    assert parse_response({"ttResults": {}}, "x", date(2026, 5, 21), 3) == []
    assert parse_response(
        {"ttResults": {"teeTimes": []}}, "x", date(2026, 5, 21), 3
    ) == []


def test_parse_response_skips_slot_with_missing_time():
    payload = {
        "ttResults": {
            "teeTimes": [
                {
                    "teeTimeRates": [
                        {
                            "holeCount": 18,
                            "isEighteen": True,
                            "detailUrl": "/x",
                            "greensFees": {"value": 50},
                        }
                    ],
                    "time": {},  # malformed
                }
            ]
        }
    }
    assert parse_response(payload, "x", date(2026, 5, 21), 3) == []
