"""Config loader: reads config.yaml into typed pydantic models.

This module does NOT load .env. App startup code calls `dotenv.load_dotenv()`
once before `resolve_telegram_secrets()` is used. Keeping config parsing
pure makes it trivial to test.

See docs/SPEC.md > Config schema (v2) for the canonical shape.

v2 (Gold Star pivot): the quality bar is a single `premium_window` plus a
per-course `all_star` flag. The v1 two-axis grading config (`time_windows`,
`grading`, course `tier`) is gone from the file format — see
docs/decisions/0006-gold-star-pivot.md.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Any, Literal
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


# --------------------------------------------------------------------------- #
# v1 compatibility shims — TEMPORARY                                          #
#                                                                             #
# `TimeWindows` and `Grading` are no longer parsed from config.yaml. They      #
# survive only so the not-yet-rewritten consumers (pipeline.filter_and_grade,  #
# grading.py, notifier.render_status, the scrape CLI) keep working during the  #
# v2 refactor. Slice 2 replaces those consumers with the Gold Star rule and    #
# both of these classes — plus the `Config.time_windows` / `Config.grading` /  #
# `Course.tier` properties below — get deleted.                                #
#                                                                             #
# Do not add new readers of these.                                            #
# --------------------------------------------------------------------------- #


class TimeWindows(BaseModel):
    """v1 only. Superseded by `Config.premium_window`."""
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


class Grading(BaseModel):
    """v1 only. v2 has no grades — the premium window is the whole bar."""
    notify_min_grade: Grade


class Search(BaseModel):
    horizon_days: int = Field(ge=1, le=30)
    start_offset_days: int = Field(ge=0, le=30)
    days_of_week: list[DayOfWeek]
    holes: Literal[9, 18]


ProviderName = Literal["golfnow", "golfatx"]


class Course(BaseModel):
    key: str
    display: str
    provider: ProviderName
    provider_id: str | int   # opaque per-provider identifier (int facilityId for GolfNow,
                             # WebTrac code string for GolfATX, "TBD" placeholder allowed)
    # Only all-star courses can fire a Gold Star alert. Every configured
    # course is still scanned so /full shows the complete picture.
    all_star: bool = False

    @property
    def tier(self) -> int:
        """v1 compat shim — see the block comment above. Deleted in Slice 2."""
        return 1 if self.all_star else 2


class Alerts(BaseModel):
    """Gold Star alert copy. Headlines are picked at random per alert.

    A headline may contain `{name}`, substituted with a random roster member.
    An empty or missing pool falls back to a single built-in default at render
    time (see docs/SPEC.md > Snark).
    """
    headlines: list[str] = Field(default_factory=list)


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
    # Availability-layer flag. Slice 3 deletes the availability layer and this
    # field with it; kept here so `availability.py` still loads until then.
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
    # The single quality bar: a weekday slot at an all-star course inside this
    # window with >=1 open spot is a Gold Star.
    premium_window: TimeWindow
    courses: list[Course] = Field(min_length=1)
    alerts: Alerts = Field(default_factory=Alerts)
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

    @model_validator(mode="after")
    def _at_least_one_all_star(self) -> Config:
        if not any(c.all_star for c in self.courses):
            raise ValueError(
                "no course has all_star: true — the bot could never alert. "
                "Mark at least one course all_star."
            )
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def course_by_key(self, key: str) -> Course | None:
        return next((c for c in self.courses if c.key == key), None)

    # ----- v1 compat shims — see the block comment above. Deleted in Slice 2.

    @property
    def time_windows(self) -> TimeWindows:
        """Collapses to the premium window on both axes, so v1 grading code
        treats "in the premium window" as the only passing bucket."""
        return TimeWindows(ideal=self.premium_window, acceptable=self.premium_window)

    @property
    def grading(self) -> Grading:
        return Grading(notify_min_grade="B")


# Keys that existed in v1 and are gone in v2. Mapping value is the replacement
# (or None if the concept was dropped outright).
_REMOVED_TOP_LEVEL: dict[str, str | None] = {
    "time_windows": "premium_window",
    "grading": None,
}
_REMOVED_SEARCH: dict[str, str | None] = {
    "default_players": None,
    "expanded_players": None,
}
_REMOVED_COURSE: dict[str, str | None] = {
    "tier": "all_star",
}


def _reject_v1_keys(raw: dict[str, Any], path: Path) -> None:
    """Fail loudly on a v1 config rather than silently ignoring dead keys.

    pydantic ignores unknown keys by default, so without this a stale
    config.yaml would load fine and quietly behave nothing like it reads.
    """
    found: list[str] = []
    for key, replacement in _REMOVED_TOP_LEVEL.items():
        if key in raw:
            found.append(f"{key}" + (f" (use {replacement})" if replacement else ""))
    search = raw.get("search")
    if isinstance(search, dict):
        for key, replacement in _REMOVED_SEARCH.items():
            if key in search:
                found.append(f"search.{key}" + (f" (use {replacement})" if replacement else ""))
    courses = raw.get("courses")
    if isinstance(courses, list):
        for key, replacement in _REMOVED_COURSE.items():
            if any(isinstance(c, dict) and key in c for c in courses):
                found.append(
                    f"courses[].{key}" + (f" (use {replacement})" if replacement else "")
                )
    if found:
        raise ValueError(
            f"{path} uses the v1 schema. Remove these keys: {', '.join(found)}. "
            "See docs/SPEC.md > Config schema (v2)."
        )


def load(path: Path | str = "config.yaml") -> Config:
    """Parse and validate a config.yaml file."""
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} did not parse to a mapping (got {type(raw).__name__})")
    _reject_v1_keys(raw, path)
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
