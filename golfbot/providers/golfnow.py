"""GolfNow provider.

Hits the public POST endpoint
`https://www.golfnow.com/api/tee-times/tee-time-search-results` once per
(facility, date) pair. The endpoint is public — no auth, just standard
browser headers — but is undocumented, so we treat it as best-effort and
log+skip on errors rather than failing the whole poll.

Response quirk: per-slot `time.date` carries a misleading `+00:00` offset
even though the value is local Central time. We parse from
`time.formatted` + `time.formattedTimeMeridian` to sidestep that entirely.

Quirk #2: the endpoint does not return per-slot player availability —
when we ask for `players: 3`, every returned slot accepts ≥ 3. Our
RawSlot.players_available reflects this ">=" semantic.
"""
from __future__ import annotations

import logging
import time as _time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

import httpx

from golfbot.config import Course
from golfbot.providers.base import RawSlot

log = logging.getLogger(__name__)

_BASE_URL = "https://www.golfnow.com"
_ENDPOINT = "/api/tee-times/tee-time-search-results"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) "
        "Gecko/20100101 Firefox/150.0"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": _BASE_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


@dataclass
class GolfNowProvider:
    name: str = "golfnow"
    timeout_seconds: float = 30.0

    async def fetch_slots(
        self,
        courses: list[Course],
        target_date: date,
        min_players: int,
    ) -> list[RawSlot]:
        owned = [c for c in courses if c.provider == self.name]
        if not owned:
            return []

        out: list[RawSlot] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=_HEADERS) as client:
            for course in owned:
                fac_id = _coerce_int(course.provider_id)
                if fac_id is None:
                    log.warning(
                        "course %r has non-numeric provider_id %r; skipping",
                        course.key, course.provider_id,
                    )
                    continue
                log.info("golfnow fetch: %s (id=%d) for %s ...", course.key, fac_id, target_date)
                started = _time_mod.monotonic()
                try:
                    raw = await self._fetch_one(client, fac_id, target_date, min_players)
                except httpx.HTTPError as e:
                    log.warning(
                        "GolfNow fetch failed for %s on %s: %s",
                        course.key, target_date, e,
                    )
                    continue
                parsed = parse_response(raw, course.key, target_date, min_players)
                elapsed = _time_mod.monotonic() - started
                log.info(
                    "golfnow fetch: %s for %s -> %d slot(s) in %.2fs",
                    course.key, target_date, len(parsed), elapsed,
                )
                out.extend(parsed)
        return out

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        facility_id: int,
        target_date: date,
        min_players: int,
    ) -> dict:
        body = build_request_body(facility_id, target_date, min_players)
        resp = await client.post(
            _BASE_URL + _ENDPOINT,
            json=body,
            headers={"Referer": f"{_BASE_URL}/tee-times/facility/{facility_id}/search"},
        )
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
# Pure helpers — exported for testing                                         #
# --------------------------------------------------------------------------- #


def build_request_body(facility_id: int, target_date: date, min_players: int) -> dict:
    """Construct the POST body.

    `timeMin` / `timeMax` are encoded as **half-hours past midnight**
    (not hours). We send 0..48 to capture the entire day; the time-window
    filter happens client-side in `pipeline.filter_and_grade`.
    """
    return {
        "useWidgetNextAvailableDays": None,
        "nextAvailableTeeTime": None,
        "tags": None,
        "address": None,
        "pageSize": 1000,
        "teeTimeCount": 1000,
        "pageNumber": 0,
        "date": target_date.strftime("%b %d %Y"),     # e.g. "May 21 2026"
        "sortBy": "Date",
        "sortByRollup": "Date.MinDate",
        "sortDirection": 0,
        "hotDealsOnly": False,
        "golfPassPerksOnly": False,
        "bestDealsOnly": False,
        "promotedCampaignsOnly": False,
        "priceMin": 0,
        "priceMax": 10000,
        "players": min_players,
        "timePeriod": "Any",
        "timeMin": 0,
        "timeMax": 48,   # half-hours past midnight; 48 = full day
        "holes": "Eighteen",
        "facilityType": "GolfCourse",
        "latitude": 30.26715,
        "longitude": -97.74306,
        "radius": 35,
        "maxAllowedRadius": None,
        "facilityId": facility_id,
        "facilityIds": [],
        "marketId": None,
        "marketName": None,
        "searchType": "Facility",
        "view": "Grouping",
        "nonGPS": None,
        "excludeFeaturedFacilities": True,
        "excludePrivateFacilities": False,
        "rateTagCodes": None,
        "customerToken": None,
        "rateType": "all",
        "currentClientDate": _now_iso(),
        "daysToSearch": None,
        "facilityTagsExclusive": None,
        "isSimulator": None,
        "isHotDealsZoneMoreDeals": None,
        "facilityGroupId": None,
        "trackmanOnly": False,
    }


def parse_response(
    payload: dict,
    course_key: str,
    target_date: date,
    min_players: int,
) -> list[RawSlot]:
    """Walk a GolfNow tee-time-search-results response into RawSlots."""
    tee_times = payload.get("ttResults", {}).get("teeTimes", []) or []
    out: list[RawSlot] = []
    for tt in tee_times:
        slot = _parse_one(tt, course_key, target_date, min_players)
        if slot is not None:
            out.append(slot)
    return out


def _parse_one(
    tt: dict[str, Any],
    course_key: str,
    target_date: date,
    min_players: int,
) -> RawSlot | None:
    """Convert one teeTimes[] entry to a RawSlot, or None to skip."""
    rates = tt.get("teeTimeRates") or []
    rate = next(
        (r for r in rates if r.get("holeCount") == 18 and r.get("isEighteen")),
        None,
    )
    if rate is None:
        return None

    tee_time = _parse_clock(tt.get("time") or {})
    if tee_time is None:
        return None

    detail_url = rate.get("detailUrl") or tt.get("detailUrl") or ""
    if detail_url.startswith("/"):
        detail_url = _BASE_URL + detail_url
    if not detail_url:
        return None

    # greensFees lives under singlePlayerPrice on real responses; some
    # older shapes (and our trimmed fixture) put it on the rate root.
    spp = rate.get("singlePlayerPrice") or {}
    gf = spp.get("greensFees") or rate.get("greensFees") or {}
    price = _coerce_float(gf.get("value"))
    rate_id = rate.get("teeTimeRateId")

    return RawSlot(
        course_key=course_key,
        tee_date=target_date,
        tee_time=tee_time,
        players_available=min_players,   # ">=" semantics for GolfNow
        holes=18,
        booking_url=detail_url,
        price_usd=price,
        provider="golfnow",
        extra={"teeTimeRateId": rate_id} if rate_id else {},
    )


def _parse_clock(time_field: dict[str, Any]) -> time | None:
    """Extract from {"formatted":"7:10","formattedTimeMeridian":"AM"}."""
    formatted = time_field.get("formatted")
    meridian = time_field.get("formattedTimeMeridian")
    if not formatted or not meridian:
        return None
    try:
        h_str, m_str = formatted.split(":")
        hour, minute = int(h_str), int(m_str)
    except (ValueError, AttributeError):
        return None
    if meridian == "PM" and hour != 12:
        hour += 12
    elif meridian == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


def _coerce_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    # GolfNow expects the trailing Z; isoformat() gives +00:00 — swap it.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
