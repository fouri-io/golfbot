"""Per-member availability layer.

State shape (under `state.json` -> `availability`):

    {
      "Colby": { "out_dates": ["2026-05-20", ...] },
      "Steve": { "out_dates": [] }
    }

Default model is **available unless explicitly marked out**. Only members
with a non-zero `telegram_user_id` count as "registered" for the purposes
of the scanner — placeholder entries (id=0) are ignored entirely.

The scanner consults this layer before each per-date provider fetch to
decide:
  - Skip the date if the admin is out (per `group.admin_required` config)
  - Otherwise use the number of available registered members as
    `min_players` for that date's provider queries
  - Annotate matches with the roster for the digest

See SPEC.md (when updated for availability).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from golfbot.config import Config


# --------------------------------------------------------------------------- #
# Model                                                                       #
# --------------------------------------------------------------------------- #


_DEFAULT_OUT_WEEKDAYS: frozenset[int] = frozenset({5, 6})   # Sat, Sun


@dataclass
class AvailabilityRecord:
    """A single member's availability.

    Two layers of state — weekly pattern plus optional per-date overrides:
      • `out_weekdays`: set of weekday ints (Mon=0..Sun=6) this member is
        OUT every week. Default is `{Sat, Sun}` for new members.
      • `out_dates`: per-date OUT overrides (member is normally available
        that weekday but is out for a specific date).
      • `in_dates`: per-date IN overrides (member is normally out on that
        weekday but is available for a specific date).
    """
    out_weekdays: set[int] = field(default_factory=lambda: set(_DEFAULT_OUT_WEEKDAYS))
    out_dates: list[date] = field(default_factory=list)
    in_dates: list[date] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_weekdays": sorted(self.out_weekdays),
            "out_dates": [d.isoformat() for d in sorted(self.out_dates)],
            "in_dates": [d.isoformat() for d in sorted(self.in_dates)],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AvailabilityRecord:
        # out_weekdays absent → default to {Sat, Sun} so existing records
        # silently inherit the new weekly-pattern semantics on first load.
        wd_raw = d.get("out_weekdays")
        if wd_raw is None:
            out_weekdays = set(_DEFAULT_OUT_WEEKDAYS)
        else:
            out_weekdays = {int(x) for x in wd_raw if isinstance(x, (int, str))}
        return cls(
            out_weekdays=out_weekdays,
            out_dates=[date.fromisoformat(s) for s in d.get("out_dates", [])],
            in_dates=[date.fromisoformat(s) for s in d.get("in_dates", [])],
        )


def load_availability(state: dict[str, Any]) -> dict[str, AvailabilityRecord]:
    """Read the availability section from state, dropping past dates."""
    raw = state.get("availability") or {}
    today = date.today()
    out: dict[str, AvailabilityRecord] = {}
    for name, entry in raw.items():
        rec = AvailabilityRecord.from_dict(entry)
        # Prune dates that have already passed.
        rec.out_dates = [d for d in rec.out_dates if d >= today]
        rec.in_dates = [d for d in rec.in_dates if d >= today]
        out[name] = rec
    return out


def save_availability(
    state: dict[str, Any],
    availability: dict[str, AvailabilityRecord],
) -> None:
    """Write availability back into state. Members with empty out_dates
    are kept (so the structure persists once they've used the feature)."""
    state["availability"] = {name: rec.to_dict() for name, rec in availability.items()}


# --------------------------------------------------------------------------- #
# Queries                                                                     #
# --------------------------------------------------------------------------- #


def registered_members(cfg: Config) -> list[str]:
    """Members with a real (non-zero) telegram_user_id."""
    return [m.name for m in cfg.group.members if m.telegram_user_id != 0]


def is_available(
    member_name: str,
    on_date: date,
    availability: dict[str, AvailabilityRecord],
) -> bool:
    """Member's effective availability on a date.

    Resolution: explicit `in_dates` override beats explicit `out_dates`
    override beats the weekly `out_weekdays` pattern. A member with no
    record at all defaults to the global default pattern (`{Sat, Sun}` out).
    """
    rec = availability.get(member_name)
    if rec is None:
        return on_date.weekday() not in _DEFAULT_OUT_WEEKDAYS
    if on_date in rec.in_dates:
        return True
    if on_date in rec.out_dates:
        return False
    return on_date.weekday() not in rec.out_weekdays


def available_members_on(
    on_date: date,
    cfg: Config,
    availability: dict[str, AvailabilityRecord],
) -> list[str]:
    """Registered members available on a given date."""
    return [
        name for name in registered_members(cfg)
        if is_available(name, on_date, availability)
    ]


def out_members_on(
    on_date: date,
    cfg: Config,
    availability: dict[str, AvailabilityRecord],
) -> list[str]:
    return [
        name for name in registered_members(cfg)
        if not is_available(name, on_date, availability)
    ]


def admin_available_on(
    on_date: date,
    cfg: Config,
    availability: dict[str, AvailabilityRecord],
) -> bool:
    return is_available(cfg.group.admin, on_date, availability)


def date_should_be_scanned(
    on_date: date,
    cfg: Config,
    availability: dict[str, AvailabilityRecord],
) -> bool:
    """The gate: should we even hit providers for this date?

    Rules:
      - If `group.admin_required` is True, skip dates when admin is out
        (admin-centric mode — preserved for users who want it).
      - Otherwise, scan any date where at least one registered member is
        available. Days when everyone is out get skipped.
      - If there are no registered members at all (fresh install), scan
        every date — no constraints to apply.
    """
    if cfg.group.admin_required and not admin_available_on(on_date, cfg, availability):
        return False
    if registered_members(cfg) and not available_members_on(on_date, cfg, availability):
        return False
    return True


def players_to_search_for(
    on_date: date,
    cfg: Config,
    availability: dict[str, AvailabilityRecord],
) -> int:
    """How many seats to ask the provider for on this date.

    Returns the count of registered available members (minimum 1). Provider
    will only return slots with at least that many open seats.
    """
    return max(1, len(available_members_on(on_date, cfg, availability)))


# --------------------------------------------------------------------------- #
# Mutations                                                                   #
# --------------------------------------------------------------------------- #


def set_out(
    name: str,
    dates: list[date],
    availability: dict[str, AvailabilityRecord],
) -> None:
    """Mark specific dates OUT.

    Adds the dates to `out_dates` and removes them from `in_dates` so a
    prior IN override doesn't fight the new OUT override.
    """
    rec = availability.setdefault(name, AvailabilityRecord())
    out_set = set(rec.out_dates)
    in_set = set(rec.in_dates)
    for d in dates:
        out_set.add(d)
        in_set.discard(d)
    rec.out_dates = sorted(out_set)
    rec.in_dates = sorted(in_set)


def set_in(
    name: str,
    dates: list[date],
    availability: dict[str, AvailabilityRecord],
) -> None:
    """Mark specific dates IN.

    Adds the dates to `in_dates` (overrides a weekly-pattern OUT) AND
    removes them from `out_dates` (clears any prior OUT override).
    """
    rec = availability.setdefault(name, AvailabilityRecord())
    out_set = set(rec.out_dates)
    in_set = set(rec.in_dates)
    for d in dates:
        out_set.discard(d)
        in_set.add(d)
    rec.out_dates = sorted(out_set)
    rec.in_dates = sorted(in_set)


def toggle_weekday(
    name: str,
    weekday: int,
    availability: dict[str, AvailabilityRecord],
) -> bool:
    """Toggle the member's weekly OUT pattern for a weekday (Mon=0..Sun=6).
    Returns True if the member is now OUT for that weekday, False if IN.
    """
    rec = availability.setdefault(name, AvailabilityRecord())
    if weekday in rec.out_weekdays:
        rec.out_weekdays.discard(weekday)
        return False
    rec.out_weekdays.add(weekday)
    return True


# --------------------------------------------------------------------------- #
# Date parsing                                                                #
# --------------------------------------------------------------------------- #


_DAY_NAMES: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2,
    "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def parse_date_arg(s: str, today: date) -> date | None:
    """Parse a user-supplied date argument. Supports:

    - 'today', 'tomorrow'
    - 'mon', 'tue', ..., 'sun' (next occurrence, today if it matches)
    - ISO 'YYYY-MM-DD'
    - 'M/D' (current year, rolls to next year if past)
    """
    s = s.strip().lower()
    if not s:
        return None
    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)

    if s in _DAY_NAMES:
        target = _DAY_NAMES[s]
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)

    # ISO
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass

    # M/D
    if "/" in s:
        try:
            m_str, d_str = s.split("/", 1)
            m, d = int(m_str), int(d_str)
            candidate = date(today.year, m, d)
            if candidate < today:
                candidate = date(today.year + 1, m, d)
            return candidate
        except (ValueError, IndexError):
            pass

    return None
