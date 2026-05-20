"""Config loader: reads config.yaml into typed pydantic models.

This module does NOT load .env. App startup code calls `dotenv.load_dotenv()`
once before `resolve_telegram_secrets()` is used. Keeping config parsing
pure makes it trivial to test.

See SPEC.md > Config schema for the canonical shape.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DayOfWeek = Literal[
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]
Grade = Literal["A", "B", "C"]


class TimeWindow(BaseModel):
    start: time
    end: time

    @model_validator(mode="after")
    def _check_order(self) -> TimeWindow:
        if self.start >= self.end:
            raise ValueError(f"start ({self.start}) must be before end ({self.end})")
        return self


class TimeWindows(BaseModel):
    ideal: TimeWindow
    acceptable: TimeWindow

    @model_validator(mode="after")
    def _ideal_within_acceptable(self) -> TimeWindows:
        if self.ideal.start < self.acceptable.start or self.ideal.end > self.acceptable.end:
            raise ValueError(
                f"ideal {self.ideal.start}-{self.ideal.end} must fit within "
                f"acceptable {self.acceptable.start}-{self.acceptable.end}"
            )
        return self


class Search(BaseModel):
    horizon_days: int = Field(ge=1, le=30)
    start_offset_days: int = Field(ge=0, le=30)
    days_of_week: list[DayOfWeek]
    holes: Literal[9, 18]
    default_players: int = Field(ge=1, le=4)
    expanded_players: int = Field(ge=1, le=4)


ProviderName = Literal["golfnow", "golfatx"]


class Course(BaseModel):
    key: str
    display: str
    tier: Literal[1, 2, 3]
    provider: ProviderName
    provider_id: str | int   # opaque per-provider identifier (int facilityId for GolfNow,
                             # WebTrac code string for GolfATX, "TBD" placeholder allowed)


class Grading(BaseModel):
    notify_min_grade: Grade


class ActiveWindow(BaseModel):
    """Time-of-day range during which the scheduled scan fires.

    Scans outside this window are silently skipped. /scan can override
    via force=True so the admin can scan whenever.
    """
    start: time
    end: time

    @model_validator(mode="after")
    def _check_order(self) -> ActiveWindow:
        if self.start >= self.end:
            raise ValueError(
                f"active_window start ({self.start}) must be before end ({self.end})"
            )
        return self


class Polling(BaseModel):
    default_interval_minutes: int = Field(ge=1)
    jitter_minutes: int = Field(ge=0)
    # Shape of a hammer window is TBD (per SPEC); permissive for now.
    hammer_windows: list[dict] = Field(default_factory=list)
    # When set, scheduled scans only fire during this time-of-day window.
    # None = 24/7. /scan always works regardless.
    active_window: ActiveWindow | None = None


class Member(BaseModel):
    name: str
    telegram_user_id: int   # 0 means "not registered yet" (set via /whoami)


class Group(BaseModel):
    admin: str
    members: list[Member] = Field(min_length=1)
    # When True: skip dates entirely when admin is out.
    # When False (default): scan any date where ≥1 registered member is available,
    # and the scanner uses that count as min_players for the search.
    admin_required: bool = False

    @model_validator(mode="after")
    def _admin_in_members(self) -> Group:
        names = {m.name for m in self.members}
        if self.admin not in names:
            raise ValueError(
                f"admin {self.admin!r} is not in members list {sorted(names)}"
            )
        return self


class Telegram(BaseModel):
    bot_token_env: str
    chat_id_env: str


class WeatherConfig(BaseModel):
    """Daily forecast via Open-Meteo. Cached in state.json."""
    enabled: bool = True
    latitude: float
    longitude: float
    cache_hours: float = Field(default=6.0, ge=0.5)


class Config(BaseModel):
    timezone: str
    search: Search
    time_windows: TimeWindows
    courses: list[Course] = Field(min_length=1)
    grading: Grading
    polling: Polling
    group: Group
    telegram: Telegram
    weather: WeatherConfig | None = None    # None = disabled

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"unknown timezone: {v}") from e
        return v

    @model_validator(mode="after")
    def _unique_course_keys(self) -> Config:
        keys = [c.key for c in self.courses]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate course keys: {sorted(dupes)}")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def course_by_key(self, key: str) -> Course | None:
        return next((c for c in self.courses if c.key == key), None)


def load(path: Path | str = "config.yaml") -> Config:
    """Parse and validate a config.yaml file."""
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} did not parse to a mapping (got {type(raw).__name__})")
    return Config.model_validate(raw)


def resolve_telegram_secrets(cfg: Config) -> tuple[str, int]:
    """Read the env vars named in cfg.telegram and return (bot_token, chat_id).

    Raises RuntimeError if either is unset, empty, or malformed.
    Caller is responsible for `dotenv.load_dotenv()` before this is called.
    """
    token = os.environ.get(cfg.telegram.bot_token_env, "").strip()
    chat = os.environ.get(cfg.telegram.chat_id_env, "").strip()
    if not token:
        raise RuntimeError(f"env var {cfg.telegram.bot_token_env} is not set")
    if not chat:
        raise RuntimeError(f"env var {cfg.telegram.chat_id_env} is not set")
    try:
        chat_id = int(chat)
    except ValueError as e:
        raise RuntimeError(
            f"{cfg.telegram.chat_id_env} must be an integer, got {chat!r}"
        ) from e
    return token, chat_id
