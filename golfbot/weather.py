"""Weather forecast via Open-Meteo (free, no API key).

We annotate the daily digest with high/low temps + rain probability + a
weather-condition emoji. The forecast is fetched at most every
`weather.cache_hours` hours and cached in `state.json` so /tee, /scan,
and scheduled scans all share the data.

Open-Meteo is free for non-commercial use with no rate limit at our
scale (~4-6 calls/day with default 6h cache).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes → emoji.
# See https://open-meteo.com/en/docs (search "weather_code").
_CODE_TO_EMOJI: dict[int, str] = {
    0: "☀️",
    1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️",
    56: "🌧️", 57: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    66: "🌧️", 67: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "🌨️", 77: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "🌧️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}


def emoji_for(code: int | None) -> str:
    if code is None:
        return ""
    return _CODE_TO_EMOJI.get(int(code), "")


@dataclass(frozen=True)
class WeatherDay:
    date: date
    tmax: float   # Fahrenheit
    tmin: float   # Fahrenheit
    rain_pct: int # 0-100
    code: int     # WMO weather code

    @property
    def emoji(self) -> str:
        return emoji_for(self.code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "tmax": self.tmax,
            "tmin": self.tmin,
            "rain_pct": self.rain_pct,
            "code": self.code,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WeatherDay:
        return cls(
            date=date.fromisoformat(d["date"]),
            tmax=float(d["tmax"]),
            tmin=float(d["tmin"]),
            rain_pct=int(d["rain_pct"]),
            code=int(d["code"]),
        )


# --------------------------------------------------------------------------- #
# Fetch + parse                                                                #
# --------------------------------------------------------------------------- #


async def fetch_forecast(
    lat: float,
    lon: float,
    tz_name: str,
    days: int = 8,
    timeout_seconds: float = 15.0,
) -> dict[date, WeatherDay]:
    """Query Open-Meteo for a daily forecast. Returns {date: WeatherDay}."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,weather_code"
        ),
        "temperature_unit": "fahrenheit",
        "timezone": tz_name,
        "forecast_days": days,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        r = await client.get(OPEN_METEO_URL, params=params)
        r.raise_for_status()
        data = r.json()
    return parse_forecast(data)


def parse_forecast(data: dict) -> dict[date, WeatherDay]:
    """Parse an Open-Meteo daily response. Forgiving on missing fields."""
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    tmaxes = daily.get("temperature_2m_max") or []
    tmins = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_probability_max") or []
    codes = daily.get("weather_code") or []

    out: dict[date, WeatherDay] = {}
    for i, date_iso in enumerate(times):
        try:
            d = date.fromisoformat(date_iso)
            wd = WeatherDay(
                date=d,
                tmax=float(tmaxes[i]) if i < len(tmaxes) and tmaxes[i] is not None else 0.0,
                tmin=float(tmins[i]) if i < len(tmins) and tmins[i] is not None else 0.0,
                rain_pct=int(rains[i]) if i < len(rains) and rains[i] is not None else 0,
                code=int(codes[i]) if i < len(codes) and codes[i] is not None else 0,
            )
            out[d] = wd
        except (ValueError, IndexError, TypeError) as e:
            log.warning("weather: skipping malformed entry %r: %s", date_iso, e)
    return out


# --------------------------------------------------------------------------- #
# Cache                                                                        #
# --------------------------------------------------------------------------- #


def load_cache(state: dict[str, Any]) -> tuple[datetime | None, dict[date, WeatherDay]]:
    """Read cached forecast from `state.weather`. Returns (fetched_at, days)."""
    cache = state.get("weather") or {}
    fetched_at: datetime | None = None
    fa = cache.get("fetched_at")
    if fa:
        try:
            fetched_at = datetime.fromisoformat(fa)
        except (ValueError, TypeError):
            fetched_at = None

    days_raw = cache.get("days") or {}
    days: dict[date, WeatherDay] = {}
    for k, v in days_raw.items():
        try:
            d = date.fromisoformat(k)
            days[d] = WeatherDay.from_dict(v)
        except (ValueError, KeyError, TypeError):
            continue
    return fetched_at, days


def save_cache(
    state: dict[str, Any],
    fetched_at: datetime,
    days: dict[date, WeatherDay],
) -> None:
    state["weather"] = {
        "fetched_at": fetched_at.isoformat(),
        "days": {d.isoformat(): wd.to_dict() for d, wd in days.items()},
    }


def is_fresh(
    fetched_at: datetime | None,
    now: datetime,
    max_age_hours: float,
) -> bool:
    if fetched_at is None:
        return False
    age = (now - fetched_at).total_seconds() / 3600
    return 0 <= age < max_age_hours
