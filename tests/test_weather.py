"""Tests for golfbot.weather (parsing + cache; no live API)."""
from __future__ import annotations

from datetime import date, datetime

from golfbot import weather as weather_mod


def test_parse_forecast_basic():
    sample = {
        "daily": {
            "time": ["2026-05-19", "2026-05-20", "2026-05-21"],
            "temperature_2m_max": [83.0, 87.0, 78.0],
            "temperature_2m_min": [62.0, 66.0, 62.0],
            "precipitation_probability_max": [7, 12, 75],
            "weather_code": [3, 2, 65],
        }
    }
    days = weather_mod.parse_forecast(sample)
    assert len(days) == 3
    d = days[date(2026, 5, 19)]
    assert d.tmax == 83.0
    assert d.tmin == 62.0
    assert d.rain_pct == 7
    assert d.code == 3
    # code 3 = overcast → ☁️
    assert d.emoji == "☁️"

    rainy = days[date(2026, 5, 21)]
    assert rainy.code == 65
    assert rainy.emoji == "🌧️"


def test_parse_forecast_handles_missing_fields():
    sample = {"daily": {"time": ["2026-05-19"], "temperature_2m_max": [None]}}
    days = weather_mod.parse_forecast(sample)
    # Either skipped or defaulted; we just confirm no crash and dict shape.
    assert isinstance(days, dict)


def test_parse_forecast_empty():
    assert weather_mod.parse_forecast({}) == {}
    assert weather_mod.parse_forecast({"daily": {}}) == {}


def test_emoji_for_unknown_code():
    assert weather_mod.emoji_for(None) == ""
    assert weather_mod.emoji_for(9999) == ""


def test_emoji_for_known_codes():
    assert weather_mod.emoji_for(0) == "☀️"
    assert weather_mod.emoji_for(2) == "⛅"
    assert weather_mod.emoji_for(65) == "🌧️"
    assert weather_mod.emoji_for(95) == "⛈️"


def test_cache_roundtrip():
    state: dict = {}
    days = {
        date(2026, 5, 19): weather_mod.WeatherDay(date(2026, 5, 19), 83, 62, 7, 3),
        date(2026, 5, 20): weather_mod.WeatherDay(date(2026, 5, 20), 87, 66, 12, 2),
    }
    weather_mod.save_cache(state, datetime(2026, 5, 19, 10, 0), days)

    fetched_at, loaded = weather_mod.load_cache(state)
    assert fetched_at == datetime(2026, 5, 19, 10, 0)
    assert loaded == days


def test_load_cache_empty():
    fetched_at, days = weather_mod.load_cache({})
    assert fetched_at is None
    assert days == {}


def test_is_fresh_within_age():
    fetched = datetime(2026, 5, 19, 8, 0)
    now = datetime(2026, 5, 19, 12, 0)  # 4h later
    assert weather_mod.is_fresh(fetched, now, 6.0) is True
    assert weather_mod.is_fresh(fetched, now, 3.0) is False


def test_is_fresh_no_cache():
    assert weather_mod.is_fresh(None, datetime.now(), 6.0) is False


def test_weatherday_roundtrip():
    wd = weather_mod.WeatherDay(date(2026, 5, 19), 83.0, 62.0, 7, 3)
    d = wd.to_dict()
    assert weather_mod.WeatherDay.from_dict(d) == wd
