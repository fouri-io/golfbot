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


@dataclass
class AvailabilityRecord:
    """A single member's availability — list of dates they're out."""
    out_dates: list[date] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"out_dates": [d.isoformat() for d in sorted(self.out_dates)]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AvailabilityRecord:
        return cls(out_dates=[date.fromisoformat(s) for s in d.get("out_dates", [])])


def load_availability(state: dict[str, Any]) -> dict[str, AvailabilityRecord]:
    """Read the availability section from state, dropping past dates."""
    raw = state.get("availability") or {}
    today = date.today()
    out: dict[str, AvailabilityRecord] = {}
    for name, entry in raw.items():
        rec = AvailabilityRecord.from_dict(entry)
        # Prune dates that have already passed.
        rec.out_dates = [d for d in rec.out_dates if d >= today]
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
    rec = availability.get(member_name)
    if rec is None:
        return True
    return on_date not in rec.out_dates


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

    Today's rule: skip if admin is out AND admin_required is set.
    Future: extend per non-admin members' rules if/when configurable.
    """
    if cfg.group.admin_required and not admin_available_on(on_date, cfg, availability):
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
    rec = availability.setdefault(name, AvailabilityRecord())
    existing = set(rec.out_dates)
    for d in dates:
        existing.add(d)
    rec.out_dates = sorted(existing)


def set_in(
    name: str,
    dates: list[date],
    availability: dict[str, AvailabilityRecord],
) -> None:
    rec = availability.get(name)
    if rec is None:
        return
    drop = set(dates)
    rec.out_dates = [d for d in rec.out_dates if d not in drop]


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
